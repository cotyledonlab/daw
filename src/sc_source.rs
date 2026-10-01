//! Saved SuperCollider programs prepared as owned, finite PCM sources.
use crate::{
    assets::PreparedAudioClip,
    session::{Device, Session},
    synthdef,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashSet},
    sync::Arc,
};

pub const MAX_SOURCES: usize = 4;
pub const MAX_PROGRAM_BYTES: usize = 60 * 1024;
pub const MAX_POINTS: usize = 512;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub synthdef_hex: String,
    pub synth_name: String,
    pub duration_frames: u64,
    pub gain: f64,
    pub controls: Vec<Control>,
    #[serde(skip)]
    pub prepared: Option<Arc<Prepared>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Control {
    pub name: String,
    pub values: Vec<f64>,
    pub points: Vec<Point>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub frame: u64,
    pub values: Vec<f64>,
}

// Fields are private: callers cannot fabricate an apparently prepared cache.
#[derive(Debug, PartialEq)]
pub struct Prepared {
    key: Vec<u8>,
    sample_rate: u32,
    audio: Arc<Vec<[f64; 2]>>,
}

impl Source {
    pub fn validate(&self, rate: u32) -> Result<(), String> {
        let bytes = synthdef::decode_hex(&self.synthdef_hex)?;
        if bytes.len() > MAX_PROGRAM_BYTES {
            return Err("saved SynthDef exceeds 60 KiB".into());
        }
        let metadata = synthdef::inspect(&bytes)?;
        if self.synth_name != metadata.name {
            return Err("synth_name does not match embedded SynthDef".into());
        }
        if self.duration_frames < u64::from(rate).div_ceil(1000)
            || self.duration_frames > u64::from(rate) * 10
        {
            return Err("SuperCollider duration must be between 0.001 and 10 seconds".into());
        }
        if !self.gain.is_finite() || !(0.0..=1.0).contains(&self.gain) {
            return Err("SuperCollider track gain must be finite and between 0 and 1".into());
        }
        if self.controls.len() > synthdef::MAX_CONTROLS {
            return Err("too many SuperCollider controls".into());
        }
        let mut names = HashSet::new();
        let mut points = 0;
        for control in &self.controls {
            if !names.insert(&control.name) {
                return Err("SuperCollider control names must be unique".into());
            }
            let native = metadata
                .controls
                .iter()
                .find(|c| c.name == control.name)
                .ok_or("SuperCollider control is absent from SynthDef")?;
            let check = |values: &[f64]| {
                if values.len() != native.default_values.len()
                    || values
                        .iter()
                        .any(|v| !v.is_finite() || !(*v as f32).is_finite())
                {
                    Err("SuperCollider control values must match native array length and be finite float32".to_string())
                } else {
                    Ok(())
                }
            };
            check(&control.values)?;
            let mut previous = None;
            for point in &control.points {
                check(&point.values)?;
                if point.frame > 0
                    && metadata.scalar_parameters
                        [native.index..native.index + native.default_values.len()]
                        .iter()
                        .any(|scalar| *scalar)
                {
                    return Err(
                        "initialization-rate controls cannot have points after frame zero".into(),
                    );
                }
                if point.frame >= self.duration_frames || previous.is_some_and(|p| point.frame <= p)
                {
                    return Err(
                        "SuperCollider point frames must strictly increase within source duration"
                            .into(),
                    );
                }
                previous = Some(point.frame);
            }
            points += control.points.len();
        }
        if points > MAX_POINTS {
            return Err("SuperCollider source exceeds 512 control points".into());
        }
        Ok(())
    }

    fn key(&self) -> Result<Vec<u8>, String> {
        // Track gain belongs to the DAW chain, not the runtime program.
        let mut source = self.clone();
        source.gain = 1.0;
        serde_json::to_vec(&source)
            .map_err(|e| format!("cannot identify SuperCollider source: {e}"))
    }

