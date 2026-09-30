//! Bounded offline VST3 and Audio Unit children. This module is never called by audio callbacks.
use crate::session::{Effect, Session};

pub struct Processed {
    pub audio: Vec<[f64; 2]>,
    pub state_hex: String,
    pub controller_state_hex: String,
}

pub fn has_au(session: &Session) -> bool {
    session
        .tracks
        .iter()
        .flat_map(|t| t.effects.as_deref().unwrap_or_default())
        .any(|e| matches!(e, Effect::Au { .. }))
}

pub fn has_plugins(session: &Session) -> bool {
    session.tracks.iter().any(|track| {
        track
            .effects
            .as_deref()
            .unwrap_or_default()
            .iter()
            .any(|effect| matches!(effect, Effect::Vst3 { .. } | Effect::Au { .. }))
    })
}

/// Validate foreign state/parameters before committing; capture initial state when omitted.
pub fn prepare_session(session: &mut Session) -> Result<(), String> {
    for track in &mut session.tracks {
        for effect in track.effects.as_mut().into_iter().flatten() {
            if matches!(effect, Effect::Vst3 { .. } | Effect::Au { .. }) {
                let captured = process(effect, &[])?;
                if let Effect::Au { state_hex, .. } = effect {
                    if state_hex.is_empty() {
                        *state_hex = captured.state_hex.clone();
                    }
                }
                if let Effect::Vst3 {
                    state_hex,
                    controller_state_hex,
                    ..
                } = effect
                {
                    if state_hex.is_empty() {
                        *state_hex = captured.state_hex;
                    }
                    if controller_state_hex.is_empty() {
                        *controller_state_hex = captured.controller_state_hex;
                    }
                }
            }
        }
    }
    session.validate()?;
    if has_plugins(session)
        && serde_json::to_vec_pretty(session)
            .map_err(|e| e.to_string())?
            .len()
            > crate::control::MAX_MESSAGE_BYTES - 4096
    {
        return Err("captured plugin session exceeds the save/load byte limit".into());
    }
    Ok(())
}

pub fn process(effect: &Effect, audio: &[[f64; 2]]) -> Result<Processed, String> {
    if matches!(effect, Effect::Au { .. }) {
        return crate::au_hosting::process(effect, audio);
    }
    #[cfg(all(feature = "vst3-offline", target_os = "macos"))]
    {
        worker::process(effect, audio)
    }
    #[cfg(not(all(feature = "vst3-offline", target_os = "macos")))]
    {
        let _ = (effect, audio);
        Err("offline VST3 requires a macOS build with --features vst3-offline".into())
    }
}

#[derive(Debug, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ParameterMetadata {
    pub id: u32,
    pub name: String,
    pub short_name: String,
    pub unit: String,
    pub default_value: f64,
    pub restored_value: f64,
    pub automatable: bool,
    pub read_only: bool,
    pub step_count: u32,
}

pub fn inspect(effect: &Effect) -> Result<Vec<ParameterMetadata>, String> {
    #[cfg(all(feature = "vst3-offline", target_os = "macos"))]
    {
        worker::inspect(effect)
    }
    #[cfg(not(all(feature = "vst3-offline", target_os = "macos")))]
    {
        let _ = effect;
        Err("VST3 parameter metadata requires a macOS vst3-offline build".into())
    }
}

