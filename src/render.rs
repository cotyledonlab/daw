use crate::{engine::Engine, session::Session};
use serde::Serialize;
use std::io::{Seek, Write};

const BLOCK_FRAMES: usize = 256;

#[derive(Debug, Serialize)]
pub struct RenderReport {
    pub frames: u64,
    pub sample_rate: u32,
    pub channels: u16,
    pub clipped_frames: u64,
}

pub fn validate_duration(seconds: f64) -> Result<(), String> {
    if !seconds.is_finite() || !(0.001..=60.0).contains(&seconds) {
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
    let mut engine = Engine::prepare(session)?;
    validate_duration(seconds)?;
    let frames = (seconds * session.sample_rate as f64).round() as u64;
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: session.sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::new(output, spec).map_err(|e| e.to_string())?;
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
        sample_rate: session.sample_rate,
        channels: 2,
        clipped_frames,
    })
}

fn to_pcm16(sample: f64) -> i16 {
    (sample * i16::MAX as f64).round() as i16
}
