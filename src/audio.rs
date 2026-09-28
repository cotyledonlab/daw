//! macOS CPAL playback. Stream lifecycle stays off the audio callback.
use crate::{audio_buffer::PlaybackBuffer, session::Session};
use cpal::{
    FromSample, SizedSample,
    traits::{DeviceTrait, HostTrait, StreamTrait},
};
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{
            AtomicBool, AtomicU8, AtomicU64,
            Ordering::{Acquire, Relaxed, Release},
        },
    },
    time::{Duration, Instant},
};

#[derive(Default)]
struct Stats {
    error: AtomicBool,
    done: AtomicBool,
    calls: AtomicU64,
    frames: AtomicU64,
    max_frames: AtomicU64,
    max_ns: AtomicU64,
    over_budget: AtomicU64,
    paused: AtomicBool,
    observed: AtomicU8,
    volume: AtomicU64,
    level: AtomicU64,
    // Single owner producer, single callback consumer. Payload may only be
    // overwritten after the callback releases the occupied slot.
    command: AtomicU8,
    command_start: AtomicU64,
    command_end: AtomicU64,
    timeline: AtomicU64,
}

pub fn devices() -> Result<Value, String> {
    let host = cpal::default_host();
    let default = host.default_output_device().and_then(|d| d.name().ok());
    let devices: Vec<_> = host.output_devices().map_err(|e| e.to_string())?.map(|device| {
        let name = device.name().unwrap_or_else(|_| "Unknown".into());
        match device.default_output_config() {
            Ok(c) => json!({"name":name,"sample_rate":c.sample_rate().0,"channels":c.channels(),"format":format!("{:?}",c.sample_format())}),
            Err(e) => json!({"name":name,"error":e.to_string()}),
        }
    }).collect();
    Ok(json!({"default_output":default,"outputs":devices}))
}

pub fn play(session: &Session, seconds: f64, volume: f64) -> Result<Value, String> {
    session.validate()?;
    crate::render::validate_duration(seconds)?;
    let device = cpal::default_host()
        .default_output_device()
        .ok_or("no default output device")?;
    let selected = device.default_output_config().map_err(|e| e.to_string())?;
    let format = selected.sample_format();
    let config: cpal::StreamConfig = selected.into();
    let renderer = PlaybackBuffer::prepare(
        session,
        config.sample_rate.0,
        config.channels as usize,
        seconds,
        volume,
    )?;
    let name = device.name().map_err(|e| e.to_string())?;
    let stats = Arc::new(Stats::default());
    stats.volume.store(1.0_f64.to_bits(), Relaxed);
    let stream = match format {
        cpal::SampleFormat::F32 => stream::<f32>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::F64 => stream::<f64>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::I16 => stream::<i16>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::U16 => stream::<u16>(&device, &config, renderer, stats.clone()),
        other => {
            return Err(format!(
                "unsupported default device sample format: {other:?}"
            ));
        }
    }?;
    eprintln!(
        "Playing on {name}: {} Hz, {} channels, {format:?}, {seconds}s, volume {volume}. Ctrl+C stops.",
        config.sample_rate.0, config.channels
    );
    stream.play().map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_secs_f64(seconds + 3.0);
    while !stats.done.load(Relaxed) && !stats.error.load(Relaxed) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(5));
    }
    let completed = stats.done.load(Relaxed);
    if completed {
        // Allow the last submitted buffer to reach the device before tearing down.
        let tail = stats.max_frames.load(Relaxed) as f64 / f64::from(config.sample_rate.0);
        std::thread::sleep(Duration::from_secs_f64(tail.min(1.0) + 0.1));
    }
    drop(stream);
    if stats.error.load(Relaxed) {
        return Err("audio stream failed or returned malformed buffers; playback stopped".into());
    }
    if !completed {
        return Err("audio callback timed out; playback stopped".into());
    }
    Ok(json!({
        "device":name, "session_sample_rate":session.sample_rate, "device_sample_rate":config.sample_rate.0,
        "channels":config.channels,"format":format!("{format:?}"),"volume":volume,
        "submitted_frames":stats.frames.load(Relaxed),"callbacks":stats.calls.load(Relaxed),
        "max_callback_frames":stats.max_frames.load(Relaxed),"max_render_microseconds":stats.max_ns.load(Relaxed) as f64 / 1000.0,
        "callbacks_over_buffer_budget":stats.over_budget.load(Relaxed),"stream_released":true
    }))
}