#[cfg(all(feature = "vst3-offline", target_os = "macos"))]
mod worker {
    use super::*;
    use std::{
        io::{Read, Write},
        os::unix::process::CommandExt,
        path::PathBuf,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };
    const MAX_OUTPUT: usize = 4_000_000;
    const MAGIC: u32 = 0x34565744;
    fn decode(value: &str) -> Result<Vec<u8>, String> {
        if value.len() > 131072 || value.len() % 2 != 0 {
            return Err("invalid plugin state length".into());
        }
        value
            .as_bytes()
            .chunks_exact(2)
            .map(|pair| {
                let a = (pair[0] as char).to_digit(16).ok_or("invalid state hex")?;
                let b = (pair[1] as char).to_digit(16).ok_or("invalid state hex")?;
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
    fn put_u32(bytes: &mut Vec<u8>, value: u32) {
        bytes.extend_from_slice(&value.to_le_bytes());
    }
    fn read_u32(input: &mut &[u8]) -> Result<u32, String> {
        if input.len() < 4 {
            return Err("truncated plugin response".into());
        }
        let value = u32::from_le_bytes(input[..4].try_into().unwrap());
        *input = &input[4..];
        Ok(value)
    }
    fn read_state(input: &mut &[u8]) -> Result<String, String> {
        let size = read_u32(input)? as usize;
        if size > 65536 || input.len() < size {
            return Err("invalid plugin response state".into());
        }
        let state = hex(&input[..size]);
        *input = &input[size..];
        Ok(state)
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
        // SAFETY: Command::process_group(0) created this owned child's distinct process group.
        // A negative positive PID targets only that group; SIGKILL releases retained pipes.
        unsafe {
            kill(-(pid as i32), 9);
        }
    }
    fn run(effect: &Effect, audio: &[[f64; 2]], metadata: bool) -> Result<Vec<u8>, String> {
        let Effect::Vst3 {
            bundle_path,
            class_id,
            state_hex,
            controller_state_hex,
            parameters,
            ..
        } = effect
        else {
            return Err("expected VST3 effect".into());
        };
        if audio.len() > 480000 {
            return Err("offline VST3 renders are limited to 10 seconds".into());
        }
        let host = std::env::var_os("DAW_VST3_HOST")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("output/vst3-spike/vst3-host")
            });
        if !host.is_absolute() || !host.is_file() {
            return Err("VST3 host unavailable; build native/vst3/build.py or set DAW_VST3_HOST to an absolute executable path".into());
        }
        let mut job = Vec::new();
        put_u32(&mut job, MAGIC);
        for state in [state_hex, controller_state_hex] {
            let bytes = decode(state)?;
            put_u32(&mut job, bytes.len() as u32);
            job.extend(bytes);
        }
        put_u32(&mut job, parameters.len() as u32);
        for parameter in parameters {
            put_u32(&mut job, parameter.id);
            job.extend(parameter.value.to_le_bytes());
            let points: Vec<_> = parameter
                .points
                .iter()
                .filter(|point| point.frame < (audio.len() as u64))
                .collect();
            put_u32(&mut job, points.len() as u32);
            for point in points {
                put_u32(&mut job, point.frame as u32);
                job.extend(point.value.to_le_bytes());
            }
        }
        put_u32(&mut job, audio.len() as u32);
        for frame in audio {
            for sample in frame {
                let value = *sample as f32;
                if !value.is_finite() {
                    return Err("plugin input exceeds float32 range".into());
                }
                job.extend(value.to_le_bytes());
            }
        }
        let mut child = Command::new(host)
            .args([
                if metadata { "metadata" } else { "process" },
                bundle_path,
                class_id,
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()
            .map_err(|e| format!("plugin host start failed: {e}"))?;
        let pid = child.id();
        let mut stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let writer = thread::spawn(move || stdin.write_all(&job));
        let output_limit = if metadata { 131072 } else { MAX_OUTPUT };
        let reader = thread::spawn(move || bounded(stdout, output_limit));
        let errors = thread::spawn(move || bounded(stderr, 65536));
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
        kill_group(pid);
        let written = writer.join().map_err(|_| "plugin input thread failed")?;
        let output = reader
            .join()
            .map_err(|_| "plugin output thread failed")?
            .map_err(|e| e.to_string())?;
        let stderr = errors
            .join()
            .map_err(|_| "plugin error thread failed")?
            .map_err(|e| e.to_string())?;
        if timed_out {
            return Err("plugin host timed out after 15 seconds".into());
        }
        if output.len() > output_limit || stderr.len() > 65536 {
            return Err("plugin host output exceeded limits".into());
        }
        let status = status.map_err(|e| e.to_string())?;
        if !status.success() {
            return Err(format!(
                "plugin host failed ({status}): {}",
                String::from_utf8_lossy(&stderr[..stderr.len().min(1024)])
            ));
        }
        written.map_err(|e| format!("plugin job write failed: {e}"))?;
        Ok(output)
    }
    pub(super) fn process(effect: &Effect, audio: &[[f64; 2]]) -> Result<Processed, String> {
        let output = run(effect, audio, false)?;
        let mut input = output.as_slice();
        if read_u32(&mut input)? != MAGIC {
            return Err("invalid plugin response magic".into());
        }
        let state_hex = read_state(&mut input)?;
        let controller_state_hex = read_state(&mut input)?;
        if read_u32(&mut input)? as usize != audio.len() || input.len() != audio.len() * 8 {
            return Err("plugin response frame count mismatch".into());
        }
        let mut result = Vec::with_capacity(audio.len());
        for frame in input.chunks_exact(8) {
            let left = f32::from_le_bytes(frame[..4].try_into().unwrap());
            let right = f32::from_le_bytes(frame[4..].try_into().unwrap());
            if !left.is_finite() || !right.is_finite() {
                return Err("plugin produced nonfinite audio".into());
            }
            result.push([left as f64, right as f64]);
        }
        Ok(Processed {
            audio: result,
            state_hex,
            controller_state_hex,
        })
    }
    fn read_text(input: &mut &[u8]) -> Result<String, String> {
        let size = read_u32(input)? as usize;
        if size > 512 || input.len() < size {
            return Err("invalid metadata text length".into());
        }
        let value = std::str::from_utf8(&input[..size])
            .map_err(|_| "invalid metadata UTF-8")?
            .to_owned();
        *input = &input[size..];
        Ok(value)
    }
    fn read_value(input: &mut &[u8]) -> Result<f64, String> {
        if input.len() < 8 {
            return Err("truncated metadata value".into());
        }
        let value = f64::from_le_bytes(input[..8].try_into().unwrap());
        *input = &input[8..];
        if !value.is_finite() || !(0.0..=1.0).contains(&value) {
            return Err("invalid normalized metadata value".into());
        }
        Ok(value)
    }
    fn read_flag(input: &mut &[u8]) -> Result<bool, String> {
        match read_u32(input)? {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err("invalid metadata flag".into()),
        }
    }
    pub(super) fn inspect(effect: &Effect) -> Result<Vec<ParameterMetadata>, String> {
        let Effect::Vst3 { parameters, .. } = effect else {
            return Err("metadata requires a VST3 effect".into());
        };
        let output = run(effect, &[], true)?;
        let mut input = output.as_slice();
        if read_u32(&mut input)? != 0x314D5744 {
            return Err("invalid metadata response magic".into());
        }
        let count = read_u32(&mut input)? as usize;
        if count != parameters.len() || count > 64 {
            return Err("metadata parameter count mismatch".into());
        }
        let mut records = Vec::with_capacity(count);
        for saved in parameters {
            let id = read_u32(&mut input)?;
            if id != saved.id {
                return Err("metadata parameter identity mismatch".into());
            }
            let name = read_text(&mut input)?;
            let short_name = read_text(&mut input)?;
            let unit = read_text(&mut input)?;
            let default_value = read_value(&mut input)?;
            let restored_value = read_value(&mut input)?;
            let automatable = read_flag(&mut input)?;
            let read_only = read_flag(&mut input)?;
            let step_count = read_u32(&mut input)?;
            if step_count > i32::MAX as u32 {
                return Err("invalid metadata step count".into());
            }
            records.push(ParameterMetadata {
                id,
                name,
                short_name,
                unit,
                default_value,
                restored_value,
                automatable,
                read_only,
                step_count,
            });
        }
        if !input.is_empty() {
            return Err("trailing metadata response bytes".into());
        }
        Ok(records)
    }
}
