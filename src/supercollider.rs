//! Bounded, owned SuperCollider NRT renderer. This is an offline process API,
//! not a live audio callback or transport implementation.
use serde::Serialize;
use std::path::Path;
#[cfg(unix)]
use std::{fs, io::Read};

#[derive(Debug, Serialize)]
pub struct Report {
    pub frames: u64,
    pub sample_rate: u32,
    pub channels: u16,
    pub path: String,
}

#[cfg(not(unix))]
pub fn render_score(_: &Path, _: &Path, _: u32) -> Result<Report, String> {
    Err("SuperCollider NRT rendering is unavailable on this platform".into())
}

#[cfg(unix)]
pub fn render_score(
    score_path: &Path,
    destination: &Path,
    sample_rate: u32,
) -> Result<Report, String> {
    use std::{
        fs::OpenOptions,
        os::unix::{fs::PermissionsExt, process::CommandExt},
        path::PathBuf,
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };

    const MAX_SCORE: usize = 1_048_576;
    const MAX_RECORDS: usize = 16_384;
    const MAX_LOG: usize = 65_536;
    const MAX_DURATION: f64 = 10.0;

    if !(8_000..=192_000).contains(&sample_rate) {
        return Err("sample rate must be between 8000 and 192000".into());
    }
    if fs::symlink_metadata(destination).is_ok() {
        return Err("destination already exists".into());
    }
    if !fs::metadata(score_path)
        .map_err(|e| format!("score file unavailable: {e}"))?
        .is_file()
    {
        return Err("score must be a regular file".into());
    }
    let score_file =
        fs::File::open(score_path).map_err(|e| format!("score file unavailable: {e}"))?;
    let score_meta = score_file
        .metadata()
        .map_err(|e| format!("score metadata failed: {e}"))?;
    if !score_meta.is_file() || score_meta.len() > MAX_SCORE as u64 {
        return Err("score must be a regular file no larger than 1 MiB".into());
    }
    let mut score = Vec::new();
    score_file
        .take((MAX_SCORE + 1) as u64)
        .read_to_end(&mut score)
        .map_err(|e| format!("score read failed: {e}"))?;
    if score.len() > MAX_SCORE {
        return Err("score must be a regular file no larger than 1 MiB".into());
    }
    let duration = validate_score(&score, sample_rate, MAX_RECORDS)?;
    let frames = (duration * sample_rate as f64).round() as u64;
    if frames == 0 || frames > (MAX_DURATION * sample_rate as f64).round() as u64 {
        return Err("score duration must render between 1 frame and 10 seconds".into());
    }

    let configured = std::env::var_os("DAW_SCSYNTH")
        .ok_or("DAW_SCSYNTH must name an absolute scsynth executable")?;
    let executable = PathBuf::from(configured);
    if !executable.is_absolute() {
        return Err("DAW_SCSYNTH must be an absolute executable path".into());
    }
    let meta = fs::metadata(&executable).map_err(|e| format!("DAW_SCSYNTH unavailable: {e}"))?;
    if !meta.is_file() || meta.permissions().mode() & 0o111 == 0 {
        return Err("DAW_SCSYNTH must be an existing regular executable".into());
    }

    let temp = tempfile::tempdir().map_err(|e| format!("temporary directory failed: {e}"))?;
    let score_copy = temp.path().join("score.osc");
    let wav_path = temp.path().join("render.wav");
    fs::write(&score_copy, &score).map_err(|e| format!("score snapshot failed: {e}"))?;
    let mut child = Command::new(executable)
        .args([
            "-N",
            score_copy.to_str().ok_or("temporary path is not UTF-8")?,
            "_",
            wav_path.to_str().ok_or("temporary path is not UTF-8")?,
            &sample_rate.to_string(),
            "WAV",
            "int16",
            "-i",
            "0",
            "-o",
            "2",
            "-z",
            "64",
            "-D",
            "0",
            "-R",
            "0",
            "-V",
            "-1",
            "-P",
            temp.path().to_str().ok_or("temporary path is not UTF-8")?,
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .stdin(Stdio::null())
        .process_group(0)
        .spawn()
        .map_err(|e| format!("scsynth start failed: {e}"))?;
    let pid = child.id();
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let out_reader = thread::spawn(move || bounded(stdout, MAX_LOG));
    let err_reader = thread::spawn(move || bounded(stderr, MAX_LOG));
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
            Err(e) => {
                kill_group(pid);
                let _ = child.wait();
                break Err(e);
            }
        }
    };
    // Terminate descendants before joining pipes; they may have inherited descriptors.
    kill_group(pid);
    let stdout = out_reader
        .join()
        .map_err(|_| "scsynth stdout reader failed")?
        .map_err(|e| e.to_string())?;
    let stderr = err_reader
        .join()
        .map_err(|_| "scsynth stderr reader failed")?
        .map_err(|e| e.to_string())?;
    if timed_out {
        return Err("scsynth timed out after 15 seconds".into());
    }
    let status = status.map_err(|e| format!("scsynth wait failed: {e}"))?;
    let logs = [
        String::from_utf8_lossy(&stdout.bytes),
        String::from_utf8_lossy(&stderr.bytes),
    ]
    .join("\n");
    if stdout.exceeded || stderr.exceeded {
        return Err("scsynth output exceeded 64 KiB".into());
    }
    let lower_logs = logs.to_ascii_lowercase();
    if !status.success()
        || ["failure in server", "*** error", "error:", "exception in "]
            .iter()
            .any(|needle| lower_logs.contains(needle))
    {
        return Err(format!(
            "scsynth render failed ({status}): {}",
            logs.chars().take(1024).collect::<String>()
        ));
    }

    let wav_meta =
        fs::symlink_metadata(&wav_path).map_err(|e| format!("scsynth WAV missing: {e}"))?;
    let max_bytes = (MAX_DURATION * sample_rate as f64 * 4.0) as u64 + 65_536;
    if !wav_meta.file_type().is_file() || wav_meta.len() > max_bytes {
        return Err("scsynth WAV is not a regular file within the output limit".into());
    }
    let reader =
        hound::WavReader::open(&wav_path).map_err(|e| format!("invalid scsynth WAV: {e}"))?;
    let spec = reader.spec();
    if spec.channels != 2
        || spec.sample_rate != sample_rate
        || spec.bits_per_sample != 16
        || spec.sample_format != hound::SampleFormat::Int
    {
        return Err("scsynth WAV must be stereo PCM16 at the requested sample rate".into());
    }
    let padded_frames = reader.duration() as u64;
    if padded_frames < frames || padded_frames > frames + 128 {
        return Err("scsynth WAV frame count does not match score duration".into());
    }
    let mut samples = reader
        .into_samples::<i16>()
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| format!("invalid scsynth PCM samples: {e}"))?;
    if samples.len() != (padded_frames * 2) as usize {
        return Err("scsynth WAV is truncated".into());
    }
    samples.truncate((frames * 2) as usize);

    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(destination)
        .map_err(|e| format!("destination create failed: {e}"))?;
    let write_result = {
        let mut buffered = std::io::BufWriter::new(&mut output);
        write_pcm16(&mut buffered, sample_rate, &samples)
    }
    .and_then(|()| output.sync_all());
    if let Err(error) = write_result {
        drop(output);
        let _ = fs::remove_file(destination);
        return Err(format!("destination write failed: {error}"));
    }
    Ok(Report {
        frames,
        sample_rate,
        channels: 2,
        path: destination.to_string_lossy().into_owned(),
    })
}

