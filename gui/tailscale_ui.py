#!/usr/bin/env python3
"""Private HTTPS-origin gateway to an already running loopback DAW; no engine ownership."""
import argparse
import http.client
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_UPLOAD = 128 * 1024 * 1024  # Same portable-project ceiling as the DAW bridge.


def validate_origin(origin):
    # Only a root HTTPS origin in Tailscale's certificate namespace, optional port.
    if not re.fullmatch(r'https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.'
                        r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.ts\.net(?::[1-9][0-9]{0,4})?', origin):
        raise ValueError('Expected https://machine.tailnet.ts.net with an optional port, no path.')
    if ':' in origin.removeprefix('https://') and int(origin.rsplit(':', 1)[1]) > 65535:
        raise ValueError('HTTPS port must be 1–65535.')
    return origin.removesuffix(':443')


class Gateway(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, upstream_port, origin, port=0):
        self.external_origin = validate_origin(origin)
        if not 1 <= upstream_port <= 65535:
            raise ValueError('Upstream port must be 1–65535.')
        self.upstream_port = upstream_port
        super().__init__(('127.0.0.1', port), Relay)
        if self.server_port == upstream_port:
            self.server_close()
            raise ValueError('Gateway and DAW ports must differ.')


class Relay(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def fail(self, status, message):
        body = json.dumps({'error': message}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def relay(self):
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        if hosts != [self.server.external_origin.removeprefix('https://')]:
            self.fail(403, 'Invalid gateway host.')
            return
        if origins and origins != [self.server.external_origin]:
            self.fail(403, 'Cross-origin requests are not allowed.')
            return
        if not self.path.startswith('/') or self.path.startswith('//'):
            self.fail(400, 'Expected a relative request path.')
            return
        lengths = self.headers.get_all('Content-Length', [])
        if (self.headers.get_all('Transfer-Encoding') or len(lengths) > 1
                or (lengths and not re.fullmatch(r'0|[1-9][0-9]{0,9}', lengths[0]))
                or (self.command == 'POST' and not lengths)):
            self.fail(400, 'Expected one Content-Length and no Transfer-Encoding.')
            return
        length = int(lengths[0]) if lengths else 0
        if length > MAX_UPLOAD:
            self.fail(413, 'Request exceeds the portable-project upload limit.')
            return
        if self.command == 'GET' and length:
            self.fail(400, 'GET requests must not contain a body.')
            return
        # A fixed upstream, no forwarded-host trust and no token minting. The DAW
        # remains responsible for API token authentication and project validation.
        connection = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=400)
        started = False
        try:
            connection.putrequest(self.command, self.path, skip_accept_encoding=True)
            for name in ('Content-Type', 'Accept', 'X-DAW-Token', 'X-DAW-Metadata'):
                values = self.headers.get_all(name, [])
                if len(values) > 1:
                    self.fail(400, 'Duplicate request control header.')
                    return
                if values:
                    connection.putheader(name, values[0])
            if origins:
                connection.putheader('Origin', f'http://127.0.0.1:{self.server.upstream_port}')
            if lengths:
                connection.putheader('Content-Length', str(length))
            connection.endheaders()
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    raise OSError('Truncated request')
                connection.send(chunk)
                remaining -= len(chunk)
            response = connection.getresponse()
            self.send_response(response.status)
            # Preserve CSP/nonce, downloads and content metadata; never relay hop headers.
            for name in ('Content-Type', 'Content-Length', 'Cache-Control', 'X-Content-Type-Options',
                         'Referrer-Policy', 'Content-Security-Policy', 'Content-Disposition', 'X-Clipped-Frames'):
                value = response.getheader(name)
                if value is not None:
                    self.send_header(name, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            started = True
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()  # Deliver Studio NDJSON progress without waiting for EOF.
        except (OSError, http.client.HTTPException):
            if not started:
                self.fail(502, 'Could not reach the running DAW. Check its loopback port.')
            self.close_connection = True
        finally:
            connection.close()

    do_GET = relay
    do_POST = relay


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-port', type=int, required=True, help='existing DAW loopback port')
    parser.add_argument('--origin', required=True, help='exact private Tailscale HTTPS origin')
    parser.add_argument('--port', type=int, default=8790, help='gateway loopback port (default 8790)')
    args = parser.parse_args()
    try:
        server = Gateway(args.upstream_port, args.origin, args.port)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    with server:
        print(f'UI gateway: http://127.0.0.1:{server.server_port} → {server.external_origin}', flush=True)
        print('Existing DAW session retained. Ctrl+C stops only this gateway.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
