//! Prepared sine rendering state shared by offline blocks and future engine work.
//! This module does not provide a device callback or claim real-time readiness.

use crate::session::{Device, Session};
use std::f64::consts::TAU;

#[derive(Debug, Clone, Copy)]
struct Voice {
    phase: f64,
    increment: f64,
    gain: f64,
}

/// Validated, preallocated state for rendering a session in blocks.
#[derive(Debug)]
pub struct Engine {
    voices: Vec<Voice>,
    frame_position: u64,
}

impl Engine {
    /// Validate the complete session and allocate oscillator state before rendering.
    pub fn prepare(session: &Session) -> Result<Self, String> {
        session.validate()?;
        let rate = f64::from(session.sample_rate);
        let voices = session
            .tracks
            .iter()
            .map(|track| match track.device {
                Device::Sine { frequency_hz, gain } => Voice {
                    phase: 0.0,
                    increment: TAU * frequency_hz / rate,
                    gain,
                },
            })
            .collect();
        Ok(Self {
            voices,
            frame_position: 0,
        })
    }

    /// Absolute frame index of the next frame to render.
    pub fn frame_position(&self) -> u64 {
        self.frame_position
    }

    /// Mix and hard-clip one stereo block. State advances continuously across calls.
    /// This method uses only prepared storage and the caller-provided output slice.
    pub fn render_block(&mut self, output: &mut [[f64; 2]]) -> u64 {
        let mut clipped_frames = 0;
        for frame in &mut *output {
            let mut mixed = 0.0;
            for voice in &mut self.voices {
                mixed += voice.phase.sin() * voice.gain;
                voice.phase += voice.increment;
                if voice.phase >= TAU {
                    voice.phase -= TAU;
                }
            }
            if mixed.abs() > 1.0 {
                clipped_frames += 1;
            }
            let sample = mixed.clamp(-1.0, 1.0);
            *frame = [sample, sample];
        }
        self.frame_position = self.frame_position.saturating_add(output.len() as u64);
        clipped_frames
    }
}