#[cfg(unix)]
fn validate_score(score: &[u8], sample_rate: u32, max_records: usize) -> Result<f64, String> {
    if score.is_empty() {
        return Err("score is empty".into());
    }
    let mut cursor = 0usize;
    let mut previous: Option<f64> = None;
    let mut last = None;
    let mut records = 0;
    while cursor < score.len() {
        if score.len() - cursor < 4 {
            return Err("truncated score record length".into());
        }
        let size = u32::from_be_bytes(score[cursor..cursor + 4].try_into().unwrap()) as usize;
        cursor += 4;
        if !(16..=65_516).contains(&size) || size % 4 != 0 || size > score.len() - cursor {
            return Err("invalid or truncated score packet length".into());
        }
        let packet = &score[cursor..cursor + size];
        cursor += size;
        if !packet.starts_with(b"#bundle\0") {
            return Err("score records must contain OSC bundles".into());
        }
        let seconds = u32::from_be_bytes(packet[8..12].try_into().unwrap()) as f64
            + u32::from_be_bytes(packet[12..16].try_into().unwrap()) as f64 / 4_294_967_296.0;
        if previous.is_some_and(|p| seconds < p) {
            return Err("score timestamps must be nondecreasing".into());
        }
        previous = Some(seconds);
        last = Some(seconds);
        let mut p = 16;
        while p < packet.len() {
            if packet.len() - p < 4 {
                return Err("truncated OSC bundle element size".into());
            }
            let n = u32::from_be_bytes(packet[p..p + 4].try_into().unwrap()) as usize;
            p += 4;
            if n < 8 || n % 4 != 0 || n > packet.len() - p {
                return Err("invalid OSC bundle element".into());
            }
            validate_message(&packet[p..p + n])?;
            p += n;
        }
        records += 1;
        if records > max_records {
            return Err("score exceeds record limit".into());
        }
    }
    let end = last.ok_or("score has no records")?;
    if !(0.001..=10.0).contains(&end) {
        return Err("score must end between 0.001 and 10 seconds".into());
    }
    // First record is checked from the first packet timestamp.
    let first_size = u32::from_be_bytes(score[..4].try_into().unwrap()) as usize;
    let first = &score[4..4 + first_size];
    let first_time = u32::from_be_bytes(first[8..12].try_into().unwrap()) as f64
        + u32::from_be_bytes(first[12..16].try_into().unwrap()) as f64 / 4_294_967_296.0;
    if first_time != 0.0 {
        return Err("score must start at time zero".into());
    }
    let frames = (end * sample_rate as f64).round();
    if frames > (10.0 * sample_rate as f64).round() {
        return Err("score duration is limited to 10 seconds".into());
    }
    Ok(end)
}

