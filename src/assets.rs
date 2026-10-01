use crate::session::{Clip, Device, Session};
use hound::{SampleFormat, WavReader, WavSpec};
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub const MAX_FILE_BYTES: u64 = 32 * 1024 * 1024;
pub const MAX_DECODED_BYTES: usize = 128 * 1024 * 1024;
pub const MAX_ASSETS: usize = 128;

#[derive(Debug)]
pub struct PreparedAudioClip {
    pub track_index: usize,
    pub start: u64,
    pub end: u64,
    pub source_offset: usize,
    pub gain: f64,
    pub frames: Arc<Vec<[f64; 2]>>,
}

struct AudioReference<'a> {
    track_index: usize,
    track_id: &'a str,
    clip_id: &'a str,
    start: u64,
    length: u64,
    source_offset: u64,
    source_path: &'a str,
    gain: f64,
}

pub fn prepare(session: &Session) -> Result<Vec<PreparedAudioClip>, String> {
    prepare_with_budget(session, 0)
}

pub(crate) fn prepare_with_budget(
    session: &Session,
    reserved_bytes: usize,
) -> Result<Vec<PreparedAudioClip>, String> {
    session.validate()?;
    let mut references = Vec::new();
    for (track_index, track) in session.tracks.iter().enumerate() {
        let Device::Audio { gain: track_gain } = &track.device else {
            continue;
        };
        for clip in track.clips.as_deref().unwrap_or_default() {
            let Clip::Audio(audio) = clip else {
                continue;
            };
            references.push(AudioReference {
                track_index,
                track_id: &track.id,
                clip_id: &audio.id,
                start: audio.start_frame,
                length: audio.length_frames,
                source_offset: audio.source_offset_frames,
                source_path: &audio.source_path,
                gain: *track_gain * audio.gain,
            });
        }
    }
    references.sort_by(|a, b| (a.track_id, a.clip_id).cmp(&(b.track_id, b.clip_id)));
    if references.is_empty() {
        return Ok(Vec::new());
    }

    let root = match &session.asset_root {
        Some(root) => root.clone(),
        None => std::env::current_dir().map_err(|e| format!("cannot determine asset root: {e}"))?,
    };
    let root = fs::canonicalize(&root)
        .map_err(|e| format!("cannot resolve asset root {}: {e}", root.display()))?;

    let mut decoded = BTreeMap::<PathBuf, Arc<Vec<[f64; 2]>>>::new();
    let mut decoded_bytes = reserved_bytes;
    let mut prepared = Vec::with_capacity(references.len());
    for reference in references {
        let relative = Path::new(reference.source_path);
        if relative.is_absolute()
            || reference.source_path.contains('\\')
            || relative
                .components()
                .any(|part| matches!(part, std::path::Component::ParentDir))
        {
            return Err(format!(
                "audio clip {} has an unsafe asset path",
                reference.clip_id
            ));
        }
        let candidate = root.join(relative);
        let canonical = fs::canonicalize(&candidate).map_err(|e| {
            format!(
                "audio clip {} asset {} cannot be resolved: {e}",
                reference.clip_id,
                candidate.display()
            )
        })?;
        if !canonical.starts_with(&root) {
            return Err(format!(
                "audio clip {} asset escapes the asset root",
                reference.clip_id
            ));
        }
        let frames = if let Some(frames) = decoded.get(&canonical) {
            Arc::clone(frames)
        } else {
            if decoded.len() >= MAX_ASSETS {
                return Err(format!(
                    "session references more than {MAX_ASSETS} unique audio assets"
                ));
            }
            let (frames, bytes) = decode_file(&canonical, session.sample_rate, decoded_bytes)?;
            decoded_bytes = decoded_bytes
                .checked_add(bytes)
                .ok_or_else(|| "decoded audio size overflow".to_string())?;
            let frames = Arc::new(frames);
            decoded.insert(canonical, Arc::clone(&frames));
            frames
        };
        let end = reference
            .source_offset
            .checked_add(reference.length)
            .ok_or_else(|| format!("audio clip {} source range overflows", reference.clip_id))?;
        if end > frames.len() as u64 {
            return Err(format!(
                "audio clip {} source range ends at frame {end}, beyond asset length {}",
                reference.clip_id,
                frames.len()
            ));
        }
        let source_offset = usize::try_from(reference.source_offset).map_err(|_| {
            format!(
                "audio clip {} source offset is too large",
                reference.clip_id
            )
        })?;
        let end = reference
            .start
            .checked_add(reference.length)
            .ok_or_else(|| format!("audio clip {} timeline end overflows", reference.clip_id))?;
        prepared.push(PreparedAudioClip {
            track_index: reference.track_index,
            start: reference.start,
            end,
            source_offset,
            gain: reference.gain,
            frames,
        });
    }
    Ok(prepared)
}

