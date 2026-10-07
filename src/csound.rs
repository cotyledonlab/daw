//! Owned offline Csound jobs. Arbitrary CSD code is caller-trusted, not sandboxed.
use serde::Serialize;
use std::path::Path;

#[derive(Debug, Serialize)]
pub struct Report {
    pub path: String,
    pub frames: u64,
    pub sample_rate: u32,
    pub channels: u16,
}

#[cfg(not(unix))]
pub fn render(_: &Path, _: &Path, _: u32, _: u64) -> Result<Report, String> {
    Err("Csound offline rendering is unavailable on this platform".into())
}

#[cfg(unix)]
pub fn render(csd: &Path, destination: &Path, rate: u32, frames: u64) -> Result<Report, String> {
    use std::{
        fs::{self, OpenOptions},
        io::{Read, Write},
        os::unix::{fs::PermissionsExt, process::CommandExt},
        path::PathBuf,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };
    if !(8000..=192000).contains(&rate) || frames == 0 || frames > u64::from(rate) * 10 {
        return Err("Csound rate/duration outside supported limits".into());
    }
    if fs::symlink_metadata(destination).is_ok() {
        return Err("destination already exists".into());
    }
    if !fs::metadata(csd)
        .map_err(|e| format!("CSD unavailable: {e}"))?
        .is_file()
    {
        return Err("CSD must be a regular file".into());
    }
    let input = fs::File::open(csd).map_err(|e| format!("CSD open failed: {e}"))?;
    let meta = input
        .metadata()
        .map_err(|e| format!("CSD metadata failed: {e}"))?;
    if !meta.is_file() || meta.len() > 1_048_576 {
        return Err("CSD must be a regular file no larger than 1 MiB".into());
    }
    let mut snapshot = Vec::new();
    input
        .take(1_048_577)
        .read_to_end(&mut snapshot)
        .map_err(|e| format!("CSD read failed: {e}"))?;
    if snapshot.is_empty()
        || snapshot.len() > 1_048_576
        || snapshot.contains(&0)
        || std::str::from_utf8(&snapshot).is_err()
    {
        return Err("CSD must be nonempty UTF-8 without NUL, no larger than 1 MiB".into());
    }
    let executable = PathBuf::from(
        std::env::var_os("DAW_CSOUND")
            .ok_or("DAW_CSOUND must name an absolute Csound executable")?,
    );
    let meta = fs::metadata(&executable).map_err(|e| format!("DAW_CSOUND unavailable: {e}"))?;
    if !executable.is_absolute() || !meta.is_file() || meta.permissions().mode() & 0o111 == 0 {
        return Err("DAW_CSOUND must be an absolute regular executable".into());
    }
    let temp = tempfile::tempdir().map_err(|e| format!("temporary directory failed: {e}"))?;
    let program = temp.path().join("program.csd");
    let wav = temp.path().join("render.wav");
    let rc = temp.path().join("empty.rc");
    fs::write(&program, snapshot).map_err(|e| format!("CSD snapshot failed: {e}"))?;
    fs::write(&rc, []).map_err(|e| format!("Csound configuration snapshot failed: {e}"))?;
    let mut child = Command::new(executable)
        .args([
            "-+ignore_csopts=1",
            "--no-default-paths",
            "-+rtaudio=null",
            "-+rtmidi=null",
            "-d",
            "-m0",
            "-W",
            "-s",
            "-r",
            &rate.to_string(),
            "--ksmps=1",
            "-o",
        ])
        .arg(&wav)
        .arg(&program)
        .env("CSOUND6RC", &rc)
        .env("CSOUND7RC", &rc)
        .current_dir(temp.path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|e| format!("Csound start failed: {e}"))?;
    let pid = child.id();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let out = thread::spawn(move || diagnostics(stdout));
    let err = thread::spawn(move || diagnostics(stderr));
    let start = Instant::now();
    let max_bytes = u64::from(rate) * 10 * 4 + 65_536;
    let mut reason = None;
    let status = loop {
        if start.elapsed() >= Duration::from_secs(15) {
            reason = Some("Csound timed out after 15 seconds");
        } else if fs::symlink_metadata(&wav)
            .is_ok_and(|m| !m.file_type().is_file() || m.len() > max_bytes)
        {
            reason = Some("Csound WAV exceeded the output limit or is not a regular file");
        }
        if reason.is_some() {
            kill_group(pid);
            break child.wait();
        }
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => thread::sleep(Duration::from_millis(10)),
            Err(e) => {
                kill_group(pid);
                let _ = child.wait();
                break Err(e);
            }
        }
    };
    // Descendants may retain pipe descriptors after the direct child exits.
    kill_group(pid);
    let out = out
        .join()
        .map_err(|_| "Csound stdout reader failed")?
        .map_err(|e| e.to_string())?;
    let err = err
        .join()
        .map_err(|_| "Csound stderr reader failed")?
        .map_err(|e| e.to_string())?;
    if let Some(reason) = reason {
        return Err(reason.into());
    }
    let status = status.map_err(|e| format!("Csound wait failed: {e}"))?;
    if out.1 || err.1 {
        return Err("Csound diagnostics exceeded 64 KiB".into());
    }
    if !status.success() {
        let logs = [
            String::from_utf8_lossy(&out.0),
            String::from_utf8_lossy(&err.0),
        ]
        .join("\n");
        return Err(format!(
            "Csound render failed ({status}): {}",
            logs.chars().take(1024).collect::<String>()
        ));
    }
    let meta = fs::symlink_metadata(&wav).map_err(|e| format!("Csound WAV missing: {e}"))?;
    if !meta.file_type().is_file() || meta.len() > max_bytes {
        return Err("Csound WAV is not a regular file within the output limit".into());
    }
    let reader = hound::WavReader::open(&wav).map_err(|e| format!("invalid Csound WAV: {e}"))?;
    let spec = reader.spec();
    if spec.channels != 2
        || spec.sample_rate != rate
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err("Csound WAV must be stereo PCM16 at the requested rate".into());
    }
    if u64::from(reader.duration()) != frames {
        return Err("Csound WAV frame count does not match duration_frames".into());
    }
    let samples = reader
        .into_samples::<i16>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("invalid Csound samples: {e}"))?;
    if samples.len() as u64 != frames * 2 {
        return Err("Csound WAV is truncated".into());
    }
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .map_err(|e| format!("destination create failed: {e}"))?;
    // Copy the validated runtime file, including its harmless WAV metadata.
    let publication = fs::read(&wav)
        .and_then(|bytes| output.write_all(&bytes))
        .and_then(|()| output.sync_all());
    if let Err(e) = publication {
        drop(output);
        let _ = fs::remove_file(destination);
        return Err(format!("destination write failed: {e}"));
    }
    Ok(Report {
        path: destination.to_string_lossy().into_owned(),
        frames,
        sample_rate: rate,
        channels: 2,
    })
}

#[cfg(unix)]
pub(crate) fn diagnostics(mut pipe: impl std::io::Read) -> std::io::Result<(Vec<u8>, bool)> {
    let mut bytes = Vec::new();
    let mut overflow = false;
    let mut chunk = [0; 4096];
    loop {
        let count = pipe.read(&mut chunk)?;
        if count == 0 {
            break;
        }
        let keep = count.min(65_536 - bytes.len());
        bytes.extend_from_slice(&chunk[..keep]);
        overflow |= keep < count;
    }
    Ok((bytes, overflow))
}

#[cfg(unix)]
pub(crate) fn kill_group(pid: u32) {
    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
    }
    // SAFETY: process_group(0) created this owned child's process group.
    unsafe {
        kill(-(pid as i32), 9);
    }
}
