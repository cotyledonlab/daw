use daw::control::{Controller, MAX_MESSAGE_BYTES, Response};
use std::io::{self, BufRead, Write};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().is_some_and(|a| a == "devices" || a == "play") {
        if let Err(error) = native_command(&args) {
            eprintln!("daw: {error}");
            std::process::exit(1);
        }
        return;
    }
    if args == ["--help"] || args == ["-h"] {
        println!(
            "daw serve\ndaw devices\ndaw play SESSION.json SECONDS [VOLUME]\nNative devices/play require macOS and --features native-audio. Volume defaults to 0.25.\nRead one versioned JSON request per line on stdin in serve mode.\nSee docs/PROTOCOL.md and examples/demo.py. Paths are relative to the working directory."
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

#[cfg(all(feature = "native-audio", target_os = "macos"))]
fn native_command(args: &[String]) -> Result<(), String> {
    use std::io::Read;
    let result = match args[0].as_str() {
        "devices" if args.len() == 1 => daw::audio::devices()?,
        "play" if args.len() == 3 || args.len() == 4 => {
            let seconds = args[2].parse::<f64>().map_err(|_| "invalid seconds")?;
            let volume = args
                .get(3)
                .map(|v| v.parse::<f64>())
                .transpose()
                .map_err(|_| "invalid volume")?
                .unwrap_or(0.25);
            let mut bytes = Vec::new();
            let file = std::fs::File::open(&args[1]).map_err(|e| e.to_string())?;
            if !file.metadata().map_err(|e| e.to_string())?.is_file() {
                return Err("session must be a regular JSON file".into());
            }
            file.take(MAX_MESSAGE_BYTES as u64 + 1)
                .read_to_end(&mut bytes)
                .map_err(|e| e.to_string())?;
            if bytes.len() > MAX_MESSAGE_BYTES {
                return Err("session exceeds 1 MiB".into());
            }
            let session = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
            daw::audio::play(&session, seconds, volume)?
        }
        _ => return Err("usage: daw devices | daw play SESSION.json SECONDS [VOLUME]".into()),
    };
    println!("{result}");
    Ok(())
}

#[cfg(not(all(feature = "native-audio", target_os = "macos")))]
fn native_command(_: &[String]) -> Result<(), String> {
    Err("native playback requires macOS and a build with --features native-audio".into())
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
