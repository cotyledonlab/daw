//! Prepared serial stereo built-in effect processing. All parameter storage is owned before playback.
use crate::session::{AutomationPoint, Effect, Track};

#[derive(Debug)]
struct PreparedGain {
    base: f64,
    value: f64,
    bypass: bool,
    points: Vec<AutomationPoint>,
    next: usize,
}

impl PreparedGain {
    fn seek(&mut self, frame: u64) {
        self.next = self.points.partition_point(|p| p.frame <= frame);
        self.value = if self.next == 0 {
            self.base
        } else {
            self.points[self.next - 1].value
        };
    }

    fn process(&mut self, samples: &mut [f64; 2], frame: u64) {
        while self.next < self.points.len() && self.points[self.next].frame <= frame {
            self.value = self.points[self.next].value;
            self.next += 1;
        }
        if !self.bypass {
            samples[0] *= self.value;
            samples[1] *= self.value;
        }
    }
}

#[derive(Debug)]
enum PreparedEffect {
    Gain(PreparedGain),
    Lowpass {
        alpha: f64,
        state: [f64; 2],
        bypass: bool,
    },
    Delay {
        buffer: Vec<[f64; 2]>,
        cursor: usize,
        valid_frames: usize,
        feedback: f64,
        mix: f64,
        bypass: bool,
    },
}

/// Ordered stereo track processing. State is allocated during preparation only.
/// Seek/loop clears filter history and delay tails; finite exports retain their requested length.
#[derive(Debug)]
pub(crate) struct PreparedChain {
    effects: Vec<PreparedEffect>,
}

impl PreparedChain {
    /// The caller must validate the complete session before preparing its tracks.
    pub(crate) fn prepare(track: &Track, sample_rate: u32) -> Self {
        let effects = track
            .effects
            .as_deref()
            .unwrap_or_default()
            .iter()
            .map(|effect| match effect {
                Effect::Gain { id, gain, bypass } => {
                    let points = track
                        .automation
                        .as_deref()
                        .unwrap_or_default()
                        .iter()
                        .find(|lane| &lane.effect_id == id)
                        .map_or_else(Vec::new, |lane| lane.points.clone());
                    PreparedEffect::Gain(PreparedGain {
                        base: *gain,
                        value: *gain,
                        bypass: *bypass,
                        points,
                        next: 0,
                    })
                }
                Effect::Lowpass {
                    cutoff_hz, bypass, ..
                } => PreparedEffect::Lowpass {
                    alpha: 1.0
                        - (-std::f64::consts::TAU * cutoff_hz / f64::from(sample_rate)).exp(),
                    state: [0.0; 2],
                    bypass: *bypass,
                },
                Effect::Delay {
                    time_ms,
                    feedback,
                    mix,
                    bypass,
                    ..
                } => PreparedEffect::Delay {
                    buffer: vec![
                        [0.0; 2];
                        (time_ms * f64::from(sample_rate) / 1000.0).round().max(1.0)
                            as usize
                    ],
                    cursor: 0,
                    valid_frames: 0,
                    feedback: *feedback,
                    mix: *mix,
                    bypass: *bypass,
                },
                Effect::Vst3 { .. } | Effect::Au { .. } => {
                    unreachable!("plugins rejected before native chain preparation")
                }
            })
            .collect();
        Self { effects }
    }

    pub(crate) fn seek(&mut self, frame: u64) {
        for effect in &mut self.effects {
            match effect {
                PreparedEffect::Gain(gain) => gain.seek(frame),
                PreparedEffect::Lowpass { state, .. } => *state = [0.0; 2],
                PreparedEffect::Delay {
                    cursor,
                    valid_frames,
                    ..
                } => {
                    // Logical invalidation keeps reset bounded even for one-frame loops.
                    // Old slots remain unreadable until every slot has been overwritten.
                    *cursor = 0;
                    *valid_frames = 0;
                }
            }
        }
    }

    pub(crate) fn process(&mut self, samples: &mut [f64; 2], frame: u64) {
        for effect in &mut self.effects {
            match effect {
                PreparedEffect::Gain(gain) => gain.process(samples, frame),
                PreparedEffect::Lowpass {
                    alpha,
                    state,
                    bypass,
                } => {
                    if !*bypass {
                        for (value, input) in state.iter_mut().zip(samples.iter()) {
                            *value += *alpha * (*input - *value);
                        }
                        *samples = *state;
                    }
                }
                PreparedEffect::Delay {
                    buffer,
                    cursor,
                    valid_frames,
                    feedback,
                    mix,
                    bypass,
                } => {
                    if !*bypass {
                        let delayed = if *valid_frames == buffer.len() {
                            buffer[*cursor]
                        } else {
                            [0.0; 2]
                        };
                        for channel in 0..2 {
                            let dry = samples[channel];
                            buffer[*cursor][channel] = dry + delayed[channel] * *feedback;
                            samples[channel] = dry * (1.0 - *mix) + delayed[channel] * *mix;
                        }
                        *cursor = (*cursor + 1) % buffer.len();
                        *valid_frames = (*valid_frames + 1).min(buffer.len());
                    }
                }
            }
        }
    }
}