#[cfg(unix)]
fn validate_message(message: &[u8]) -> Result<(), String> {
    fn padded_string<'a>(data: &'a [u8], at: &mut usize) -> Result<&'a [u8], String> {
        let tail = data.get(*at..).ok_or("truncated OSC string")?;
        let end = tail
            .iter()
            .position(|&b| b == 0)
            .ok_or("unterminated OSC string")?;
        let value = &tail[..end];
        let padded = (end + 4) & !3;
        if tail
            .get(end..padded)
            .ok_or("truncated OSC string padding")?
            .iter()
            .any(|&b| b != 0)
        {
            return Err("OSC string padding must be zero".into());
        }
        *at += padded;
        if *at > data.len() {
            return Err("truncated OSC string padding".into());
        }
        Ok(value)
    }
    let mut at = 0;
    let address = padded_string(message, &mut at)?;
    std::str::from_utf8(address).map_err(|_| "OSC address must be UTF-8")?;
    if !address.starts_with(b"/") {
        return Err("invalid OSC address".into());
    }
    let tags = padded_string(message, &mut at)?;
    if tags.first() != Some(&b',') {
        return Err("invalid OSC type tag string".into());
    }
    if address == b"/d_recv" && tags.get(1) != Some(&b'b') {
        return Err("/d_recv requires a SynthDef blob".into());
    }
    for (index, tag) in tags[1..].iter().enumerate() {
        let size = match tag {
            b'i' | b'f' | b'c' => 4,
            b'h' | b'd' => 8,
            b's' | b'S' => {
                let value = padded_string(message, &mut at)?;
                std::str::from_utf8(value).map_err(|_| "OSC strings must be UTF-8")?;
                continue;
            }
            b'b' => {
                if message.len().saturating_sub(at) < 4 {
                    return Err("truncated OSC blob".into());
                }
                let n = u32::from_be_bytes(message[at..at + 4].try_into().unwrap()) as usize;
                at += 4;
                let padded = n.checked_add(3).ok_or("invalid OSC blob length")? & !3;
                if padded > message.len().saturating_sub(at) {
                    return Err("truncated OSC blob".into());
                }
                if address == b"/d_recv" && index == 0 {
                    let blob = &message[at..at + n];
                    if blob.len() < 10
                        || &blob[..4] != b"SCgf"
                        || ![1, 2].contains(&u32::from_be_bytes(blob[4..8].try_into().unwrap()))
                        || i16::from_be_bytes(blob[8..10].try_into().unwrap()) <= 0
                    {
                        return Err("invalid SynthDef file header in /d_recv".into());
                    }
                }
                if message[at + n..at + padded].iter().any(|byte| *byte != 0) {
                    return Err("OSC blob padding must be zero".into());
                }
                at += padded;
                continue;
            }
            b'T' | b'F' | b'N' | b'I' => 0,
            _ => return Err("unsupported OSC type tag".into()),
        };
        if size > message.len().saturating_sub(at) {
            return Err("truncated OSC argument".into());
        }
        if *tag == b'f' && !f32::from_be_bytes(message[at..at + 4].try_into().unwrap()).is_finite()
        {
            return Err("OSC float must be finite".into());
        }
        if *tag == b'd' && !f64::from_be_bytes(message[at..at + 8].try_into().unwrap()).is_finite()
        {
            return Err("OSC double must be finite".into());
        }
        at += size;
    }
    if at != message.len() {
        return Err("trailing OSC message bytes".into());
    }
    Ok(())
}

