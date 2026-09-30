//! Bounded offline Audio Unit child worker. Never call this from an audio callback.
use crate::{hosting::Processed, session::Effect};

#[cfg(not(all(feature = "au-offline", target_os = "macos")))]
pub fn process(_: &Effect, _: &[[f64; 2]]) -> Result<Processed, String> {
    Err("offline Audio Unit hosting requires a macOS build with --features au-offline".into())
}

#[cfg(all(feature = "au-offline", target_os = "macos"))]
pub fn process(effect: &Effect, audio: &[[f64; 2]]) -> Result<Processed, String> {
    worker::process(effect, audio)
}

#[cfg(all(feature = "au-offline", target_os = "macos"))]
mod worker {
    use super::*;
    const MAGIC: u32 = 0x3155_4144; // DAU1
    const MAX_STATE: usize = 65_536;
    const MAX_PARAMETERS: usize = 64;
    const MAX_FRAMES: usize = 480_000;

    use std::{
        io::{Read, Write},
        os::unix::process::CommandExt,
        path::PathBuf,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };
    const MAX_OUTPUT: usize = 4_000_000;

    fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
        if value.len() > MAX_STATE * 2 || value.len() % 2 != 0 {
            return Err("invalid Audio Unit state length".into());
        }
        value
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let a = (pair[0] as char)
                    .to_digit(16)
                    .ok_or("invalid Audio Unit state hex")?;
                let b = (pair[1] as char)
                    .to_digit(16)
                    .ok_or("invalid Audio Unit state hex")?;
                Ok((a * 16 + b) as u8)
            })
            .collect()
    }

    fn hex(bytes: &[u8]) -> String {
        const DIGITS: &[u8] = b"0123456789ABCDEF";
        let mut result = String::with_capacity(bytes.len() * 2);
        for byte in bytes {
            result.push(DIGITS[(byte >> 4) as usize] as char);
            result.push(DIGITS[(byte & 15) as usize] as char);
        }
        result
    }

    fn put_u32(out: &mut Vec<u8>, value: u32) {
        out.extend_from_slice(&value.to_le_bytes());
    }

    fn read_u32(input: &mut &[u8]) -> Result<u32, String> {
        if input.len() < 4 {
            return Err("truncated Audio Unit response".into());
        }
        let value = u32::from_le_bytes(input[..4].try_into().unwrap());
        *input = &input[4..];
        Ok(value)
    }

    fn read_state(input: &mut &[u8]) -> Result<String, String> {
        let size = read_u32(input)? as usize;
        if size == 0 || size > MAX_STATE || input.len() < size {
            return Err("invalid Audio Unit response state".into());
        }
        let value = hex(&input[..size]);
        *input = &input[size..];
        Ok(value)
    }

    fn encode_request(effect: &Effect, audio: &[[f64; 2]]) -> Result<Vec<u8>, String> {
        let Effect::Au {
            component_type,
            component_subtype,
            component_manufacturer,
            state_hex,
            parameters,
            ..
        } = effect
        else {
            return Err("expected Audio Unit effect".into());
        };
        if audio.len() > MAX_FRAMES {
            return Err("offline Audio Unit renders are limited to 10 seconds".into());
        }
        if parameters.len() > MAX_PARAMETERS {
            return Err("Audio Unit parameter count exceeds limit".into());
        }
        for identity in [component_type, component_subtype, component_manufacturer] {
            if identity.len() != 4 || !identity.is_ascii() {
                return Err("Audio Unit component codes must be four ASCII bytes".into());
            }
        }
        let state = decode_hex(state_hex)?;
        let mut job = Vec::new();
        put_u32(&mut job, MAGIC);
        put_u32(&mut job, state.len() as u32);
        job.extend(state);
        put_u32(&mut job, parameters.len() as u32);
        for parameter in parameters {
            if !parameter.value.is_finite() {
                return Err("Audio Unit parameter value must be finite".into());
            }
            put_u32(&mut job, parameter.id);
            job.extend_from_slice(&parameter.value.to_le_bytes());
        }
        put_u32(&mut job, audio.len() as u32);
        for frame in audio {
            for sample in frame {
                let value = *sample as f32;
                if !value.is_finite() {
                    return Err("Audio Unit input exceeds float32 range".into());
                }
                job.extend_from_slice(&value.to_le_bytes());
            }
        }
        Ok(job)
    }

    fn decode_response(output: &[u8], expected_frames: usize) -> Result<Processed, String> {
        let mut input = output;
        if read_u32(&mut input)? != MAGIC {
            return Err("invalid Audio Unit response magic".into());
        }
        let state_hex = read_state(&mut input)?;
        if read_u32(&mut input)? as usize != expected_frames {
            return Err("Audio Unit response frame count mismatch".into());
        }
        if input.len() != expected_frames.saturating_mul(8) {
            return Err(if input.len() < expected_frames.saturating_mul(8) {
                "truncated Audio Unit response audio".into()
            } else {
                "trailing bytes in Audio Unit response".into()
            });
        }
        let mut result = Vec::with_capacity(expected_frames);
        for frame in input.chunks_exact(8) {
            let left = f32::from_le_bytes(frame[..4].try_into().unwrap());
            let right = f32::from_le_bytes(frame[4..].try_into().unwrap());
            if !left.is_finite() || !right.is_finite() {
                return Err("Audio Unit produced nonfinite audio".into());
            }
            result.push([left as f64, right as f64]);
        }
        Ok(Processed {
            audio: result,
            state_hex,
            controller_state_hex: String::new(),
        })
    }

    fn bounded(mut stream: impl Read, limit: usize) -> std::io::Result<Vec<u8>> {
        let mut bytes = Vec::new();
        (&mut stream)
            .take((limit + 1) as u64)
            .read_to_end(&mut bytes)?;
        Ok(bytes)
    }

    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }

    fn kill_group(pid: u32) {
        // SAFETY: process_group(0) gives the owned worker a distinct group.
        unsafe {
            kill(-(pid as i32), 9);
        }
    }

    fn run(effect: &Effect, audio: &[[f64; 2]]) -> Result<Vec<u8>, String> {
        let Effect::Au {
            component_type,
            component_subtype,
            component_manufacturer,
            ..
        } = effect
        else {
            return Err("expected Audio Unit effect".into());
        };
        let job = encode_request(effect, audio)?;
        let host = std::env::var_os("DAW_AU_HOST")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("output/au-spike/au-host")
            });
        if !host.is_absolute() || !host.is_file() {
            return Err("Audio Unit host unavailable; build native/au/build.py or set DAW_AU_HOST to an absolute executable path".into());
        }
        let mut child = Command::new(host)
            .args([
                "process",
                component_type,
                component_subtype,
                component_manufacturer,
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()
            .map_err(|e| format!("Audio Unit host start failed: {e}"))?;
        let pid = child.id();
        let mut stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let writer = thread::spawn(move || stdin.write_all(&job));
        let reader = thread::spawn(move || bounded(stdout, MAX_OUTPUT));
        let errors = thread::spawn(move || bounded(stderr, 65_536));
        let start = Instant::now();
        let mut timed_out = false;
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Ok(status),
                Ok(None) if start.elapsed() < Duration::from_secs(15) => {
                    thread::sleep(Duration::from_millis(10))
                }
                Ok(None) => {
                    timed_out = true;
                    kill_group(pid);
                    break child.wait();
                }
                Err(error) => {
                    kill_group(pid);
                    let _ = child.wait();
                    break Err(error);
                }
            }
        };
        // Kill descendants before joining pipe threads so inherited descriptors cannot hang joins.
        kill_group(pid);
        let written = writer
            .join()
            .map_err(|_| "Audio Unit input thread failed")?;
        let output = reader
            .join()
            .map_err(|_| "Audio Unit output thread failed")?
            .map_err(|e| e.to_string())?;
        let stderr = errors
            .join()
            .map_err(|_| "Audio Unit error thread failed")?
            .map_err(|e| e.to_string())?;
        if timed_out {
            return Err("Audio Unit host timed out after 15 seconds".into());
        }
        if output.len() > MAX_OUTPUT || stderr.len() > 65_536 {
            return Err("Audio Unit host output exceeded limits".into());
        }
        let status = status.map_err(|e| e.to_string())?;
        if !status.success() {
            return Err(format!(
                "Audio Unit host failed ({status}): {}",
                String::from_utf8_lossy(&stderr[..stderr.len().min(1024)])
            ));
        }
        written.map_err(|e| format!("Audio Unit job write failed: {e}"))?;
        Ok(output)
    }

    pub(super) fn process(effect: &Effect, audio: &[[f64; 2]]) -> Result<Processed, String> {
        decode_response(&run(effect, audio)?, audio.len())
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        fn valid(frames: usize) -> Vec<u8> {
            let mut out = Vec::new();
            put_u32(&mut out, MAGIC);
            put_u32(&mut out, 1);
            out.push(0xAB);
            put_u32(&mut out, frames as u32);
            for _ in 0..frames {
                out.extend_from_slice(&0.25f32.to_le_bytes());
                out.extend_from_slice(&(-0.5f32).to_le_bytes());
            }
            out
        }
        #[test]
        fn response_round_trip_and_limits() {
            let decoded = decode_response(&valid(1), 1).unwrap();
            assert_eq!(decoded.state_hex, "AB");
            assert_eq!(decoded.controller_state_hex, "");
            assert_eq!(decoded.audio, vec![[0.25, -0.5]]);
        }
        #[test]
        fn rejects_truncation_and_empty_or_oversized_state() {
            assert!(decode_response(&[1, 2], 0).is_err());
            let mut out = valid(0);
            out[4..8].copy_from_slice(&0u32.to_le_bytes());
            assert!(decode_response(&out, 0).is_err());
            let mut out = valid(0);
            out[4..8].copy_from_slice(&65_537u32.to_le_bytes());
            assert!(decode_response(&out, 0).is_err());
            assert!(decode_response(&valid(1)[..13], 1).is_err());
        }
        #[test]
        fn rejects_frame_mismatch_nonfinite_and_trailing_bytes() {
            assert!(decode_response(&valid(0), 1).is_err());
            let mut out = valid(1);
            out[13..17].copy_from_slice(&f32::NAN.to_le_bytes());
            assert!(decode_response(&out, 1).is_err());
            let mut out = valid(0);
            out.push(0);
            assert!(decode_response(&out, 0).is_err());
        }
        #[test]
        fn rejects_state_and_parameter_request_bounds() {
            let params =
                vec![crate::session::AuParameter { id: 0, value: 0.5 }; MAX_PARAMETERS + 1];
            let make = |state_hex: &str, parameters| Effect::Au {
                id: "au".into(),
                bypass: false,
                component_type: "aufx".into(),
                component_subtype: "lpas".into(),
                component_manufacturer: "appl".into(),
                state_hex: state_hex.into(),
                parameters,
            };
            assert!(encode_request(&make("", vec![]), &[]).is_ok());
            assert!(encode_request(&make("", params), &[]).is_err());
            assert!(encode_request(&make("GG", vec![]), &[]).is_err());
        }
    }
}
