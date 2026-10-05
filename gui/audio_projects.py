"""Bounded, private WAV assets and portable ZIP projects for one GUI server."""
import copy
import io
import json
from pathlib import PurePosixPath
import re
import secrets
import stat
import struct
import zipfile

MAX_AUDIO_BODY = 32 * 1024 * 1024
MAX_PROJECT_BODY = 128 * 1024 * 1024
MAX_DECODED_BYTES = 128 * 1024 * 1024
MAX_ASSETS = 128
MAX_METADATA = 2048
MAX_SESSION_BYTES = 1024 * 1024
MAX_FRAME = 9007199254740991
LIMITS = {'audio_bytes': MAX_AUDIO_BODY, 'project_bytes': MAX_PROJECT_BODY,
          'metadata_bytes': MAX_METADATA, 'decoded_bytes': MAX_DECODED_BYTES,
          'assets': MAX_ASSETS}


def strict_json(raw):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field.')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON number.')
    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=constant)


def metadata(raw, keys):
    if raw is None or len(raw.encode('utf-8')) > MAX_METADATA:
        raise ValueError('X-DAW-Metadata must be bounded JSON (at most 2 KiB).')
    result = strict_json(raw)
    if not isinstance(result, dict) or set(result) != set(keys):
        raise ValueError('Expected metadata fields: ' + ', '.join(keys) + '.')
    revision = result['expected_revision']
    if (not isinstance(revision, str) or not re.fullmatch(r'0|[1-9][0-9]{0,19}', revision)
            or int(revision) > 0xffffffffffffffff):
        raise ValueError('expected_revision must be a canonical unsigned 64-bit decimal string.')
    return result


