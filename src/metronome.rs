//! Listening-only click, on the session's integer 960-PPQ frame grid.
pub fn supports_session(session: &crate::session::Session) -> bool {
    use crate::session::Device;
    session.tracks.iter().all(|track| {
        matches!(
            track.device,
            Device::Sine { .. }
                | Device::Synth { .. }
                | Device::Drumkit { .. }
                | Device::Audio { .. }
        ) && track
            .effects
            .iter()
            .flatten()
            .all(|effect| effect.is_builtin())
    })
}

#[derive(Clone, Debug)]
pub struct Metronome {
    rate: u32,
    tempo: u32,
    enabled: bool,
    count_in_total: u64,
    count_in_remaining: u64,
}
impl Metronome {
    pub fn new(rate: u32, tempo: u32, enabled: bool, bars: u8) -> Result<Self, String> {
        if bars > 2 {
            return Err("count_in_bars must be between 0 and 2".into());
        }
        crate::session::tick_to_frame(0, rate, tempo)?;
        let count_in_total = crate::session::tick_to_frame(u64::from(bars) * 4 * 960, rate, tempo)?;
        Ok(Self {
            rate,
            tempo,
            enabled,
            count_in_total,
            count_in_remaining: count_in_total,
        })
    }
    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    pub(crate) fn set_tempo(&mut self, tempo: u32) {
        self.tempo = tempo;
    }
    pub fn enabled(&self) -> bool {
        self.enabled
    }
    pub fn count_in_remaining_frames(&self) -> u64 {
        self.count_in_remaining
    }
    pub fn next_count_in(&mut self) -> Option<f64> {
        if self.count_in_remaining == 0 {
            return None;
        }
        let sample = self.click(self.count_in_total - self.count_in_remaining);
        self.count_in_remaining -= 1;
        Some(sample)
    }
    pub fn sample(&self, frame: u64) -> f64 {
        if self.enabled { self.click(frame) } else { 0.0 }
    }
    fn click(&self, frame: u64) -> f64 {
        let numerator = u128::from(self.rate) * 60_000;
        // Candidate beat is at most one behind due to half-up rounding.
        let mut beat = u128::from(frame) * u128::from(self.tempo) / numerator;
        let beat_frame = |beat: u128| {
            (beat * numerator * 2 + u128::from(self.tempo)) / (2 * u128::from(self.tempo))
        };
        if beat_frame(beat + 1) <= u128::from(frame) {
            beat += 1;
        }
        let elapsed = (u128::from(frame) - beat_frame(beat)) as u64;
        let length = u64::from(self.rate) / 100; // short, 10 ms click
        if elapsed >= length {
            return 0.0;
        }
        let accent = beat % 4 == 0;
        let gain = if accent { 0.32 } else { 0.2 };
        let frequency = if accent { 1800.0 } else { 1200.0 };
        let phase = std::f64::consts::TAU * frequency * elapsed as f64 / f64::from(self.rate);
        gain * phase.cos() * (1.0 - elapsed as f64 / length as f64)
    }
}