    fn prepare(&self, rate: u32) -> Result<Arc<Prepared>, String> {
        self.validate(rate)?;
        let key = self.key()?;
        if let Some(prepared) = &self.prepared {
            if prepared.sample_rate == rate && prepared.key == key {
                return Ok(Arc::clone(prepared));
            }
        }
        let metadata = synthdef::inspect(&synthdef::decode_hex(&self.synthdef_hex)?)?;
        let mut events = BTreeMap::<u64, Vec<Vec<u8>>>::new();
        events.entry(0).or_default().extend([
            blob_message("/d_recv", &synthdef::decode_hex(&self.synthdef_hex)?),
            int_message("/g_new", &[1, 0, 0]),
            synth_message(self, &metadata),
        ]);
        for control in &self.controls {
            let index = metadata
                .controls
                .iter()
                .find(|c| c.name == control.name)
                .unwrap()
                .index;
            for point in &control.points {
                if point.frame == 0 {
                    continue;
                }
                events
                    .entry(point.frame)
                    .or_default()
                    .push(set_message(index, &point.values));
            }
        }
        events.entry(self.duration_frames).or_default().extend([
            int_message("/n_free", &[1000]),
            int_message("/c_set", &[0, 0]),
        ]);
        let mut score = Vec::new();
        for (frame, messages) in events {
            // Separate large definition and control messages into same-time bundles.
            // NRT runs in order, and d_recv is synchronous in the NRT server.
            for message in messages {
                let mut packet = b"#bundle\0".to_vec();
                let ticks = ((u128::from(frame) << 32).div_ceil(u128::from(rate))) as u64;
                packet.extend_from_slice(&ticks.to_be_bytes());
                packet.extend_from_slice(&(message.len() as u32).to_be_bytes());
                packet.extend_from_slice(&message);
                score.extend_from_slice(&(packet.len() as u32).to_be_bytes());
                score.extend_from_slice(&packet);
            }
        }
        let temp = tempfile::tempdir().map_err(|e| format!("source job directory failed: {e}"))?;
        let path = temp.path().join("source.osc");
        std::fs::write(&path, score).map_err(|e| format!("source score write failed: {e}"))?;
        let audio = crate::supercollider::render_float(&path, rate)?;
        if audio.len() as u64 != self.duration_frames {
            return Err("prepared SuperCollider audio length differs from saved duration".into());
        }
        Ok(Arc::new(Prepared {
            key,
            sample_rate: rate,
            audio: Arc::new(audio),
        }))
    }
}

pub fn prepare_session(session: &mut Session) -> Result<(), String> {
    session.validate()?;
    if !session
        .tracks
        .iter()
        .any(|track| matches!(track.device, Device::Supercollider(_)))
    {
        return Ok(());
    }
    if serde_json::to_vec_pretty(session)
        .map_err(|e| e.to_string())?
        .len()
        > crate::control::MAX_MESSAGE_BYTES - 4096
    {
        return Err("SuperCollider session exceeds the save/load byte limit".into());
    }
    let assets = crate::assets::prepare_with_budget(session, decoded_bytes(session))?;
    for track in &mut session.tracks {
        if let Device::Supercollider(source) = &mut track.device {
            source.prepared = Some(source.prepare(session.sample_rate)?);
        }
    }
    // Audio assets and runtime PCM share the decoded-session budget.
    let runtimes = prepare_clips(session)?;
    check_budget(assets.iter().chain(&runtimes))
}

pub(crate) fn decoded_bytes(session: &Session) -> usize {
    session
        .tracks
        .iter()
        .map(|track| match &track.device {
            Device::Supercollider(source) => {
                source.duration_frames as usize * std::mem::size_of::<[f64; 2]>()
            }
            _ => 0,
        })
        .sum()
}

pub fn prepare_clips(session: &Session) -> Result<Vec<PreparedAudioClip>, String> {
    let mut clips = Vec::new();
    for (track_index, track) in session.tracks.iter().enumerate() {
        if let Device::Supercollider(source) = &track.device {
            let prepared = source.prepare(session.sample_rate)?;
            clips.push(PreparedAudioClip {
                track_index,
                start: 0,
                end: source.duration_frames,
                source_offset: 0,
                gain: source.gain,
                frames: Arc::clone(&prepared.audio),
            });
        }
    }
    Ok(clips)
}

pub fn check_budget<'a>(clips: impl Iterator<Item = &'a PreparedAudioClip>) -> Result<(), String> {
    let mut unique = HashSet::new();
    let mut bytes = 0;
    for clip in clips {
        if unique.insert(Arc::as_ptr(&clip.frames)) {
            bytes += clip.frames.len() * std::mem::size_of::<[f64; 2]>();
        }
    }
    if bytes > crate::assets::MAX_DECODED_BYTES {
        Err("combined assets and runtime audio exceed decoded session budget".into())
    } else {
        Ok(())
    }
}

