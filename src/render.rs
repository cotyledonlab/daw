use crate::{
    engine::Engine,
    session::{Device, Session},
};
use serde::Serialize;
use std::io::{BufWriter, Seek, Write};

const BLOCK_FRAMES: usize = 256;
pub const MIN_SECONDS: f64 = 0.001;
/// Finite native playback and legacy prepared runtime export limit.
pub const MAX_SECONDS: f64 = 60.0;
pub const MAX_BUILTIN_RENDER_SECONDS: f64 = 180.0;
pub const MAX_PLUGIN_RENDER_SECONDS: f64 = 10.0;

/// The longer export limit is determined by composition, independent of schema.
pub fn max_render_seconds(session: &Session) -> f64 {
    if crate::hosting::has_plugins(session) {
        return MAX_PLUGIN_RENDER_SECONDS;
    }
    if session.tracks.iter().all(|track| {
        matches!(
            track.device,
            Device::Sine { .. }
                | Device::Synth { .. }
                | Device::Drumkit { .. }
                | Device::Audio { .. }
                | Device::PdInstrument(_)
        ) && track
            .effects
            .iter()
            .flatten()
            .all(|effect| effect.is_builtin())
    }) {
        MAX_BUILTIN_RENDER_SECONDS
    } else {
        MAX_SECONDS
    }
}

pub fn validate_render_duration(session: &Session, seconds: f64) -> Result<(), String> {
    validate_render_limit(seconds, max_render_seconds(session))
}

fn validate_render_limit(seconds: f64, maximum: f64) -> Result<(), String> {
    if !seconds.is_finite() || !(MIN_SECONDS..=maximum).contains(&seconds) {
        return Err(format!(
            "render seconds must be finite and between {MIN_SECONDS} and {maximum} for this session"
        ));
    }
    Ok(())
}

#[derive(Debug, Serialize)]
pub struct RenderReport {
    pub frames: u64,
    pub sample_rate: u32,
    pub channels: u16,
    pub clipped_frames: u64,
}

pub fn validate_duration(seconds: f64) -> Result<(), String> {
    if !seconds.is_finite() || !(MIN_SECONDS..=MAX_SECONDS).contains(&seconds) {
        return Err("seconds must be finite and between 0.001 and 60".into());
    }
    Ok(())
}

/// Render a validated session to stereo PCM16 using bounded stack storage.
/// Each invocation prepares fresh oscillator state at frame zero.
pub fn render<W: Write + Seek>(
    session: &Session,
    seconds: f64,
    output: W,
) -> Result<RenderReport, String> {
    validate_render_duration(session, seconds)?;
    if crate::hosting::has_plugins(session) {
        return crate::plugin_render::prepare(session, seconds)?.encode(output);
    }
    let mut engine = Engine::prepare(session)?;
    render_prepared(&mut engine, session.sample_rate, seconds, output)
}

/// Encode an already prepared snapshot, so asset failures precede output creation.
pub fn render_prepared<W: Write + Seek>(
    engine: &mut Engine,
    sample_rate: u32,
    seconds: f64,
    output: W,
) -> Result<RenderReport, String> {
    validate_render_limit(seconds, engine.max_render_seconds())?;
    let frames = (seconds * sample_rate as f64).round() as u64;
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer =
        hound::WavWriter::new(BufWriter::new(output), spec).map_err(|e| e.to_string())?;
    let mut block = [[0.0; 2]; BLOCK_FRAMES];
    let mut remaining = frames;
    let mut clipped_frames = 0;
    while remaining > 0 {
        let count = remaining.min(BLOCK_FRAMES as u64) as usize;
        clipped_frames += engine.render_block(&mut block[..count]);
        for [left, right] in &block[..count] {
            writer
                .write_sample(to_pcm16(*left))
                .map_err(|e| e.to_string())?;
            writer
                .write_sample(to_pcm16(*right))
                .map_err(|e| e.to_string())?;
        }
        remaining -= count as u64;
    }
    writer.finalize().map_err(|e| e.to_string())?;
    Ok(RenderReport {
        frames,
        sample_rate,
        channels: 2,
        clipped_frames,
    })
}

fn to_pcm16(sample: f64) -> i16 {
    (sample * i16::MAX as f64).round() as i16
}