fn stream<T: SizedSample + FromSample<f64>>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    mut renderer: PlaybackBuffer,
    stats: Arc<Stats>,
) -> Result<cpal::Stream, String> {
    let errors = stats.clone();
    let channels = config.channels as usize;
    let rate = u64::from(config.sample_rate.0);
    device
        .build_output_stream(
            config,
            move |output: &mut [T], _| {
                let start = Instant::now();
                if stats.error.load(Relaxed) || stats.done.load(Relaxed) {
                    output.fill(T::EQUILIBRIUM);
                    stats.level.store(0, Relaxed);
                    return;
                }
                let command = stats.command.load(Acquire);
                if command != 0 {
                    let start = stats.command_start.load(Relaxed);
                    let end = stats.command_end.load(Relaxed);
                    // The owner validated all values before publishing. Successful
                    // engine operations use only fixed storage and scalar state.
                    let result = match command {
                        1 => renderer.seek(start),
                        2 => renderer.set_loop(Some((start, end))),
                        _ => renderer.set_loop(None),
                    };
                    if result.is_err() {
                        stats.error.store(true, Relaxed);
                        output.fill(T::EQUILIBRIUM);
                        stats.command.store(0, Release);
                        return;
                    }
                    stats.timeline.store(renderer.frame_position(), Relaxed);
                    stats.command.store(0, Release);
                }
                if stats.paused.load(Relaxed) {
                    output.fill(T::EQUILIBRIUM);
                    stats.observed.store(2, Relaxed);
                    stats.level.store(0, Relaxed);
                    return;
                }
                stats.observed.store(1, Relaxed);
                let frames = output.len() / channels;
                let volume = f64::from_bits(stats.volume.load(Relaxed));
                let peak = std::cell::Cell::new(0.0_f64);
                match renderer.fill(output, |sample| {
                    let sample = sample * volume;
                    peak.set(peak.get().max(sample.abs()));
                    T::from_sample(sample)
                }) {
                    Ok(rendered) => {
                        stats.frames.fetch_add(rendered, Relaxed);
                        stats.timeline.store(renderer.frame_position(), Relaxed);
                    }
                    Err(_) => {
                        stats.error.store(true, Relaxed);
                    }
                }
                stats.level.store(peak.get().to_bits(), Relaxed);
                stats.calls.fetch_add(1, Relaxed);
                stats.max_frames.fetch_max(frames as u64, Relaxed);
                let nanos = start.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64;
                stats.max_ns.fetch_max(nanos, Relaxed);
                if nanos > frames as u64 * 1_000_000_000 / rate {
                    stats.over_budget.fetch_add(1, Relaxed);
                }
                if renderer.remaining_frames() == 0 {
                    stats.done.store(true, Relaxed);
                }
            },
            move |_| {
                errors.error.store(true, Relaxed);
            },
            Some(Duration::from_secs(5)),
        )
        .map_err(|e| e.to_string())
}

// The command thread owns all CPAL lifecycle operations. The audio callback only
// communicates through scalar atomics, never this channel.
#[derive(Default)]
pub struct Transport {
    sender: Option<std::sync::mpsc::SyncSender<Envelope>>,
}

