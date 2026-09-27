use serde::{Deserialize, Serialize};
use std::collections::HashSet;

pub const SCHEMA_VERSION: u32 = 1;
pub const MAX_TRACKS: usize = 64;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Session {
    pub schema_version: u32,
    pub sample_rate: u32,
    pub tracks: Vec<Track>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Track {
    pub id: String,
    pub device: Device,
}

/// Persisted device descriptions. Foreign formats are deliberately not placeholders:
/// introduce their descriptors only when a working adapter establishes the contract.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Device {
    Sine { frequency_hz: f64, gain: f64 },
}

impl Default for Session {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            sample_rate: 48_000,
            tracks: vec![],
        }
    }
}

impl Session {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(format!(
                "unsupported schema_version {}; expected {SCHEMA_VERSION}",
                self.schema_version
            ));
        }
        if !(8_000..=192_000).contains(&self.sample_rate) {
            return Err("sample_rate must be between 8000 and 192000".into());
        }
        if self.tracks.len() > MAX_TRACKS {
            return Err(format!("at most {MAX_TRACKS} tracks are supported"));
        }
        let mut ids = HashSet::new();
        for track in &self.tracks {
            if track.id.is_empty() || track.id.len() > 128 || !ids.insert(&track.id) {
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
                    if !gain.is_finite() || !(0.0..=1.0).contains(&gain) {
                        return Err("gain must be finite and between 0 and 1".into());
                    }
                }
            }
        }
        Ok(())
    }
}
