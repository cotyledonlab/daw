//! Bounded pull rendering for browser monitoring, without a hardware device.
use crate::{engine::Engine, session::Session};
use serde_json::{Value, json};
use std::time::{Duration, Instant};

pub fn validate_live_update(old: &Session, new: &Session) -> Result<(), String> {
    new.validate()?;
    let builtins = |session: &Session| {
        session.tracks.iter().all(|t| {
            matches!(
                t.device,
                crate::session::Device::Sine { .. }
                    | crate::session::Device::Synth { .. }
                    | crate::session::Device::Drumkit { .. }
                    | crate::session::Device::Audio { .. }
            ) && t.effects.iter().flatten().all(|e| e.is_builtin())
        })
    };
    if !builtins(old)
        || !builtins(new)
        || old.sample_rate != new.sample_rate
        || old.tracks.len() != new.tracks.len()
        || old.tracks.iter().zip(&new.tracks).any(|(a, b)| {
            a.id != b.id
                || a.mode != b.mode
                || std::mem::discriminant(&a.device) != std::mem::discriminant(&b.device)
        })
    {
        return Err("live edits require the same ordered built-in tracks, device kinds, modes and sample rate; stop playback for track or foreign-runtime changes".into());
    }
    Ok(())
}

#[derive(Default)]
pub struct Stream {
    engine: Option<Engine>,
    session: Option<Session>,
    serial: u64,
    revision: u64,
    paused: bool,
    last_read: Option<Instant>,
    transition: Vec<[f64; 2]>,
}
impl Stream {
    pub fn active(&mut self) -> bool {
        if self
            .last_read
            .is_some_and(|t| t.elapsed() > Duration::from_secs(30))
        {
            self.stop();
        }
        self.engine.is_some()
    }
    pub fn start(&mut self, session: &Session, revision: u64) -> Result<Value, String> {
        if self.active() {
            return Err("A browser stream is already active; stop it first".into());
        }
        validate_live_update(session, session)?;
        let engine = Engine::prepare(session)?;
        let serial = self
            .serial
            .checked_add(1)
            .ok_or("stream identity exhausted")?;
        self.serial = serial;
        self.engine = Some(engine);
        self.session = Some(session.clone());
        self.revision = revision;
        self.paused = false;
        self.last_read = Some(Instant::now());
        Ok(self.status())
    }
    pub fn check(&mut self, id: &str) -> Result<(), String> {
        if !self.active() || id != self.serial.to_string() {
            return Err("Browser stream expired or belongs to another playback".into());
        }
        self.last_read = Some(Instant::now());
        Ok(())
    }
    pub fn status(&mut self) -> Value {
        self.active();
        json!({"state":if self.engine.is_none() {"stopped"} else if self.paused {"paused"} else {"playing"},
            "stream_id":self.serial.to_string(), "sample_rate":self.session.as_ref().map(|s|s.sample_rate),
            "timeline_frame":self.engine.as_ref().map_or(0, |e|e.frame_position()),
            "output_frames":self.engine.as_ref().map_or(0, |e|e.output_position()),
            "live_arrangement_edits":self.engine.is_some(), "source_mode":"prepared", "until_stopped":true,
            "rendered_session_revision":self.revision.to_string()})
    }
    pub fn stop(&mut self) {
        self.engine = None;
        self.session = None;
        self.transition.clear();
        self.last_read = None;
    }
    pub fn pause(&mut self, paused: bool) {
        self.paused = paused;
    }
    pub fn seek(&mut self, frame: u64) -> Result<(), String> {
        self.engine.as_mut().ok_or("stream stopped")?.seek(frame)?;
        self.transition.clear();
        Ok(())
    }
    pub fn set_loop(&mut self, region: Option<(u64, u64)>) -> Result<(), String> {
        self.engine
            .as_mut()
            .ok_or("stream stopped")?
            .set_loop(region)
    }
    pub fn update(&mut self, session: &Session, revision: u64) -> Result<(), String> {
        if !self.active() {
            return Err("Browser stream stopped".into());
        }
        let old = self.session.as_ref().ok_or("stream stopped")?;
        validate_live_update(old, session)?;
        let mut prepared = Engine::prepare(session)?;
        let previous = self.engine.as_mut().ok_or("stream stopped")?;
        prepared.adopt_live(previous, old, session);
        let mut transition = vec![[0.0; 2]; 64];
        previous.render_block(&mut transition);
        self.engine = Some(prepared);
        self.session = Some(session.clone());
        self.transition = transition;
        self.revision = revision;
        Ok(())
    }
    pub fn read(&mut self, frames: usize) -> Result<Value, String> {
        if !(256..=4096).contains(&frames) {
            return Err("stream block must contain 256..4096 frames".into());
        }
        if self.paused {
            return Err("Browser stream is paused".into());
        }
        let engine = self.engine.as_mut().ok_or("stream stopped")?;
        let start = engine.frame_position();
        let mut pcm = vec![[0.0; 2]; frames];
        let clipped = engine.render_block(&mut pcm);
        for (i, prior) in self.transition.iter().enumerate() {
            let mix = (i + 1) as f64 / 64.0;
            for (channel, sample) in pcm[i].iter_mut().enumerate() {
                *sample = prior[channel] * (1.0 - mix) + *sample * mix;
            }
        }
        self.transition.clear();
        Ok(
            json!({"pcm":pcm,"start_frame":start,"end_frame":engine.frame_position(),
            "sample_rate":self.session.as_ref().unwrap().sample_rate,"revision":self.revision.to_string(),"clipped_frames":clipped}),
        )
    }
}
