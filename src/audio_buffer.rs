//! Device-independent, bounded callback buffer adapter. No device access here.
use crate::{engine::Engine, render::validate_duration, session::Session};

pub struct PlaybackBuffer {
    engine: Engine,
    channels: usize,
    remaining: u64,
    volume: f64,
}

impl PlaybackBuffer {
    pub fn prepare(
        session: &Session,
        device_rate: u32,
        channels: usize,
        seconds: f64,
        volume: f64,
    ) -> Result<Self, String> {
        session.validate()?;
        validate_duration(seconds)?;
        if !(1..=32).contains(&channels) {
            return Err("device must have 1..32 channels".into());
        }
        if !volume.is_finite() || !(0.0..=1.0).contains(&volume) {
            return Err("volume must be finite and between 0 and 1".into());
        }
        if session.schema_version == 2 && device_rate != session.sample_rate {
            return Err(
                "note sessions require the device rate to match the session sample rate".into(),
            );
        }
        let mut adjusted = session.clone();
        adjusted.sample_rate = device_rate;
        // Prepare at the actual device rate, preserving Hz. No sample resampling needed
        // for these procedural oscillators. Reject tones beyond device Nyquist.
        let engine = Engine::prepare(&adjusted)?;
        Ok(Self {
            engine,
            channels,
            remaining: (seconds * f64::from(device_rate)).round() as u64,
            volume,
        })
    }

    pub fn remaining_frames(&self) -> u64 {
        self.remaining
    }

    pub fn frame_position(&self) -> u64 {
        self.engine.frame_position()
    }

    pub fn output_position(&self) -> u64 {
        self.engine.output_position()
    }

    pub fn seek(&mut self, frame: u64) -> Result<(), String> {
        self.engine.seek(frame)
    }

    pub fn set_loop(&mut self, region: Option<(u64, u64)>) -> Result<(), String> {
        self.engine.set_loop(region)
    }

    /// Fill all samples, including silence after the duration and on malformed buffers.
    /// The conversion function must be allocation-free and map zero to sample equilibrium.
    pub fn fill<T: Copy>(
        &mut self,
        output: &mut [T],
        convert: impl Fn(f64) -> T,
    ) -> Result<u64, &'static str> {
        output.fill(convert(0.0));
        if output.len() % self.channels != 0 {
            return Err("partial device frame");
        }
        let frames = (output.len() / self.channels).min(self.remaining as usize);
        let mut block = [[0.0; 2]; 256];
        for offset in (0..frames).step_by(256) {
            let count = (frames - offset).min(256);
            self.engine.render_block(&mut block[..count]);
            for (index, frame) in block[..count].iter().enumerate() {
                let destination = &mut output[(offset + index) * self.channels..][..self.channels];
                destination[0] = convert(if self.channels == 1 {
                    (frame[0] + frame[1]) * 0.5 * self.volume
                } else {
                    frame[0] * self.volume
                });
                if self.channels >= 2 {
                    destination[1] = convert(frame[1] * self.volume);
                }
                // Channels beyond stereo intentionally remain silent.
            }
        }
        self.remaining -= frames as u64;
        Ok(frames as u64)
    }
}
