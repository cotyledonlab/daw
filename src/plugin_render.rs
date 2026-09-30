//! Bounded offline rendering for sessions containing the VST3 adapter.
//!
//! Plugin instances are created and run while preparing this snapshot, before
//! the caller opens an output file. The resulting samples are ordinary owned
//! stereo audio and encoding does not touch the plugin host.

use crate::{
    engine::Engine,
    render::{self, RenderReport},
    session::{Effect, Session},
};
use std::io::{Seek, Write};

const MAX_SECONDS: f64 = 10.0;

/// A fully rendered, unclipped stereo mix ready for PCM16 WAV encoding.
#[derive(Debug)]
pub struct PreparedPluginRender {
    sample_rate: u32,
    frames: u64,
    mix: Vec<[f64; 2]>,
}

/// Prepare each track independently, apply its serial effect chain, then sum
/// the processed stems without clipping. The session is validated before any
/// host work and each plugin is processed before the caller creates output.
pub fn prepare(session: &Session, seconds: f64) -> Result<PreparedPluginRender, String> {
    session.validate()?;
    if !seconds.is_finite() || !(render::MIN_SECONDS..=MAX_SECONDS).contains(&seconds) {
        return Err("plugin render seconds must be finite and between 0.001 and 10".into());
    }
    if session.sample_rate != 48_000 {
        return Err("VST3 effects require a 48000 Hz session sample rate".into());
    }
    let frame_count = (seconds * f64::from(session.sample_rate)).round();
    if !(1.0..=480_000.0).contains(&frame_count) {
        return Err("plugin render must contain between 1 and 480000 frames".into());
    }
    let frames = frame_count as usize;
    let mut mix = vec![[0.0; 2]; frames];

    for source_track in &session.tracks {
        let mut one_track = session.clone();
        one_track.schema_version = 3;
        one_track.tracks = vec![source_track.clone()];
        let track = &mut one_track.tracks[0];
        let effects = track.effects.take().unwrap_or_default();
        track.effects = Some(Vec::new());
        track.automation = None;

        let mut engine = Engine::prepare(&one_track)?;
        let mut stem = vec![[0.0; 2]; frames];
        engine.render_block_unclipped(&mut stem);
        for effect in effects {
            match &effect {
                Effect::Gain { id, gain, bypass } => {
                    if *bypass {
                        continue;
                    }
                    let points = source_track
                        .automation
                        .as_deref()
                        .unwrap_or_default()
                        .iter()
                        .find(|lane| lane.effect_id == *id)
                        .map(|lane| lane.points.as_slice())
                        .unwrap_or_default();
                    let mut value = *gain;
                    let mut next = 0;
                    for (frame, samples) in stem.iter_mut().enumerate() {
                        while next < points.len() && points[next].frame <= frame as u64 {
                            value = points[next].value;
                            next += 1;
                        }
                        samples[0] *= value;
                        samples[1] *= value;
                    }
                }
                Effect::Vst3 { bypass, .. } if *bypass => {}
                Effect::Vst3 { .. } => {
                    let processed = crate::hosting::process(&effect, &stem)?;
                    stem = processed.audio;
                    if stem.len() != frames {
                        return Err("VST3 processor returned an unexpected frame count".into());
                    }
                    if stem.iter().flatten().any(|sample| !sample.is_finite()) {
                        return Err("VST3 processor returned non-finite audio".into());
                    }
                }
            }
        }
        for (mixed, sample) in mix.iter_mut().zip(stem) {
            mixed[0] += sample[0];
            mixed[1] += sample[1];
        }
    }

    if mix.iter().flatten().any(|sample| !sample.is_finite()) {
        return Err("plugin render mix contains non-finite audio".into());
    }
    Ok(PreparedPluginRender {
        sample_rate: session.sample_rate,
        frames: frames as u64,
        mix,
    })
}

impl PreparedPluginRender {
    /// Encode the prepared mix as stereo PCM16 WAV, clipping only at the final
    /// master output and counting a frame once even when both channels clip.
    pub fn encode<W: Write + Seek>(self, output: W) -> Result<RenderReport, String> {
        let spec = hound::WavSpec {
            channels: 2,
            sample_rate: self.sample_rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut writer = hound::WavWriter::new(output, spec).map_err(|e| e.to_string())?;
        let mut clipped_frames = 0;
        for [left, right] in self.mix {
            if left.abs() > 1.0 || right.abs() > 1.0 {
                clipped_frames += 1;
            }
            writer
                .write_sample(to_pcm16(left))
                .map_err(|e| e.to_string())?;
            writer
                .write_sample(to_pcm16(right))
                .map_err(|e| e.to_string())?;
        }
        writer.finalize().map_err(|e| e.to_string())?;
        Ok(RenderReport {
            frames: self.frames,
            sample_rate: self.sample_rate,
            channels: 2,
            clipped_frames,
        })
    }
}

fn to_pcm16(sample: f64) -> i16 {
    (sample.clamp(-1.0, 1.0) * i16::MAX as f64).round() as i16
}
