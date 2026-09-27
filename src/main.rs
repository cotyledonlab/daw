use daw::control::{Controller, MAX_MESSAGE_BYTES, Response};
use std::io::{self, BufRead, Write};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args == ["--help"] || args == ["-h"] {
        println!(
            "daw serve\nRead one versioned JSON request per line on stdin; write one response per line to stdout.\nSee docs/PROTOCOL.md and examples/demo.py. File paths are relative to the working directory."
        );
        return;
    }
    if args != ["serve"] {
        eprintln!("usage: daw serve | --help");
        std::process::exit(2);
    }
    if let Err(error) = serve() {
        if error.kind() != io::ErrorKind::BrokenPipe {
            eprintln!("daw: {error}");
        }
        std::process::exit(1);
    }
}

fn serve() -> io::Result<()> {
    let stdin = io::stdin();
    let mut input = stdin.lock();
    let stdout = io::stdout();
    let mut output = stdout.lock();
    let mut controller = Controller::default();
    loop {
        // Bound retained memory even for an adversarial unterminated input line.
        let mut bytes = Vec::new();
        let mut oversized = false;
        loop {
            let buffer = input.fill_buf()?;
            if buffer.is_empty() {
                break;
            }
            let end = buffer.iter().position(|&b| b == b'\n');
            let count = end.map_or(buffer.len(), |i| i + 1);
            if !oversized {
                if bytes.len() + count > MAX_MESSAGE_BYTES + 1 {
                    oversized = true;
                    bytes.clear();
                } else {
                    bytes.extend_from_slice(&buffer[..count]);
                }
            }
            input.consume(count);
            if end.is_some() {
                break;
            }
        }
        if bytes.is_empty() && !oversized {
            return Ok(());
        }
        if bytes.last() == Some(&b'\n') {
            bytes.pop();
        }
        let response = if oversized {
            Response::failure(None, "request_too_large", "request exceeds 1 MiB")
        } else {
            match std::str::from_utf8(&bytes) {
                Ok(line) => controller.handle_line(line),
                Err(error) => Response::failure(None, "invalid_request", error),
            }
        };
        serde_json::to_writer(&mut output, &response)?;
        writeln!(output)?;
        output.flush()?;
    }
}