enum Action {
    Play(Session, f64, f64),
    Pause,
    Resume,
    Stop,
    Volume(f64),
    Seek(u64),
    Loop(Option<(u64, u64)>),
    Status,
}
struct Envelope {
    action: Action,
    reply: std::sync::mpsc::SyncSender<Result<Value, String>>,
}
struct Active {
    _stream: cpal::Stream,
    stats: Arc<Stats>,
    device: String,
    rate: u32,
    deadline: Instant,
    drain: Option<Instant>,
    loop_region: Option<(u64, u64)>,
}
impl Active {
    fn start(session: &Session, seconds: f64, volume: f64) -> Result<Self, String> {
        session.validate()?;
        crate::render::validate_duration(seconds)?;
        validate_volume(volume)?;
        let device = cpal::default_host()
            .default_output_device()
            .ok_or("no default output device")?;
        let selected = device.default_output_config().map_err(|e| e.to_string())?;
        let format = selected.sample_format();
        let config: cpal::StreamConfig = selected.into();
        let renderer = PlaybackBuffer::prepare(
            session,
            config.sample_rate.0,
            config.channels as usize,
            seconds,
            1.0,
        )?;
        let stats = Arc::new(Stats::default());
        stats.volume.store(volume.to_bits(), Relaxed);
        let stream = match format {
            cpal::SampleFormat::F32 => stream::<f32>(&device, &config, renderer, stats.clone()),
            cpal::SampleFormat::F64 => stream::<f64>(&device, &config, renderer, stats.clone()),
            cpal::SampleFormat::I16 => stream::<i16>(&device, &config, renderer, stats.clone()),
            cpal::SampleFormat::U16 => stream::<u16>(&device, &config, renderer, stats.clone()),
            _ => return Err(format!("unsupported device format: {format:?}")),
        }?;
        let name = device.name().map_err(|e| e.to_string())?;
        stream.play().map_err(|e| e.to_string())?;
        Ok(Self {
            _stream: stream,
            stats,
            device: name,
            rate: config.sample_rate.0,
            deadline: Instant::now() + Duration::from_secs_f64(seconds),
            drain: None,
            loop_region: None,
        })
    }
    fn snapshot(&self) -> Value {
        let pending = self.stats.command.load(Acquire) != 0;
        let observed = self.stats.observed.load(Relaxed);
        let paused = self.stats.paused.load(Relaxed);
        let state = match (paused, observed) {
            (true, 2) => "paused",
            (false, 1) => "playing",
            _ => "starting",
        };
        json!({"state":state,"device":self.device,"sample_rate":self.rate,
            "level":f64::from_bits(self.stats.level.load(Relaxed)),
            "submitted_frames":self.stats.frames.load(Relaxed),
            "timeline_frame":self.stats.timeline.load(Relaxed),
            "callbacks":self.stats.calls.load(Relaxed),
            "max_render_microseconds":self.stats.max_ns.load(Relaxed) as f64 / 1000.0,
            "callbacks_over_buffer_budget":self.stats.over_budget.load(Relaxed),
            "timeline_command_pending":pending,
            "loop_region":self.loop_region.map(|(start,end)| json!({"start_frame":start,"end_frame":end})),
            "volume":f64::from_bits(self.stats.volume.load(Relaxed))})
    }
}
fn validate_volume(volume: f64) -> Result<(), String> {
    if !volume.is_finite() || !(0.0..=1.0).contains(&volume) {
        Err("volume must be between 0 and 1".into())
    } else {
        Ok(())
    }
}
impl Transport {
    fn request(&mut self, action: Action) -> Result<Value, String> {
        if self.sender.is_none() {
            if matches!(action, Action::Stop | Action::Status) {
                return Ok(json!({"state":"stopped","level":0}));
            }
            let (sender, receiver) = std::sync::mpsc::sync_channel::<Envelope>(8);
            std::thread::Builder::new()
                .name("daw-audio-owner".into())
                .spawn(move || owner(receiver))
                .map_err(|e| e.to_string())?;
            self.sender = Some(sender);
        }
        let (reply, result) = std::sync::mpsc::sync_channel(1);
        self.sender
            .as_ref()
            .unwrap()
            .try_send(Envelope { action, reply })
            .map_err(|e| format!("native control unavailable: {e}"))?;
        result
            .recv_timeout(Duration::from_secs(10))
            .map_err(|_| "native control timed out; restart the engine".to_string())?
    }
    pub fn start(&mut self, session: &Session, seconds: f64, volume: f64) -> Result<Value, String> {
        session.validate()?;
        crate::render::validate_duration(seconds)?;
        validate_volume(volume)?;
        self.request(Action::Play(session.clone(), seconds, volume))
    }
    pub fn seek(&mut self, frame: u64) -> Result<Value, String> {
        if frame > crate::session::MAX_FRAME {
            return Err("frame exceeds timeline limit".into());
        }
        self.request(Action::Seek(frame))
    }
    pub fn set_loop(&mut self, region: Option<(u64, u64)>) -> Result<Value, String> {
        if region.is_some_and(|(start, end)| start >= end || end > crate::session::MAX_FRAME) {
            return Err("invalid loop region".into());
        }
        self.request(Action::Loop(region))
    }
    pub fn pause(&mut self) -> Result<Value, String> {
        self.request(Action::Pause)
    }
    pub fn resume(&mut self) -> Result<Value, String> {
        self.request(Action::Resume)
    }
    pub fn stop(&mut self) -> Result<Value, String> {
        self.request(Action::Stop)
    }
    pub fn status(&mut self) -> Result<Value, String> {
        self.request(Action::Status)
    }
    pub fn volume(&mut self, volume: f64) -> Result<Value, String> {
        validate_volume(volume)?;
        self.request(Action::Volume(volume))
    }
}
fn owner(receiver: std::sync::mpsc::Receiver<Envelope>) {
    let mut active: Option<Active> = None;
    let mut idle = json!({"state":"stopped","level":0});
    loop {
        if let Some(current) = active.as_mut() {
            let now = Instant::now();
            if current.stats.error.load(Relaxed) {
                active = None;
                idle = json!({"state":"error","level":0,"error":"Audio device failed. Playback stopped."});
            } else {
                if now >= current.deadline {
                    current.stats.done.store(true, Relaxed);
                }
                if current.stats.done.load(Relaxed) && current.drain.is_none() {
                    let tail =
                        current.stats.max_frames.load(Relaxed) as f64 / f64::from(current.rate);
                    current.drain = Some(now + Duration::from_secs_f64(tail.min(1.0) + 0.1));
                }
                if current.drain.is_some_and(|deadline| now >= deadline) {
                    active = None;
                    idle = json!({"state":"stopped","level":0});
                }
            }
        }
        let envelope = match receiver.recv_timeout(Duration::from_millis(25)) {
            Ok(envelope) => envelope,
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => continue,
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
        };
        let result: Result<(), String> = (|| {
            match envelope.action {
                Action::Play(session, seconds, volume) => {
                    if active.is_some() {
                        return Err("stop native playback before starting again".into());
                    }
                    active = Some(Active::start(&session, seconds, volume)?);
                }
                Action::Stop => {
                    active = None;
                    idle = json!({"state":"stopped","level":0});
                }
                Action::Status => {}
                Action::Pause | Action::Resume => {
                    let current = active.as_ref().ok_or("native playback is stopped")?;
                    current.stats.observed.store(0, Relaxed);
                    current
                        .stats
                        .paused
                        .store(matches!(envelope.action, Action::Pause), Relaxed);
                }
                Action::Seek(_) | Action::Loop(_) => {
                    let current = active.as_mut().ok_or("native playback is stopped")?;
                    if current.stats.done.load(Relaxed) || current.drain.is_some() {
                        return Err("native playback is finishing".into());
                    }
                    if current.stats.command.load(Acquire) != 0 {
                        return Err("timeline command pending; poll status before retrying".into());
                    }
                    let (tag, start, end) = match envelope.action {
                        Action::Seek(frame) => (1, frame, 0),
                        Action::Loop(region) => {
                            current.loop_region = region;
                            region.map_or((3, 0, 0), |(start, end)| (2, start, end))
                        }
                        _ => unreachable!(),
                    };
                    current.stats.command_start.store(start, Relaxed);
                    current.stats.command_end.store(end, Relaxed);
                    current.stats.command.store(tag, Release);
                }
                Action::Volume(volume) => {
                    if let Some(current) = active.as_ref() {
                        current.stats.volume.store(volume.to_bits(), Relaxed);
                    }
                }
            }
            Ok(())
        })();
        let result = result.map(|()| {
            active
                .as_ref()
                .map_or_else(|| idle.clone(), Active::snapshot)
        });
        if envelope.reply.send(result).is_err() {
            active = None;
            idle = json!({"state":"error","level":0,"error":"Native command caller disconnected; playback stopped."});
        }
    }
    // Dropping the active stream on this owner thread releases hardware on EOF.
}