def inspect_wav(body, rate):
    if not 12 <= len(body) <= MAX_AUDIO_BODY or body[:4] != b'RIFF' or body[8:12] != b'WAVE':
        raise ValueError('Expected a bounded RIFF WAV file.')
    if struct.unpack_from('<I', body, 4)[0] + 8 != len(body):
        raise ValueError('WAV size is truncated or inconsistent.')
    offset, fmt, data_size = 12, None, None
    while offset < len(body):
        if offset + 8 > len(body):
            raise ValueError('Truncated WAV chunk header.')
        tag, size = struct.unpack_from('<4sI', body, offset)
        offset += 8
        end = offset + size
        if end > len(body):
            raise ValueError('Truncated WAV chunk.')
        if tag == b'fmt ':
            if fmt is not None or size < 16:
                raise ValueError('Invalid or duplicate WAV format chunk.')
            encoding, channels, sample_rate, byte_rate, block_align, bits = struct.unpack_from('<HHIIHH', body, offset)
            if encoding == 0xfffe:
                pcm_guid = bytes.fromhex('0100000000001000800000aa00389b71')
                if (size < 40 or struct.unpack_from('<H', body, offset + 16)[0] < 22
                        or body[offset + 24:offset + 40] != pcm_guid
                        or struct.unpack_from('<H', body, offset + 18)[0] != bits):
                    raise ValueError('Only integer PCM WAV is supported.')
            elif encoding != 1:
                raise ValueError('Only integer PCM WAV is supported; float/compressed audio is unavailable.')
            if channels not in (1, 2) or bits not in (16, 24, 32):
                raise ValueError('WAV must be mono/stereo integer PCM16, PCM24 or PCM32.')
            if sample_rate != rate:
                raise ValueError('WAV sample rate must match the session; resampling is unavailable.')
            if block_align != channels * (bits // 8) or byte_rate != sample_rate * block_align:
                raise ValueError('WAV frame alignment or byte rate is inconsistent.')
            fmt = (channels, sample_rate, bits, block_align)
        elif tag == b'data':
            if data_size is not None:
                raise ValueError('Duplicate WAV data chunk.')
            data_size = size
        # Some PCM writers omit padding after the final odd-byte data chunk.
        # Complete frame alignment below remains authoritative.
        offset = end if end == len(body) else end + (size % 2)
    if fmt is None or data_size is None or not data_size or data_size % fmt[3]:
        raise ValueError('WAV must contain complete, nonempty audio frames.')
    frames = data_size // fmt[3]
    if frames * 16 > MAX_DECODED_BYTES:
        raise ValueError('Decoded WAV exceeds 128 MiB.')
    return {'frames': frames, 'sample_rate': fmt[1], 'channels': fmt[0], 'bits_per_sample': fmt[2]}


def validate_zip_directory(body):
    # Check the EOCD counts before ZipFile allocates one Python object per entry.
    # These small bundles need neither ZIP64 nor split/multidisk archives.
    if not 22 <= len(body) <= MAX_PROJECT_BODY:
        raise ValueError('Encoded project ZIP exceeds its size limit or is truncated.')
    offset = body.rfind(b'PK\x05\x06', max(0, len(body) - 65557))
    if offset < 0 or offset + 22 > len(body):
        raise ValueError('Project ZIP directory is missing.')
    disk, directory_disk, disk_entries, entries, size, start, comment = struct.unpack_from('<HHHHIIH', body, offset + 4)
    if (disk or directory_disk or disk_entries != entries or not 1 <= entries <= MAX_ASSETS + 1
            or size > 256 * 1024 or start + size != offset or offset + 22 + comment != len(body)):
        raise ValueError('Project ZIP directory is unsafe, oversized or unsupported.')


def audio_paths(session):
    result = set()
    if not isinstance(session, dict) or not isinstance(session.get('tracks'), list):
        return result
    for track in session['tracks']:
        if not isinstance(track, dict) or not isinstance(track.get('clips'), list):
            continue
        for clip in track['clips']:
            if isinstance(clip, dict) and clip.get('kind') == 'audio':
                path = clip.get('source_path')
                if not isinstance(path, str):
                    raise ValueError('Audio clips require a registered asset path.')
                result.add(path)
    return result


def project_session(session):
    if not isinstance(session, dict) or session.get('schema_version') not in (1, 2, 3, 4, 9, 10, 11, 12):
        raise ValueError('Audio projects support built-in sessions v1/v2/v3/v4/v9/v10/v11 and Pd instrument arrangements v12.')
    effect_kinds = ('gain', 'lowpass', 'delay') if session['schema_version'] >= 11 else ('gain',)
    device_kinds = ('sine', 'audio', 'synth', 'drumkit') + (('pd_instrument',) if session['schema_version'] == 12 else ())
    if not isinstance(session.get('tracks'), list):
        raise ValueError('Expected session tracks.')
    for track in session['tracks']:
        if (not isinstance(track, dict) or not isinstance(track.get('effects', []), list)
                or not isinstance(track.get('device'), dict)
                or track['device'].get('kind') not in device_kinds
                or any(not isinstance(effect, dict) or effect.get('kind') not in effect_kinds
                       for effect in track.get('effects', []))):
            raise ValueError('Audio projects support built-in devices and the effects allowed by their saved format.')


class AudioProjects:
    def __init__(self, engine, root, validate_shape):
        self.engine, self.root, self.validate_shape = engine, root, validate_shape
        self.assets = {}
        (root / 'assets').mkdir()

    def registered(self, session):
        paths = audio_paths(session)
        if not paths.issubset(self.assets):
            raise ValueError('Audio paths must refer only to registered assets imported into this server.')
        self.budget(paths)

    def budget(self, paths, extra=None):
        infos = [self.assets[path] for path in paths]
        if extra is not None:
            infos.append(extra)
        if len(infos) > MAX_ASSETS or sum(info['frames'] * 16 for info in infos) > MAX_DECODED_BYTES:
            raise ValueError('Project exceeds 128 shared assets or 128 MiB decoded audio.')

    def snapshot(self, revision):
        snapshot = self.engine.call('session.inspect')
        if snapshot['revision'] != revision:
            raise ValueError('Stale session revision; reload the applied session before importing.')
        return snapshot['session']

    def write_asset(self, body, info, *, staged=False):
        # Retain owned assets for undo, but bound retained storage across replacements.
        if not staged and (len(self.assets) >= MAX_ASSETS
                or sum(asset['encoded_bytes'] for asset in self.assets.values()) + len(body) > MAX_PROJECT_BODY):
            raise ValueError('Owned asset storage is full; reopen a saved project to reclaim unused assets.')
        path = 'assets/' + secrets.token_hex(16) + '.wav'
        stream = (self.root / path).open('xb')
        try:
            with stream:
                stream.write(body)
        except BaseException:
            (self.root / path).unlink(missing_ok=True)
            raise
        self.assets[path] = {**info, 'encoded_bytes': len(body)}
        return path

    def rollback(self, paths):
        for path in paths:
            self.assets.pop(path, None)
            (self.root / path).unlink(missing_ok=True)

    def replace(self, session, revision):
        self.validate_shape(session)
        self.registered(session)
        applied = self.engine.call('session.replace', {'session': session, 'expected_revision': revision})
        # Checked replacement increments this exact revision once. No fallible
        # follow-up inspection may roll back assets already committed by Rust.
        return {'session': applied, 'revision': str(int(revision) + 1)}

    def import_audio(self, meta, body):
        session = copy.deepcopy(self.snapshot(meta['expected_revision']))
        project_session(session)
        self.validate_shape(session)
        self.registered(session)
        for key in ('track_id', 'clip_id'):
            if not isinstance(meta[key], str) or not 0 < len(meta[key].encode('utf-8')) <= 128:
                raise ValueError(key + ' must contain 1..128 UTF-8 bytes.')
        start = meta['start_frame']
        if type(start) is not int or not 0 <= start <= MAX_FRAME:
            raise ValueError('start_frame must be an integer within the timeline limit.')
        if any(track['id'] == meta['track_id'] for track in session['tracks']):
            raise ValueError('Import requires a new unique track_id.')
        info = inspect_wav(body, session['sample_rate'])
        if start + info['frames'] > MAX_FRAME:
            raise ValueError('Imported clip extends beyond the timeline limit.')
        self.budget(audio_paths(session), info)
        if session['schema_version'] == 1:
            session['schema_version'] = 9
            session['tempo_milli_bpm'] = 120000
            for track in session['tracks']:
                track.update(mode='continuous', clips=[], effects=[])
        path = self.write_asset(body, info)
        try:
            track = {'id': meta['track_id'], 'mode': 'sequenced', 'device': {'kind': 'audio', 'gain': 1},
                     'clips': [{'kind': 'audio', 'id': meta['clip_id'], 'start_frame': start,
                                'length_frames': info['frames'], 'source_path': path,
                                'source_offset_frames': 0, 'gain': 1}]}
            if session['schema_version'] != 2:
                track['effects'] = []
            session['tracks'].append(track)
            result = self.replace(session, meta['expected_revision'])
        except BaseException as error:
            if not getattr(error, 'commit_outcome_unknown', False):
                self.rollback([path])
            raise
        result['asset'] = {'source_path': path, **info}
        return result

    def export_project(self):
        session = self.engine.call('session.get')
        project_session(session)
        self.validate_shape(session)
        self.registered(session)
        wire = json.dumps(session, allow_nan=False).encode()
        if len(wire) > MAX_SESSION_BYTES:
            raise ValueError('Project session JSON exceeds 1 MiB.')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('session.json', wire)
            for path in sorted(audio_paths(session)):
                archive.writestr(path, (self.root / path).read_bytes())
        if output.tell() > MAX_PROJECT_BODY:
            raise ValueError('Encoded project exceeds 128 MiB.')
        return output.getvalue()

    def import_project(self, meta, body):
        self.snapshot(meta['expected_revision'])
        validate_zip_directory(body)
        staged = []
        old_assets = set(self.assets)
        committed = False
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                entries = archive.infolist()
                if not 1 <= len(entries) <= MAX_ASSETS + 1:
                    raise ValueError('Project contains too many entries.')
                names, expanded = {}, 0
                for entry in entries:
                    name = entry.filename
                    mode = entry.external_attr >> 16
                    if (name in names or name.casefold() in {key.casefold() for key in names}
                            or entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                            or entry.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                            or (name != 'session.json' and not re.fullmatch(r'assets/[A-Za-z0-9_-]{1,128}\.wav', name))
                            or str(PurePosixPath(name)) != name):
                        raise ValueError('Project contains an unsafe, duplicate or unsupported ZIP entry.')
                    limit = MAX_SESSION_BYTES if name == 'session.json' else MAX_AUDIO_BODY
                    if entry.file_size > limit or entry.compress_size > MAX_PROJECT_BODY:
                        raise ValueError('Project ZIP entry exceeds its size limit.')
                    expanded += entry.file_size
                    if expanded > MAX_PROJECT_BODY:
                        raise ValueError('Project ZIP expansion exceeds 128 MiB.')
                    names[name] = entry
                if 'session.json' not in names:
                    raise ValueError('Project requires session.json.')
                session = strict_json(archive.read(names['session.json']))
                project_session(session)
                self.validate_shape(session)
                paths = audio_paths(session)
                if set(names) != paths | {'session.json'}:
                    raise ValueError('ZIP assets must exactly match the session audio references.')
                remap, total_decoded = {}, 0
                for path in sorted(paths):
                    with archive.open(names[path]) as source:
                        wav = source.read(MAX_AUDIO_BODY + 1)
                    info = inspect_wav(wav, session['sample_rate'])
                    total_decoded += info['frames'] * 16
                    if total_decoded > MAX_DECODED_BYTES:
                        raise ValueError('Project decoded audio exceeds 128 MiB.')
                    owned = self.write_asset(wav, info, staged=True)
                    staged.append(owned)
                    remap[path] = owned
                for track in session['tracks']:
                    for clip in track.get('clips', []):
                        if clip.get('kind') == 'audio':
                            clip['source_path'] = remap[clip['source_path']]
                result = self.replace(session, meta['expected_revision'])
                committed = True
                # Opening a project resets frontend history: reclaim every old
                # asset only after the new bundle has committed successfully.
                self.rollback(old_assets)
                return result
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
            self.rollback(staged)
            raise ValueError('Invalid or unsupported project ZIP.') from error
        except BaseException as error:
            if not committed and not getattr(error, 'commit_outcome_unknown', False):
                self.rollback(staged)
            raise
