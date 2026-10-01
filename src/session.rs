use serde::{Deserialize, Deserializer, Serialize};
use std::{collections::HashSet, path::PathBuf};

pub const SCHEMA_VERSION: u32 = 1;
pub const SCHEMA_VERSION_2: u32 = 2;
pub const SCHEMA_VERSION_3: u32 = 3;
pub const SCHEMA_VERSION_4: u32 = 4;
pub const SCHEMA_VERSION_5: u32 = 5;
pub const SCHEMA_VERSION_6: u32 = 6;
pub const SCHEMA_VERSION_7: u32 = 7;
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
pub const MAX_EFFECTS_PER_TRACK: usize = 16;
pub const MAX_EFFECT_GAIN: f64 = 4.0;
pub const MAX_AUTOMATION_LANES_PER_TRACK: usize = 16;
pub const MAX_AUTOMATION_POINTS: usize = 16_384;
pub const MAX_VST3_PLUGINS: usize = 8;
pub const MAX_VST3_PARAMETERS: usize = 64;
pub const MAX_VST3_STATE_BYTES: usize = 64 * 1024;
pub const MAX_VST3_SESSION_STATE_BYTES: usize = 256 * 1024;
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
    #[serde(skip)]
    pub asset_root: Option<PathBuf>,
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
    pub clips: Option<Vec<Clip>>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_nonnull"
    )]
    pub effects: Option<Vec<Effect>>,
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "deserialize_nonnull"
    )]
    pub automation: Option<Vec<AutomationLane>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AutomationLane {
    pub effect_id: String,
    pub parameter: AutomationParameter,
    pub interpolation: AutomationInterpolation,
    pub points: Vec<AutomationPoint>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AutomationParameter {
    Gain,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AutomationInterpolation {
    Step,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AutomationPoint {
    pub frame: u64,
    pub value: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Effect {
    Gain {
        id: String,
        gain: f64,
        bypass: bool,
    },
    Au {
        id: String,
        bypass: bool,
        component_type: String,
        component_subtype: String,
        component_manufacturer: String,
        state_hex: String,
        parameters: Vec<AuParameter>,
    },
    Vst3 {
        id: String,
        bypass: bool,
        bundle_path: String,
        class_id: String,
        state_hex: String,
        controller_state_hex: String,
        parameters: Vec<Vst3Parameter>,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AuParameter {
    pub id: u32,
    pub value: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Vst3Parameter {
    pub id: u32,
    pub value: f64,
    pub points: Vec<AutomationPoint>,
}

impl Effect {
    pub fn id(&self) -> &str {
        match self {
            Self::Gain { id, .. } => id,
            Self::Vst3 { id, .. } | Self::Au { id, .. } => id,
        }
    }
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
    Audio { gain: f64 },
    Supercollider(crate::sc_source::Source),
    Csound(crate::csound_source::Source),
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum Clip {
    Notes(NoteClip),
    Audio(AudioClip),
}

impl Clip {
    pub fn id(&self) -> &str {
        match self {
            Self::Notes(c) => &c.id,
            Self::Audio(c) => &c.id,
        }
    }
    pub fn start_frame(&self) -> u64 {
        match self {
            Self::Notes(c) => c.start_frame,
            Self::Audio(c) => c.start_frame,
        }
    }
    pub fn length_frames(&self) -> u64 {
        match self {
            Self::Notes(c) => c.length_frames,
            Self::Audio(c) => c.length_frames,
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum AudioClipKind {
    Audio,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct AudioClip {
    pub kind: AudioClipKind,
    pub id: String,
    pub start_frame: u64,
    pub length_frames: u64,
    pub source_path: String,
    pub source_offset_frames: u64,
    pub gain: f64,
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
            asset_root: None,
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

fn decode_hex_len(value: &str) -> Option<usize> {
    if value.len() % 2 != 0 || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        None
    } else {
        Some(value.len() / 2)
    }
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
        if !(SCHEMA_VERSION..=SCHEMA_VERSION_7).contains(&self.schema_version) {
            return Err(format!(
                "unsupported schema_version {}; expected 1, 2, 3, 4, 5, 6, or 7",
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
                    .any(|t| t.mode.is_some() || t.clips.is_some() || t.effects.is_some())
                || self.tracks.iter().any(|t| t.automation.is_some())
            {
                return Err("schema_version 1 does not accept timeline or effect fields".into());
            }
        } else if !self
            .tempo_milli_bpm
            .is_some_and(|t| (20_000..=300_000).contains(&t))
        {
            return Err(format!(
                "schema_version {} requires tempo_milli_bpm between 20000 and 300000",
                self.schema_version
            ));
        }

        let mut track_ids = HashSet::new();
        let mut total_clips = 0usize;
        let mut total_notes = 0usize;
        let mut total_automation_points = 0usize;
        let mut foreign_plugin_count = 0usize;
        let mut foreign_state_bytes = 0usize;
        let mut continuous_voices = 0usize;
        let mut sc_sources = 0;
        let mut sc_frames = 0;
        let mut sc_points = 0;
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
                Device::Audio { gain } => {
                    if !gain.is_finite() || !(MIN_GAIN..=MAX_GAIN).contains(&gain) {
                        return Err("gain must be finite and between 0 and 1".into());
                    }
                    if self.schema_version == SCHEMA_VERSION {
                        return Err("schema_version 1 does not support audio devices".into());
                    }
                }
                Device::Csound(_) => {}
                Device::Supercollider(ref source) => {
                    if self.schema_version < SCHEMA_VERSION_6 {
                        return Err(
                            "SuperCollider sources require schema_version 6 or later".into()
                        );
                    }
                    source.validate(self.sample_rate)?;
                    if track.mode != Some(TrackMode::Continuous) {
                        return Err(
                            "SuperCollider sources require continuous mode and empty clips".into(),
                        );
                    }
                    sc_sources += 1;
                    sc_frames += source.duration_frames;
                    sc_points += source
                        .controls
                        .iter()
                        .map(|c| c.points.len())
                        .sum::<usize>();
                }
            }
            if let Device::Csound(ref source) = track.device {
                if self.schema_version != SCHEMA_VERSION_7 {
                    return Err("Csound sources require schema_version 7".into());
                }
                source.validate(self.sample_rate)?;
                if track.mode != Some(TrackMode::Continuous) {
                    return Err("Csound sources require continuous mode and empty clips".into());
                }
                sc_sources += 1;
                sc_frames += source.duration_frames;
                sc_points += source
                    .controls
                    .iter()
                    .map(|c| c.points.len())
                    .sum::<usize>();
            }
            if sc_sources > crate::sc_source::MAX_SOURCES
                || sc_frames > u64::from(self.sample_rate) * 10
                || sc_points > crate::sc_source::MAX_POINTS
            {
                return Err("runtime session exceeds four sources, ten total source seconds or 512 control points".into());
            }
            if self.schema_version == SCHEMA_VERSION {
                continue;
            }
            match (self.schema_version, &track.effects) {
                (SCHEMA_VERSION_2, Some(_)) => {
                    return Err("schema_version 2 does not accept effects".into());
                }
                (
                    SCHEMA_VERSION_3 | SCHEMA_VERSION_4 | SCHEMA_VERSION_5 | SCHEMA_VERSION_6
                    | SCHEMA_VERSION_7,
                    None,
                ) => {
                    return Err(format!(
                        "schema_version {} requires track effects",
                        self.schema_version
                    ));
                }
                _ => {}
            }
            if self.schema_version == SCHEMA_VERSION_2 && track.automation.is_some() {
                return Err("schema_version 2 does not accept automation".into());
            }
            if let Some(effects) = &track.effects {
                if effects.len() > MAX_EFFECTS_PER_TRACK {
                    return Err(format!(
                        "at most {MAX_EFFECTS_PER_TRACK} effects per track are supported"
                    ));
                }
                let mut effect_ids = HashSet::new();
                for effect in effects {
                    if !valid_id(effect.id()) || !effect_ids.insert(effect.id()) {
                        return Err("effect IDs must be unique within a track, nonempty, and at most 128 UTF-8 bytes".into());
                    }
                    match effect {
                        Effect::Gain { gain, .. }
                            if !gain.is_finite()
                                || !(MIN_GAIN..=MAX_EFFECT_GAIN).contains(gain) =>
                        {
                            return Err(format!(
                                "effect gain must be finite and between 0 and {MAX_EFFECT_GAIN}"
                            ));
                        }
                        Effect::Gain { .. } => {}
                        Effect::Au { .. } if self.schema_version < SCHEMA_VERSION_5 => {
                            return Err("AU effects require schema_version 5 or later".into());
                        }
                        Effect::Au {
                            component_type,
                            component_subtype,
                            component_manufacturer,
                            state_hex,
                            parameters,
                            ..
                        } => {
                            for identity in
                                [component_type, component_subtype, component_manufacturer]
                            {
                                if identity.len() != 4
                                    || !identity.bytes().all(|b| (32..=126).contains(&b))
                                {
                                    return Err("AU component identifiers must be exactly four printable ASCII characters".into());
                                }
                            }
                            let decoded = decode_hex_len(state_hex)
                                .ok_or("AU state must be even-length hexadecimal")?;
                            if decoded > MAX_VST3_STATE_BYTES {
                                return Err("AU state exceeds 64 KiB".into());
                            }
                            foreign_state_bytes += decoded;
                            foreign_plugin_count += 1;
                            if foreign_state_bytes > MAX_VST3_SESSION_STATE_BYTES
                                || foreign_plugin_count > MAX_VST3_PLUGINS
                            {
                                return Err("session supports at most 8 foreign effects and 256 KiB aggregate state".into());
                            }
                            if parameters.len() > MAX_VST3_PARAMETERS {
                                return Err("at most 64 AU parameters per effect".into());
                            }
                            let mut ids = HashSet::new();
                            for parameter in parameters {
                                if !ids.insert(parameter.id)
                                    || !parameter.value.is_finite()
                                    || !(parameter.value as f32).is_finite()
                                {
                                    return Err("AU parameter IDs must be unique and values finite float32 native values".into());
                                }
                            }
                        }
                        Effect::Vst3 { .. } if self.schema_version < SCHEMA_VERSION_4 => {
                            return Err("VST3 effects require schema_version 4 or later".into());
                        }
                        Effect::Vst3 {
                            bundle_path,
                            class_id,
                            state_hex,
                            controller_state_hex,
                            parameters,
                            ..
                        } => {
                            if bundle_path.len() > 4096
                                || !std::path::Path::new(bundle_path).is_absolute()
                                || !bundle_path.ends_with(".vst3")
                            {
                                return Err("VST3 bundle_path must be an absolute .vst3 path of at most 4096 UTF-8 bytes".into());
                            }
                            if class_id.len() != 32
                                || !class_id
                                    .bytes()
                                    .all(|b| b.is_ascii_digit() || (b'A'..=b'F').contains(&b))
                            {
                                return Err("VST3 class_id must be exactly 32 uppercase hexadecimal characters".into());
                            }
                            for state in [state_hex, controller_state_hex] {
                                let decoded = decode_hex_len(state).ok_or_else(|| {
                                    "VST3 state must be even-length hexadecimal".to_string()
                                })?;
                                if decoded > MAX_VST3_STATE_BYTES {
                                    return Err("VST3 state exceeds 64 KiB".into());
                                }
                                foreign_state_bytes = foreign_state_bytes
                                    .checked_add(decoded)
                                    .ok_or_else(|| "VST3 state byte count overflow".to_string())?;
                                if foreign_state_bytes > MAX_VST3_SESSION_STATE_BYTES {
                                    return Err("foreign session state exceeds 256 KiB".into());
                                }
                            }
                            foreign_plugin_count += 1;
                            if foreign_plugin_count > MAX_VST3_PLUGINS {
                                return Err(
                                    "at most 8 foreign effects are supported per session".into()
                                );
                            }
                            if parameters.len() > MAX_VST3_PARAMETERS {
                                return Err(
                                    "at most 64 VST3 parameters are supported per effect".into()
                                );
                            }
                            let mut parameter_ids = HashSet::new();
                            for parameter in parameters {
                                if !parameter_ids.insert(parameter.id) {
                                    return Err(
                                        "VST3 parameter IDs must be unique within an effect".into(),
                                    );
                                }
                                if !parameter.value.is_finite()
                                    || !(0.0..=1.0).contains(&parameter.value)
                                {
                                    return Err("VST3 parameter value must be finite and normalized between 0 and 1".into());
                                }
                                total_automation_points = total_automation_points
                                    .checked_add(parameter.points.len())
                                    .ok_or_else(|| "automation point count overflow".to_string())?;
                                if total_automation_points > MAX_AUTOMATION_POINTS {
                                    return Err(format!(
                                        "at most {MAX_AUTOMATION_POINTS} automation points are supported"
                                    ));
                                }
                                let mut previous_frame = None;
                                for point in &parameter.points {
                                    if point.frame > MAX_FRAME {
                                        return Err(
                                            "VST3 automation frame exceeds MAX_FRAME".into()
                                        );
                                    }
                                    if previous_frame.is_some_and(|frame| point.frame <= frame) {
                                        return Err("VST3 automation point frames must be strictly increasing".into());
                                    }
                                    if !point.value.is_finite()
                                        || !(0.0..=1.0).contains(&point.value)
                                    {
                                        return Err("VST3 automation value must be finite and normalized between 0 and 1".into());
                                    }
                                    previous_frame = Some(point.frame);
                                }
                            }
                        }
                    }
                }
            }
            if let Some(lanes) = &track.automation {
                if lanes.len() > MAX_AUTOMATION_LANES_PER_TRACK {
                    return Err(format!(
                        "at most {MAX_AUTOMATION_LANES_PER_TRACK} automation lanes per track are supported"
                    ));
                }
                let effects = track
                    .effects
                    .as_ref()
                    .ok_or_else(|| "automation requires track effects".to_string())?;
                let mut lane_targets = HashSet::new();
                for lane in lanes {
                    if !lane_targets.insert(lane.effect_id.as_str()) {
                        return Err("each effect can have at most one automation lane".into());
                    }
                    if !effects.iter().any(
                        |effect| matches!(effect, Effect::Gain { id, .. } if id == &lane.effect_id),
                    ) {
                        return Err("automation effect_id must target an existing effect".into());
                    }
                    if lane.points.is_empty() {
                        return Err("automation lanes must contain at least one point".into());
                    }
                    total_automation_points = total_automation_points
                        .checked_add(lane.points.len())
                        .ok_or_else(|| "automation point count overflow".to_string())?;
                    if total_automation_points > MAX_AUTOMATION_POINTS {
                        return Err(format!(
                            "at most {MAX_AUTOMATION_POINTS} automation points are supported"
                        ));
                    }
                    let mut previous_frame = None;
                    for point in &lane.points {
                        if point.frame > MAX_FRAME {
                            return Err("automation frame exceeds MAX_FRAME".into());
                        }
                        if previous_frame.is_some_and(|frame| point.frame <= frame) {
                            return Err(
                                "automation point frames must be strictly increasing".into()
                            );
                        }
                        if !point.value.is_finite()
                            || !(MIN_GAIN..=MAX_EFFECT_GAIN).contains(&point.value)
                        {
                            return Err(format!(
                                "automation value must be finite and between 0 and {MAX_EFFECT_GAIN}"
                            ));
                        }
                        previous_frame = Some(point.frame);
                    }
                }
            }
            if track.effects.as_ref().is_some_and(|effects| {
                effects
                    .iter()
                    .any(|e| matches!(e, Effect::Vst3 { .. } | Effect::Au { .. }))
            }) && self.sample_rate != 48_000
            {
                return Err("foreign effects require a 48000 Hz session sample rate".into());
            }
            let mode = track.mode.ok_or_else(|| {
                format!("schema_version {} requires track mode", self.schema_version)
            })?;
            let clips = track.clips.as_ref().ok_or_else(|| {
                format!(
                    "schema_version {} requires track clips",
                    self.schema_version
                )
            })?;
            match mode {
                TrackMode::Continuous => {
                    if !matches!(
                        &track.device,
                        Device::Sine { .. } | Device::Supercollider(_) | Device::Csound(_)
                    ) {
                        return Err("audio tracks require sequenced mode".into());
                    }
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
                if !valid_id(clip.id()) || !clip_ids.insert(clip.id()) {
                    return Err(
                        "clip IDs must be unique within a track and at most 128 UTF-8 bytes".into(),
                    );
                }
                let clip_end = checked_end(clip.start_frame(), clip.length_frames(), "clip")?;
                match (clip, &track.device) {
                    (Clip::Notes(_), Device::Sine { .. }) => {}
                    (Clip::Audio(audio), Device::Audio { .. }) => {
                        if audio.source_path.is_empty()
                            || audio.source_path.len() > 4096
                            || audio.source_path.contains('\\')
                            || audio.source_path.contains(':')
                            || std::path::Path::new(&audio.source_path)
                                .components()
                                .any(|c| !matches!(c, std::path::Component::Normal(_)))
                        {
                            return Err(
                                "audio source_path must be a relative path with normal components"
                                    .into(),
                            );
                        }
                        if !audio.gain.is_finite() || !(MIN_GAIN..=MAX_GAIN).contains(&audio.gain) {
                            return Err("audio clip gain must be finite and between 0 and 1".into());
                        }
                        let source_end = audio
                            .source_offset_frames
                            .checked_add(audio.length_frames)
                            .ok_or_else(|| "audio source range overflows".to_string())?;
                        if source_end > MAX_FRAME {
                            return Err("audio source range exceeds MAX_FRAME".into());
                        }
                        lifetimes.push((audio.start_frame, 1));
                        lifetimes.push((clip_end, -1));
                    }
                    _ => return Err(
                        "notes clips require sine devices and audio clips require audio devices"
                            .into(),
                    ),
                }
                if let Clip::Audio(_) = clip {
                    continue;
                }
                let Clip::Notes(clip) = clip else {
                    unreachable!()
                };
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

    pub fn has_audio(&self) -> bool {
        self.tracks
            .iter()
            .any(|track| matches!(&track.device, Device::Audio { .. }))
    }
}