#[cfg(unix)]
struct Bounded {
    bytes: Vec<u8>,
    exceeded: bool,
}
#[cfg(unix)]
fn bounded(mut stream: impl std::io::Read, limit: usize) -> std::io::Result<Bounded> {
    let mut bytes = Vec::new();
    stream
        .by_ref()
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)?;
    let exceeded = bytes.len() > limit;
    bytes.truncate(limit);
    Ok(Bounded { bytes, exceeded })
}
#[cfg(unix)]
unsafe extern "C" {
    fn kill(pid: i32, signal: i32) -> i32;
}
#[cfg(unix)]
fn kill_group(pid: u32) {
    // SAFETY: process_group(0) created this owned child's group.
    unsafe {
        kill(-(pid as i32), 9);
    }
}

#[cfg(unix)]
fn write_pcm16(
    mut out: impl std::io::Write + std::io::Seek,
    rate: u32,
    samples: &[i16],
) -> std::io::Result<()> {
    let data_len = (samples.len() * 2) as u32;
    out.write_all(b"RIFF")?;
    out.write_all(&(36u32 + data_len).to_le_bytes())?;
    out.write_all(b"WAVEfmt ")?;
    out.write_all(&16u32.to_le_bytes())?;
    out.write_all(&1u16.to_le_bytes())?;
    out.write_all(&2u16.to_le_bytes())?;
    out.write_all(&rate.to_le_bytes())?;
    out.write_all(&(rate * 4).to_le_bytes())?;
    out.write_all(&4u16.to_le_bytes())?;
    out.write_all(&16u16.to_le_bytes())?;
    out.write_all(b"data")?;
    out.write_all(&data_len.to_le_bytes())?;
    for sample in samples {
        out.write_all(&sample.to_le_bytes())?;
    }
    out.flush()
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    fn record(sec: u32, frac: u32) -> Vec<u8> {
        let message = b"/done\0\0\0,\0\0\0";
        let mut packet = b"#bundle\0".to_vec();
        packet.extend(sec.to_be_bytes());
        packet.extend(frac.to_be_bytes());
        packet.extend((message.len() as u32).to_be_bytes());
        packet.extend(message);
        let mut record = (packet.len() as u32).to_be_bytes().to_vec();
        record.extend(packet);
        record
    }
    #[test]
    fn accepts_valid_and_rejects_truncated_lengths() {
        let mut score = record(0, 0);
        score.extend(record(1, 0));
        assert_eq!(validate_score(&score, 48000, 10).unwrap(), 1.0);
        let mut bad = score.clone();
        bad[3] = 0xff;
        assert!(validate_score(&bad, 48000, 10).is_err());
    }
    #[test]
    fn rejects_bad_blob_padding_and_nonfinite_arguments() {
        let mut message = b"/x\0\0,b\0\0".to_vec();
        message.extend(1u32.to_be_bytes());
        message.extend([7, 0, 0, 0]);
        assert!(validate_message(&message).is_ok());
        *message.last_mut().unwrap() = 1;
        assert!(validate_message(&message).is_err());
        let mut message = b"/x\0\0,f\0\0".to_vec();
        message.extend(f32::NAN.to_be_bytes());
        assert!(validate_message(&message).is_err());
        let mut message = b"/d_recv\0,b\0\0".to_vec();
        message.extend(4u32.to_be_bytes());
        message.extend(b"bad!");
        assert!(validate_message(&message).is_err());
    }
    #[test]
    fn validates_timestamps_and_record_limit() {
        let mut score = record(1, 0);
        score.extend(record(0, 0));
        assert!(validate_score(&score, 48000, 10).is_err());
        assert!(validate_score(&record(0, 0), 48000, 10).is_err());
        let mut score = record(0, 0);
        score.extend(record(1, 0));
        assert!(validate_score(&score, 48000, 1).is_err());
        assert!(validate_score(&record(0, 0), 48000, 0).is_err());
        let mut score = record(0, 0);
        score.extend(record(10, 1));
        assert!(validate_score(&score, 48000, 10).is_err());
    }
}
