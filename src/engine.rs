//! Prepared sine and note rendering. All scheduling storage is allocated before playback.
use crate::{
    assets::{self, PreparedAudioClip},
    effects::PreparedChain,
    session::{
        Clip, Device, MAX_FRAME, MAX_TRACKS, MAX_VOICES, Session, TrackMode, envelope_frames,
    },
};
use std::f64::consts::TAU;

#[derive(Debug, Clone, Copy)]
struct Voice {
    track_index: usize,
    phase: f64,
    increment: f64,
    gain: f64,
}

#[derive(Debug)]
struct PreparedNote {
    track_index: usize,
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
    // Schema-v3 tracks sum into independent stereo buses before serial effects.
    effect_chains: Option<Vec<PreparedChain>>,
    audio: Vec<PreparedAudioClip>,
    audio_starts: Vec<usize>,
    next_audio: usize,
    active_audio: [usize; MAX_VOICES],
    audio_count: usize,
    notes: Vec<PreparedNote>,
    starts: Vec<usize>,
    next_start: usize,
    active: [ActiveNote; MAX_VOICES],
    active_count: usize,
    envelope: f64,
    frame_position: u64,
    output_position: u64,
    loop_region: Option<(u64, u64)>,
}

impl Engine {
    pub fn prepare(session: &Session) -> Result<Self, String> {
        session.validate()?;
        if crate::hosting::has_plugins(session) {
            return Err("VST3 sessions support offline rendering only; native plugin playback is unavailable".into());
        }
        let mut audio =
            assets::prepare_with_budget(session, crate::sc_source::decoded_bytes(session))?;
        audio.extend(crate::sc_source::prepare_clips(session)?);
        crate::sc_source::check_budget(audio.iter())?;
        let mut audio_starts: Vec<_> = (0..audio.len()).collect();
        audio_starts.sort_by_key(|&index| (audio[index].start, index));
        let rate = f64::from(session.sample_rate);
        let envelope = envelope_frames(session.sample_rate);
        let mut voices = Vec::new();
        let mut ordered = Vec::new();
        for (track_index, track) in session.tracks.iter().enumerate() {
            let Device::Sine { frequency_hz, gain } = track.device else {
                continue;
            };
            if track.mode != Some(TrackMode::Sequenced) {
                voices.push(Voice {
                    track_index,
                    phase: 0.0,
                    increment: TAU * frequency_hz / rate,
                    gain,
                });
                continue;
            }
            for clip in track.clips.as_ref().expect("validated clips") {
                let Clip::Notes(clip) = clip else {
                    continue;
                };
                for note in &clip.notes {
                    let start = clip.start_frame + note.start_frame;
                    let off = start + note.duration_frames;
                    ordered.push((
                        (&track.id, &clip.id, &note.id),
                        PreparedNote {
                            track_index,
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
        let effect_chains = (session.schema_version >= 3)
            .then(|| session.tracks.iter().map(PreparedChain::prepare).collect());
        Ok(Self {
            voices,
            effect_chains,
            audio,
            audio_starts,
            next_audio: 0,
            active_audio: [0; MAX_VOICES],
            audio_count: 0,
            notes,
            starts,
            next_start: 0,
            active: [ActiveNote::default(); MAX_VOICES],
            active_count: 0,
            envelope: envelope as f64,
            frame_position: 0,
            output_position: 0,
            loop_region: None,
        })
    }

    pub fn frame_position(&self) -> u64 {
        self.frame_position
    }

    /// Number of frames emitted since preparation. Timeline jumps do not affect it.
    pub fn output_position(&self) -> u64 {
        self.output_position
    }

    /// Move the timeline without chasing notes. Audio clips covering the destination
    /// become active at their corresponding source offsets.
    pub fn seek(&mut self, frame: u64) -> Result<(), String> {
        if frame > MAX_FRAME {
            return Err("seek frame exceeds MAX_FRAME".into());
        }
        self.frame_position = frame;
        self.reset_schedules(frame);
        Ok(())
    }

    pub fn set_loop(&mut self, region: Option<(u64, u64)>) -> Result<(), String> {
        if let Some((start, end)) = region {
            if start > MAX_FRAME || end > MAX_FRAME || end <= start {
                return Err("loop must satisfy 0 <= start < end <= MAX_FRAME".into());
            }
        }
        self.loop_region = region;
        Ok(())
    }

    /// Clear active state and position cursors for a discontinuity. Note starts before
    /// the destination are intentionally skipped; audio clips spanning it are retained.
    fn reset_schedules(&mut self, frame: u64) {
        if let Some(chains) = &mut self.effect_chains {
            for chain in chains {
                chain.seek(frame);
            }
        }
        self.active_count = 0;
        for voice in &mut self.voices {
            voice.phase = 0.0;
        }
        self.next_start = self
            .starts
            .partition_point(|&index| self.notes[index].start < frame);
        self.audio_count = 0;
        for (index, clip) in self.audio.iter().enumerate() {
            if clip.start <= frame && frame < clip.end {
                // Prepared audio identity order is the vector index order.
                self.active_audio[self.audio_count] = index;
                self.audio_count += 1;
            }
        }
        self.next_audio = self
            .audio_starts
            .partition_point(|&index| self.audio[index].start <= frame);
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

    fn update_audio(&mut self) {
        let mut retained = 0;
        for index in 0..self.audio_count {
            let voice = self.active_audio[index];
            if self.audio[voice].end > self.frame_position {
                self.active_audio[retained] = voice;
                retained += 1;
            }
        }
        self.audio_count = retained;
        while self.next_audio < self.audio_starts.len() {
            let index = self.audio_starts[self.next_audio];
            if self.audio[index].start != self.frame_position {
                break;
            }
            let insertion =
                self.active_audio[..self.audio_count].partition_point(|&voice| voice < index);
            self.active_audio
                .copy_within(insertion..self.audio_count, insertion + 1);
            self.active_audio[insertion] = index;
            self.audio_count += 1;
            self.next_audio += 1;
        }
    }

    /// Mix and hard-clip stereo frames. Each sample has the same event/envelope
    /// semantics regardless of the caller's block sizes.
    pub fn render_block(&mut self, output: &mut [[f64; 2]]) -> u64 {
        self.render_inner(output, true)
    }

    /// Offline stem preparation preserves headroom until the final master mix.
    pub(crate) fn render_block_unclipped(&mut self, output: &mut [[f64; 2]]) {
        self.render_inner(output, false);
    }

    fn render_inner(&mut self, output: &mut [[f64; 2]], clip: bool) -> u64 {
        let mut clipped_frames = 0;
        for frame in output {
            if let Some((start, end)) = self.loop_region {
                if self.frame_position >= end {
                    self.frame_position = start;
                    self.reset_schedules(start);
                }
            }
            self.update_notes();
            self.update_audio();
            let mut track_frames = [[0.0; 2]; MAX_TRACKS];
            let mut mixed = 0.0;
            for voice in &mut self.voices {
                let sample = voice.phase.sin() * voice.gain;
                if self.effect_chains.is_some() {
                    track_frames[voice.track_index][0] += sample;
                    track_frames[voice.track_index][1] += sample;
                } else {
                    mixed += sample;
                }
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
                let sample = voice.phase.sin() * note.gain * envelope;
                if self.effect_chains.is_some() {
                    track_frames[note.track_index][0] += sample;
                    track_frames[note.track_index][1] += sample;
                } else {
                    mixed += sample;
                }
                voice.phase += note.increment;
                if voice.phase >= TAU {
                    voice.phase -= TAU;
                }
            }
            let mut stereo = [mixed, mixed];
            for &index in &self.active_audio[..self.audio_count] {
                let clip = &self.audio[index];
                let source = clip.source_offset + (self.frame_position - clip.start) as usize;
                let samples = clip.frames[source];
                if self.effect_chains.is_some() {
                    track_frames[clip.track_index][0] += samples[0] * clip.gain;
                    track_frames[clip.track_index][1] += samples[1] * clip.gain;
                } else {
                    stereo[0] += samples[0] * clip.gain;
                    stereo[1] += samples[1] * clip.gain;
                }
            }
            if let Some(chains) = &mut self.effect_chains {
                for (frame, chain) in track_frames.iter_mut().zip(chains.iter_mut()) {
                    chain.process(frame, self.frame_position);
                    stereo[0] += frame[0];
                    stereo[1] += frame[1];
                }
            }
            if stereo.iter().any(|sample| sample.abs() > 1.0) {
                clipped_frames += 1;
            }
            *frame = if clip {
                [stereo[0].clamp(-1.0, 1.0), stereo[1].clamp(-1.0, 1.0)]
            } else {
                stereo
            };
            self.frame_position = self.frame_position.saturating_add(1);
            self.output_position = self.output_position.saturating_add(1);
        }
        clipped_frames
    }
}
