//! Prepared sine and note rendering. All scheduling storage is allocated before playback.
use crate::session::{Device, MAX_VOICES, Session, TrackMode, envelope_frames};
use std::f64::consts::TAU;

#[derive(Debug, Clone, Copy)]
struct Voice {
    phase: f64,
    increment: f64,
    gain: f64,
}

#[derive(Debug)]
struct PreparedNote {
    start: u64,
    off: u64,
    end: u64,
    increment: f64,
    gain: f64,
    release_level: f64,
}

#[derive(Debug, Clone, Copy, Default)]
struct ActiveNote {
    index: usize,
    phase: f64,
}

/// Validated, preallocated state for rendering a session in blocks.
#[derive(Debug)]
pub struct Engine {
    voices: Vec<Voice>,
    notes: Vec<PreparedNote>,
    starts: Vec<usize>,
    next_start: usize,
    active: [ActiveNote; MAX_VOICES],
    active_count: usize,
    envelope: f64,
    frame_position: u64,
}

impl Engine {
    pub fn prepare(session: &Session) -> Result<Self, String> {
        session.validate()?;
        let rate = f64::from(session.sample_rate);
        let envelope = envelope_frames(session.sample_rate);
        let mut voices = Vec::new();
        let mut ordered = Vec::new();
        for track in &session.tracks {
            let Device::Sine { frequency_hz, gain } = track.device;
            if track.mode != Some(TrackMode::Sequenced) {
                voices.push(Voice {
                    phase: 0.0,
                    increment: TAU * frequency_hz / rate,
                    gain,
                });
                continue;
            }
            for clip in track.clips.as_ref().expect("validated clips") {
                for note in &clip.notes {
                    let start = clip.start_frame + note.start_frame;
                    let off = start + note.duration_frames;
                    ordered.push((
                        (&track.id, &clip.id, &note.id),
                        PreparedNote {
                            start,
                            off,
                            end: (off + envelope).min(clip.start_frame + clip.length_frames),
                            increment: TAU * note.frequency_hz / rate,
                            gain: gain * note.velocity,
                            release_level: (note.duration_frames as f64 / envelope as f64).min(1.0),
                        },
                    ));
                }
            }
        }
        ordered.sort_by(|a, b| a.0.cmp(&b.0));
        let notes: Vec<_> = ordered.into_iter().map(|(_, note)| note).collect();
        let mut starts: Vec<_> = (0..notes.len()).collect();
        starts.sort_by_key(|&index| (notes[index].start, index));
        Ok(Self {
            voices,
            notes,
            starts,
            next_start: 0,
            active: [ActiveNote::default(); MAX_VOICES],
            active_count: 0,
            envelope: envelope as f64,
            frame_position: 0,
        })
    }

    pub fn frame_position(&self) -> u64 {
        self.frame_position
    }

    /// Retire ended voices and insert this frame's starts into the fixed active list.
    /// Identity ranks and onset order were prepared earlier; no callback sorting/allocation.
    fn update_notes(&mut self) {
        let mut retained = 0;
        for index in 0..self.active_count {
            let voice = self.active[index];
            if self.notes[voice.index].end > self.frame_position {
                self.active[retained] = voice;
                retained += 1;
            }
        }
        self.active_count = retained;
        while self.next_start < self.starts.len() {
            let index = self.starts[self.next_start];
            if self.notes[index].start != self.frame_position {
                break;
            }
            let insertion =
                self.active[..self.active_count].partition_point(|voice| voice.index < index);
            self.active
                .copy_within(insertion..self.active_count, insertion + 1);
            self.active[insertion] = ActiveNote { index, phase: 0.0 };
            self.active_count += 1;
            self.next_start += 1;
        }
    }

    /// Mix and hard-clip stereo frames. Each sample has the same event/envelope
    /// semantics regardless of the caller's block sizes.
    pub fn render_block(&mut self, output: &mut [[f64; 2]]) -> u64 {
        let mut clipped_frames = 0;
        for frame in output {
            self.update_notes();
            let mut mixed = 0.0;
            for voice in &mut self.voices {
                mixed += voice.phase.sin() * voice.gain;
                voice.phase += voice.increment;
                if voice.phase >= TAU {
                    voice.phase -= TAU;
                }
            }
            for voice in &mut self.active[..self.active_count] {
                let note = &self.notes[voice.index];
                let envelope = if self.frame_position < note.off {
                    ((self.frame_position - note.start) as f64 / self.envelope).min(1.0)
                } else {
                    note.release_level
                        * (1.0 - (self.frame_position - note.off) as f64 / self.envelope)
                };
                mixed += voice.phase.sin() * note.gain * envelope;
                voice.phase += note.increment;
                if voice.phase >= TAU {
                    voice.phase -= TAU;
                }
            }
            if mixed.abs() > 1.0 {
                clipped_frames += 1;
            }
            let sample = mixed.clamp(-1.0, 1.0);
            *frame = [sample, sample];
            self.frame_position = self.frame_position.saturating_add(1);
        }
        clipped_frames
    }
}