fn decode_file(
    path: &Path,
    sample_rate: u32,
    already_decoded: usize,
) -> Result<(Vec<[f64; 2]>, usize), String> {
    if !fs::metadata(path)
        .map_err(|e| format!("cannot inspect audio asset {}: {e}", path.display()))?
        .is_file()
    {
        return Err(format!(
            "audio asset {} is not a regular file",
            path.display()
        ));
    }
    let file =
        File::open(path).map_err(|e| format!("cannot open audio asset {}: {e}", path.display()))?;
    let metadata = file
        .metadata()
        .map_err(|e| format!("cannot inspect audio asset {}: {e}", path.display()))?;
    if !metadata.is_file() {
        return Err(format!(
            "audio asset {} is not a regular file",
            path.display()
        ));
    }
    if metadata.len() > MAX_FILE_BYTES {
        return Err(format!(
            "audio asset {} exceeds {MAX_FILE_BYTES} bytes",
            path.display()
        ));
    }
    let cap = usize::try_from(MAX_FILE_BYTES + 1).expect("asset limit fits usize");
    let file_size = usize::try_from(metadata.len())
        .map_err(|_| format!("audio asset {} is too large", path.display()))?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(file_size)
        .map_err(|_| format!("cannot allocate audio asset {}", path.display()))?;
    file.take(cap as u64)
        .read_to_end(&mut bytes)
        .map_err(|e| format!("cannot read audio asset {}: {e}", path.display()))?;
    if bytes.len() as u64 > MAX_FILE_BYTES {
        return Err(format!(
            "audio asset {} exceeds {MAX_FILE_BYTES} bytes",
            path.display()
        ));
    }
    if bytes.len() as u64 != metadata.len() {
        return Err(format!(
            "audio asset {} changed while it was being read",
            path.display()
        ));
    }
    let mut reader = WavReader::new(std::io::Cursor::new(bytes))
        .map_err(|e| format!("audio asset {} is not a valid WAV: {e}", path.display()))?;
    let spec = reader.spec();
    validate_spec(spec, sample_rate, path)?;

    let channels = usize::from(spec.channels);
    let bits = spec.bits_per_sample;
    let scale = 2_f64.powi(i32::from(bits) - 1);
    let max_frames = MAX_DECODED_BYTES
        .checked_sub(already_decoded)
        .ok_or_else(|| "decoded audio limit exceeded".to_string())?
        / std::mem::size_of::<[f64; 2]>();
    let declared_bytes = u64::from(reader.len()) * u64::from(bits / 8);
    if declared_bytes > metadata.len() {
        return Err(format!(
            "audio asset {} has truncated PCM data",
            path.display()
        ));
    }
    let declared_frames = usize::try_from(reader.duration())
        .map_err(|_| format!("audio asset {} frame count is too large", path.display()))?;
    if declared_frames > max_frames {
        return Err(format!(
            "decoded audio assets exceed {MAX_DECODED_BYTES} bytes"
        ));
    }
    let mut decoded = Vec::new();
    decoded
        .try_reserve_exact(declared_frames)
        .map_err(|_| format!("cannot allocate decoded audio for {}", path.display()))?;
    let mut pending = [0.0f64; 2];
    let mut channel_index = 0usize;
    for sample in reader.samples::<i32>() {
        let sample = sample.map_err(|e| {
            format!(
                "audio asset {} has truncated or invalid PCM data: {e}",
                path.display()
            )
        })?;
        pending[channel_index] = f64::from(sample) / scale;
        channel_index += 1;
        if channel_index == channels {
            if channels == 1 {
                pending[1] = pending[0];
            }
            if decoded.len() >= max_frames {
                return Err(format!(
                    "decoded audio assets exceed {MAX_DECODED_BYTES} bytes"
                ));
            }
            if decoded.len() >= declared_frames {
                return Err(format!(
                    "audio asset {} has an inconsistent WAV frame count",
                    path.display()
                ));
            }
            decoded.push(pending);
            channel_index = 0;
        }
    }
    if channel_index != 0 {
        return Err(format!(
            "audio asset {} ends with a partial PCM frame",
            path.display()
        ));
    }
    if decoded.len() != declared_frames {
        return Err(format!(
            "audio asset {} has an inconsistent WAV frame count",
            path.display()
        ));
    }
    let size = decoded
        .len()
        .checked_mul(std::mem::size_of::<[f64; 2]>())
        .ok_or_else(|| "decoded audio size overflow".to_string())?;
    Ok((decoded, size))
}

fn validate_spec(spec: WavSpec, sample_rate: u32, path: &Path) -> Result<(), String> {
    if spec.channels != 1 && spec.channels != 2 {
        return Err(format!(
            "audio asset {} must be mono or stereo",
            path.display()
        ));
    }
    if spec.sample_rate != sample_rate {
        return Err(format!(
            "audio asset {} sample rate {} does not match session rate {sample_rate}",
            path.display(),
            spec.sample_rate
        ));
    }
    if spec.sample_format != SampleFormat::Int || !matches!(spec.bits_per_sample, 16 | 24 | 32) {
        return Err(format!(
            "audio asset {} must use 16-, 24-, or 32-bit PCM",
            path.display()
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn aggregate_budget_is_checked_before_decoding_another_asset() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("one.wav");
        let mut writer = hound::WavWriter::create(
            &path,
            WavSpec {
                channels: 1,
                sample_rate: 8000,
                bits_per_sample: 16,
                sample_format: SampleFormat::Int,
            },
        )
        .unwrap();
        writer.write_sample(1_i16).unwrap();
        writer.finalize().unwrap();
        assert!(decode_file(&path, 8000, MAX_DECODED_BYTES).is_err());
        assert!(decode_file(&path, 8000, MAX_DECODED_BYTES - 16).is_ok());
    }
}
