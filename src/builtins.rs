//! Versioned factory instruments. Preparation owns all PCM; rendering only reads it.
use serde::{Deserialize, Serialize};
use std::f64::consts::TAU;

pub const FACTORY_KIT: &str = "factory-v1";
pub const DRUM_MIDI: [u8; 3] = [36, 38, 42];
const DURATIONS_MS: [u32; 3] = [500, 250, 120];

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Waveform {
    Saw,
    Square,
}

pub fn drum_index(hz: f64) -> Option<usize> {
    DRUM_MIDI.iter().position(|&midi| {
        let expected = 440.0 * 2.0_f64.powf((f64::from(midi) - 69.0) / 12.0);
        (hz - expected).abs() <= expected * 1e-6
    })
}

pub fn drum_frames(index: usize, sample_rate: u32) -> u64 {
    u64::from(sample_rate) * u64::from(DURATIONS_MS[index]) / 1000
}

/// Deterministic synthesized one-shots are the project-owned factory-v1 bank.
#[derive(Debug)]
pub struct DrumBank {
    pub samples: [Vec<f64>; 3],
}
impl DrumBank {
    pub fn prepare(sample_rate: u32) -> Self {
        let samples = std::array::from_fn(|index| {
            let mut seed = 0x4d59_5df4_u32;
            let mut phase = 0.0;
            let mut previous_noise = 0.0;
            (0..drum_frames(index, sample_rate))
                .map(|frame| {
                    let t = frame as f64 / f64::from(sample_rate);
                    seed ^= seed << 13;
                    seed ^= seed >> 17;
                    seed ^= seed << 5;
                    let noise = f64::from(seed) / f64::from(u32::MAX) * 2.0 - 1.0;
                    let sample = match index {
                        0 => {
                            phase +=
                                TAU * (48.0 + 105.0 * (-t * 35.0).exp()) / f64::from(sample_rate);
                            phase.sin() * (-t * 12.0).exp() * 0.85
                        }
                        1 => ((TAU * 185.0 * t).sin() * 0.25 + noise * 0.65) * (-t * 22.0).exp(),
                        _ => (noise - previous_noise) * 0.3 * (-t * 55.0).exp(),
                    };
                    previous_noise = noise;
                    let length = drum_frames(index, sample_rate);
                    // Fade the final 5ms to zero, including the last stored frame.
                    let fade =
                        ((length - frame - 1) as f64 / (f64::from(sample_rate) * 0.005)).min(1.0);
                    sample * fade
                })
                .collect()
        });
        Self { samples }
    }
}

pub fn envelope_frames(ms: f64, sample_rate: u32) -> u64 {
    (ms * f64::from(sample_rate) / 1000.0).round().max(1.0) as u64
}

fn poly_blep(phase: f64, step: f64) -> f64 {
    if phase < step {
        let t = phase / step;
        t + t - t * t - 1.0
    } else if phase > 1.0 - step {
        let t = (phase - 1.0) / step;
        t * t + t + t + 1.0
    } else {
        0.0
    }
}

pub fn oscillator(waveform: Waveform, phase: f64, step: f64) -> f64 {
    match waveform {
        Waveform::Saw => 2.0 * phase - 1.0 - poly_blep(phase, step),
        Waveform::Square => {
            (if phase < 0.5 { 1.0 } else { -1.0 }) + poly_blep(phase, step)
                - poly_blep((phase + 0.5) % 1.0, step)
        }
    }
}