fn string(value: &str) -> Vec<u8> {
    let mut data = value.as_bytes().to_vec();
    data.push(0);
    data.resize(data.len().next_multiple_of(4), 0);
    data
}
fn header(address: &str, tags: &str) -> Vec<u8> {
    let mut result = string(address);
    result.extend(string(tags));
    result
}
fn int_message(address: &str, values: &[i32]) -> Vec<u8> {
    let mut result = header(address, &format!(",{}", "i".repeat(values.len())));
    for value in values {
        result.extend(value.to_be_bytes());
    }
    result
}
fn blob_message(address: &str, blob: &[u8]) -> Vec<u8> {
    let mut result = header(address, ",b");
    result.extend((blob.len() as u32).to_be_bytes());
    result.extend(blob);
    result.resize(result.len().next_multiple_of(4), 0);
    result
}
fn synth_message(source: &Source, metadata: &synthdef::Program) -> Vec<u8> {
    let count: usize = source.controls.iter().map(|c| c.values.len()).sum();
    let mut result = header("/s_new", &format!(",siii{}", "if".repeat(count)));
    result.extend(string(&source.synth_name));
    for value in [1000_i32, 0, 1] {
        result.extend(value.to_be_bytes());
    }
    for control in &source.controls {
        let index = metadata
            .controls
            .iter()
            .find(|c| c.name == control.name)
            .unwrap()
            .index;
        let values = control
            .points
            .first()
            .filter(|point| point.frame == 0)
            .map_or(&control.values, |point| &point.values);
        for (offset, value) in values.iter().enumerate() {
            result.extend(((index + offset) as i32).to_be_bytes());
            result.extend((*value as f32).to_be_bytes());
        }
    }
    result
}
fn set_message(index: usize, values: &[f64]) -> Vec<u8> {
    let mut result = header("/n_setn", &format!(",iii{}", "f".repeat(values.len())));
    for value in [1000_i32, index as i32, values.len() as i32] {
        result.extend(value.to_be_bytes());
    }
    for value in values {
        result.extend((*value as f32).to_be_bytes());
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cached_runtime_pcm_obeys_track_gain_chain_duration_seek_and_loop() {
        let mut program = b"SCgf\0\0\0\x02\0\x01\x01x".to_vec();
        program.extend_from_slice(&[0; 18]);
        let hex: String = program.iter().map(|byte| format!("{byte:02x}")).collect();
        let mut session: Session = serde_json::from_value(serde_json::json!({
            "schema_version":6,"sample_rate":8000,"tempo_milli_bpm":120000,
            "tracks":[{"id":"runtime","mode":"continuous","clips":[],
                "effects":[{"kind":"gain","id":"level","gain":2.0,"bypass":false}],
                "device":{"kind":"supercollider","synthdef_hex":hex,"synth_name":"x",
                    "duration_frames":80,"gain":0.5,"controls":[]}}]
        }))
        .unwrap();
        let Device::Supercollider(source) = &mut session.tracks[0].device else {
            unreachable!()
        };
        source.prepared = Some(Arc::new(Prepared {
            key: source.key().unwrap(),
            sample_rate: 8000,
            audio: Arc::new(vec![[0.5, -0.25]; 80]),
        }));
        let original = source.prepare(8000).unwrap();
        source.gain = 0.25;
        assert!(Arc::ptr_eq(&original, &source.prepare(8000).unwrap()));
        let mut engine = crate::engine::Engine::prepare(&session).unwrap();
        let mut frames = [[0.0; 2]; 81];
        engine.render_block(&mut frames);
        assert_eq!(frames[0], [0.25, -0.125]);
        assert_eq!(frames[79], frames[0]);
        assert_eq!(frames[80], [0.0; 2]);
        engine.seek(40).unwrap();
        engine.render_block(&mut frames[..1]);
        assert_eq!(frames[0], [0.25, -0.125]);
        engine.set_loop(Some((79, 80))).unwrap();
        engine.seek(79).unwrap();
        engine.render_block(&mut frames);
        assert!(frames.iter().all(|frame| *frame == [0.25, -0.125]));
    }
}
