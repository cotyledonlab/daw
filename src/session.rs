use serde::{Deserialize, Deserializer, Serialize};
use std::collections::HashSet;

pub const SCHEMA_VERSION: u32 = 1;
pub const SCHEMA_VERSION_2: u32 = 2;
pub const MAX_TRACKS: usize = 64;
pub const MIN_SAMPLE_RATE: u32 = 8_000;
pub const MAX_SAMPLE_RATE: u32 = 192_000;
pub const DEFAULT_SAMPLE_RATE: u32 = 48_000;
pub const MAX_TRACK_ID_BYTES: usize = 128;
pub const MIN_GAIN: f64 = 0.0;
pub const MAX_GAIN: f64 = 1.0;
pub const MAX_FRAME: u64 = 9_007_199_254_740_991;
pub const MAX_CLIPS: usize = 1_024;
pub const MAX_NOTES: usize = 16_384;
pub const MAX_VOICES: usize = 64;
pub const DEFAULT_TEMPO_MILLI_BPM: u32 = 120_000;

fn deserialize_nonnull<'de, T, D>(deserializer: D) -> Result<Option<T>, D::Error>
where
    T: Deserialize<'de>,
    D: Deserializer<'de>,
{
    T::deserialize(deserializer).map(Some)
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Session {
    pub schema_version: u32,
    pub sample_rate: u32,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_nonnull"
    )]
    pub tempo_milli_bpm: Option<u32>,
    pub tracks: Vec<Track>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Track {
    pub id: String,
    pub device: Device,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_nonnull"
    )]
    pub mode: Option<TrackMode>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_nonnull"
    )]
    pub clips: Option<Vec<NoteClip>>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum TrackMode {
    Continuous,
    Sequenced,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Device {
    Sine { frequency_hz: f64, gain: f64 },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct NoteClip {
    pub kind: ClipKind,
    pub id: String,
    pub start_frame: u64,
    pub length_frames: u64,
    pub notes: Vec<Note>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ClipKind {
    Notes,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Note {
    pub id: String,
    pub start_frame: u64,
    pub duration_frames: u64,
    pub frequency_hz: f64,
    pub velocity: f64,
}

impl Default for Session {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            sample_rate: DEFAULT_SAMPLE_RATE,
            tempo_milli_bpm: None,
            tracks: vec![],
        }
    }
}

/// Number of frames in the fixed five-millisecond attack or release, rounded half-up.
pub fn envelope_frames(rate: u32) -> u64 {
    (rate as u64 + 100) / 200
}

/// Convert absolute 960-PPQ ticks to frames with exact integer half-up rounding.
pub fn tick_to_frame(ticks: u64, rate: u32, tempo: u32) -> Result<u64, String> {
    if !(MIN_SAMPLE_RATE..=MAX_SAMPLE_RATE).contains(&rate) || !(20_000..=300_000).contains(&tempo)
    {
        return Err("tick conversion requires a supported sample rate and tempo".into());
    }
    if ticks > MAX_FRAME {
        return Err("ticks exceed MAX_FRAME".into());
    }
    let numerator = (ticks as u128)
        .checked_mul(rate as u128)
        .and_then(|v| v.checked_mul(60_000))
        .ok_or_else(|| "tick conversion numerator overflow".to_string())?;
    let denominator = 960u128
        .checked_mul(tempo as u128)
        .ok_or_else(|| "tick conversion denominator overflow".to_string())?;
    let twice_denominator = denominator
        .checked_mul(2)
        .ok_or_else(|| "tick conversion rounding overflow".to_string())?;
    let rounded = numerator
        .checked_mul(2)
        .and_then(|v| v.checked_add(denominator))
        .ok_or_else(|| "tick conversion rounding overflow".to_string())?
        / twice_denominator;
    let frame = u64::try_from(rounded).map_err(|_| "tick conversion frame overflow".to_string())?;
    if frame > MAX_FRAME {
        return Err("tick conversion frame exceeds MAX_FRAME".into());
    }
    Ok(frame)
}

fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= MAX_TRACK_ID_BYTES
}

fn checked_end(start: u64, length: u64, label: &str) -> Result<u64, String> {
    if length == 0 {
        return Err(format!("{label} length must be positive"));
    }
    let end = start
        .checked_add(length)
        .ok_or_else(|| format!("{label} end overflows"))?;
    if end > MAX_FRAME {
        return Err(format!("{label} end exceeds MAX_FRAME"));
    }
    Ok(end)
}

impl Session {
    pub fn validate(&self) -> Result<(), String> {
        if !(SCHEMA_VERSION..=SCHEMA_VERSION_2).contains(&self.schema_version) {
            return Err(format!(
                "unsupported schema_version {}; expected 1 or 2",
                self.schema_version
            ));
        }
        if !(MIN_SAMPLE_RATE..=MAX_SAMPLE_RATE).contains(&self.sample_rate) {
            return Err("sample_rate must be between 8000 and 192000".into());
        }
        if self.tracks.len() > MAX_TRACKS {
            return Err(format!("at most {MAX_TRACKS} tracks are supported"));
        }
        if self.schema_version == SCHEMA_VERSION {
            if self.tempo_milli_bpm.is_some()
                || self
                    .tracks
                    .iter()
                    .any(|t| t.mode.is_some() || t.clips.is_some())
            {
                return Err("schema_version 1 does not accept timeline fields".into());
            }
        } else if !self
            .tempo_milli_bpm
            .is_some_and(|t| (20_000..=300_000).contains(&t))
        {
            return Err(
                "schema_version 2 requires tempo_milli_bpm between 20000 and 300000".into(),
            );
        }

        let mut track_ids = HashSet::new();
        let mut total_clips = 0usize;
        let mut total_notes = 0usize;
        let mut continuous_voices = 0usize;
        let mut lifetimes: Vec<(u64, i32)> = Vec::new();
        for track in &self.tracks {
            if !valid_id(&track.id) || !track_ids.insert(&track.id) {
                return Err(
                    "track IDs must be unique, nonempty, and at most 128 UTF-8 bytes".into(),
                );
            }
            match track.device {
                Device::Sine { frequency_hz, gain } => {
                    if !frequency_hz.is_finite()
                        || frequency_hz <= 0.0
                        || frequency_hz >= self.sample_rate as f64 / 2.0
                    {
                        return Err("frequency_hz must be positive and below Nyquist".into());
                    }
                    if !gain.is_finite() || !(MIN_GAIN..=MAX_GAIN).contains(&gain) {
                        return Err("gain must be finite and between 0 and 1".into());
                    }
                }
            }
            if self.schema_version == SCHEMA_VERSION {
                continue;
            }
            let mode = track
                .mode
                .ok_or_else(|| "schema_version 2 requires track mode".to_string())?;
            let clips = track
                .clips
                .as_ref()
                .ok_or_else(|| "schema_version 2 requires track clips".to_string())?;
            match mode {
                TrackMode::Continuous => {
                    continuous_voices += 1;
                    if !clips.is_empty() {
                        return Err("continuous tracks require empty clips".into());
                    }
                }
                TrackMode::Sequenced => {}
            }
            total_clips = total_clips
                .checked_add(clips.len())
                .ok_or_else(|| "clip count overflow".to_string())?;
            if total_clips > MAX_CLIPS {
                return Err(format!("at most {MAX_CLIPS} clips are supported"));
            }
            let mut clip_ids = HashSet::new();
            for clip in clips {
                if !valid_id(&clip.id) || !clip_ids.insert(&clip.id) {
                    return Err(
                        "clip IDs must be unique within a track and at most 128 UTF-8 bytes".into(),
                    );
                }
                let clip_end = checked_end(clip.start_frame, clip.length_frames, "clip")?;
                total_notes = total_notes
                    .checked_add(clip.notes.len())
                    .ok_or_else(|| "note count overflow".to_string())?;
                if total_notes > MAX_NOTES {
                    return Err(format!("at most {MAX_NOTES} notes are supported"));
                }
                let mut note_ids = HashSet::new();
                for note in &clip.notes {
                    if !valid_id(&note.id) || !note_ids.insert(&note.id) {
                        return Err(
                            "note IDs must be unique within a clip and at most 128 UTF-8 bytes"
                                .into(),
                        );
                    }
                    if note.start_frame >= clip.length_frames {
                        return Err("note start_frame must be inside its clip".into());
                    }
                    let note_end = checked_end(note.start_frame, note.duration_frames, "note")?;
                    if note_end > clip.length_frames {
                        return Err("note gate must end within its clip".into());
                    }
                    if !note.frequency_hz.is_finite()
                        || note.frequency_hz <= 0.0
                        || note.frequency_hz >= self.sample_rate as f64 / 2.0
                    {
                        return Err("note frequency_hz must be positive and below Nyquist".into());
                    }
                    if !note.velocity.is_finite() || !(0.0..=1.0).contains(&note.velocity) {
                        return Err("note velocity must be finite and between 0 and 1".into());
                    }
                    let absolute_start = clip
                        .start_frame
                        .checked_add(note.start_frame)
                        .ok_or_else(|| "note start overflows".to_string())?;
                    let absolute_off = clip
                        .start_frame
                        .checked_add(note_end)
                        .ok_or_else(|| "note end overflows".to_string())?;
                    let release_end = absolute_off
                        .checked_add(envelope_frames(self.sample_rate))
                        .ok_or_else(|| "note release end overflows".to_string())?;
                    if release_end > MAX_FRAME {
                        return Err("note release end exceeds MAX_FRAME".into());
                    }
                    let lifetime_end = release_end.min(clip_end);
                    if mode == TrackMode::Sequenced {
                        lifetimes.push((absolute_start, 1));
                        lifetimes.push((lifetime_end, -1));
                    }
                }
            }
        }
        if total_clips > MAX_CLIPS {
            return Err(format!("at most {MAX_CLIPS} clips are supported"));
        }
        if total_notes > MAX_NOTES {
            return Err(format!("at most {MAX_NOTES} notes are supported"));
        }
        lifetimes.sort_by_key(|&(frame, delta)| (frame, delta)); // -1 release/free precedes +1 note-on.
        let mut voices = continuous_voices;
        if voices > MAX_VOICES {
            return Err(format!("polyphony exceeds {MAX_VOICES} voices"));
        }
        for (_, delta) in lifetimes {
            if delta < 0 {
                voices -= 1;
            } else {
                voices += 1;
            }
            if voices > MAX_VOICES {
                return Err(format!("polyphony exceeds {MAX_VOICES} voices"));
            }
        }
        Ok(())
    }
}
