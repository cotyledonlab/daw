use crate::session::{Device, Session};
use serde::Serialize;
use std::{
    f64::consts::TAU,
    io::{Seek, Write},
};

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

/// Offline reference renderer, not a real-time callback. Each render starts at frame zero.
/// Constant memory use; track signals sum, then hard-clip into stereo PCM16.
pub fn render<W: Write + Seek>(
    session: &Session,
    seconds: f64,
    output: W,
) -> Result<RenderReport, String> {
    session.validate()?;
    validate_duration(seconds)?;
    let frames = (seconds * session.sample_rate as f64).round() as u64;
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: session.sample_rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::new(output, spec).map_err(|e| e.to_string())?;
    let mut clipped_frames = 0;
    for frame in 0..frames {
        let time = frame as f64 / session.sample_rate as f64;
        let mixed: f64 = session
            .tracks
            .iter()
            .map(|track| match track.device {
                Device::Sine { frequency_hz, gain } => (TAU * frequency_hz * time).sin() * gain,
            })
            .sum();
        if mixed.abs() > 1.0 {
            clipped_frames += 1;
        }
        let sample = (mixed.clamp(-1.0, 1.0) * i16::MAX as f64).round() as i16;
        writer.write_sample(sample).map_err(|e| e.to_string())?;
        writer.write_sample(sample).map_err(|e| e.to_string())?;
    }
    writer.finalize().map_err(|e| e.to_string())?;
    Ok(RenderReport {
        frames,
        sample_rate: session.sample_rate,
        channels: 2,
        clipped_frames,
    })
}
