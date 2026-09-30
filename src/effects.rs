//! Prepared serial stereo gain processing. All parameter storage is owned before playback.
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

/// Source scheduling and effect parameters meet at stereo track audio.
/// This concrete chain has zero latency; processing and seek perform no heap operations.
#[derive(Debug)]
pub(crate) struct PreparedChain {
    gains: Vec<PreparedGain>,
}

impl PreparedChain {
    /// The caller must validate the complete session before preparing its tracks.
    pub(crate) fn prepare(track: &Track) -> Self {
        let gains = track
            .effects
            .as_ref()
            .expect("validated effects")
            .iter()
            .map(|effect| {
                let Effect::Gain { id, gain, bypass } = effect else {
                    unreachable!("plugins rejected before native chain preparation")
                };
                let points = track
                    .automation
                    .as_deref()
                    .unwrap_or_default()
                    .iter()
                    .find(|lane| &lane.effect_id == id)
                    .map_or_else(Vec::new, |lane| lane.points.clone());
                PreparedGain {
                    base: *gain,
                    value: *gain,
                    bypass: *bypass,
                    points,
                    next: 0,
                }
            })
            .collect();
        Self { gains }
    }

    pub(crate) fn seek(&mut self, frame: u64) {
        for gain in &mut self.gains {
            gain.seek(frame);
        }
    }

    pub(crate) fn process(&mut self, samples: &mut [f64; 2], frame: u64) {
        for gain in &mut self.gains {
            gain.process(samples, frame);
        }
    }
}
