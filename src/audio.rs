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
    pre_monitor_peak: AtomicU64,
    source_digest: AtomicU64,
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
    #[cfg(all(feature = "vst3-live", target_os = "macos"))]
    let (renderer, mut plugin_worker) = PlaybackBuffer::prepare_native(
        session,
        config.sample_rate.0,
        config.channels as usize,
        seconds,
        volume,
    )?;
    #[cfg(not(all(feature = "vst3-live", target_os = "macos")))]
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
    #[cfg(all(feature = "vst3-live", target_os = "macos"))]
    let underruns = if let Some(worker) = &mut plugin_worker {
        let count = worker.underruns();
        worker.finish()?;
        count
    } else {
        0
    };
    #[cfg(not(all(feature = "vst3-live", target_os = "macos")))]
    let underruns = 0u64;
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
        "callbacks_over_buffer_budget":stats.over_budget.load(Relaxed),"stream_released":true,"plugin_worker_underruns":underruns
    }))
}

/// Finite SC routing diagnostic. The caller owns the server process; this
/// function owns only its queue consumer, worker and hardware stream.
pub fn play_sc_stream(
    path: &std::path::Path,
    nonce: u64,
    blocks: u32,
    volume: f64,
    gain: f64,
) -> Result<Value, String> {
    if !volume.is_finite() || !(0.0..=1.0).contains(&volume) {
        return Err("volume must be finite and between 0 and 1".into());
    }
    let selected = sc_device()?;
    let (consumer, worker) = crate::sc_stream::start(path, nonce, blocks, gain)?;
    play_sc_consumer(consumer, worker, u64::from(blocks) * 64, volume, selected)
}

pub fn play_sc_session(session: &Session, seconds: f64, volume: f64) -> Result<Value, String> {
    validate_volume(volume)?;
    let frames = crate::sc_session::validate(session, seconds)?;
    let selected = sc_device()?;
    let _interrupt = crate::sc_session::InterruptGuard::install()?;
    let (consumer, worker) = crate::sc_session::start(session, seconds)?;
    let mut result = play_sc_consumer(consumer, worker, frames, volume, selected)?;
    result["saved_session"] = json!(true);
    result["source_preparation"] = json!("owned_live_sc");
    Ok(result)
}

fn sc_device() -> Result<(cpal::Device, cpal::SampleFormat, cpal::StreamConfig), String> {
    let device = cpal::default_host()
        .default_output_device()
        .ok_or("no default output device")?;
    let selected = device.default_output_config().map_err(|e| e.to_string())?;
    let format = selected.sample_format();
    let config: cpal::StreamConfig = selected.into();
    if config.sample_rate.0 != 48000 {
        return Err("SC live playback requires a 48000 Hz output device".into());
    }
    Ok((device, format, config))
}

fn play_sc_consumer(
    consumer: crate::live_ring::Consumer,
    mut worker: crate::sc_stream::Worker,
    frames: u64,
    volume: f64,
    selected: (cpal::Device, cpal::SampleFormat, cpal::StreamConfig),
) -> Result<Value, String> {
    let (device, format, config) = selected;
    let renderer = PlaybackBuffer::from_stream(consumer, frames, config.channels as usize, 1.0)?;
    let stats = Arc::new(Stats::default());
    stats.volume.store(volume.to_bits(), Relaxed);
    // CoreAudio can call the renderer during stream construction, before play.
    // Hold callbacks silent until the source queue has completed prefill.
    stats.paused.store(true, Relaxed);
    let stream = match format {
        cpal::SampleFormat::F32 => stream::<f32>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::F64 => stream::<f64>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::I16 => stream::<i16>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::U16 => stream::<u16>(&device, &config, renderer, stats.clone()),
        other => {
            return Err(format!(
                "unsupported SC diagnostic device format: {other:?}"
            ));
        }
    }?;
    // Diagnostic callers may create their source now; saved-session callers
    // arm their paused sources next. This marker is not an audio acknowledgment.
    eprintln!("DAW_SC_STREAM_READY");
    worker.begin()?;
    worker.wait_ready()?;
    stats.paused.store(false, Release);
    stream.play().map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_secs_f64(frames as f64 / 48000.0 + 3.0);
    while !stats.done.load(Relaxed)
        && !stats.error.load(Relaxed)
        && !crate::sc_session::interrupted()
        && Instant::now() < deadline
    {
        std::thread::sleep(Duration::from_millis(1));
    }
    if stats.done.load(Relaxed) && !crate::sc_session::interrupted() {
        let tail = stats.max_frames.load(Relaxed) as f64 / 48000.0;
        std::thread::sleep(Duration::from_secs_f64(tail.min(1.0) + 0.1));
    }
    drop(stream);
    let report = worker.finish()?;
    if crate::sc_session::interrupted() {
        return Err("SC session interrupted; owned servers released".into());
    }
    if stats.error.load(Relaxed) {
        return Err("SC native callback failed; stream released".into());
    }
    if !stats.done.load(Relaxed) {
        return Err("SC native callback timed out; stream released".into());
    }
    Ok(
        json!({"sample_rate":48000,"channels":config.channels,"volume":volume,
        "source":report,"submitted_frames":stats.frames.load(Relaxed),"callbacks":stats.calls.load(Relaxed),
        "underruns":worker.control.underruns(),"last_underrun_frame":worker.control.last_underrun_frame(),"callback_signal_peak":f64::from_bits(stats.pre_monitor_peak.load(Relaxed)),"callback_source_digest":format!("{:016x}",stats.source_digest.load(Relaxed)),"max_render_microseconds":stats.max_ns.load(Relaxed) as f64 / 1000.0,
        "callbacks_over_buffer_budget":stats.over_budget.load(Relaxed),"stream_released":true,
        "acoustic_verified":false,"session_transport":false}),
    )
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
                let pre_monitor_peak = std::cell::Cell::new(0.0_f64);
                match renderer.fill(output, |sample| {
                    pre_monitor_peak.set(pre_monitor_peak.get().max(sample.abs()));
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
                stats.source_digest.store(renderer.source_digest(), Relaxed);
                stats.level.store(peak.get().to_bits(), Relaxed);
                stats
                    .pre_monitor_peak
                    .fetch_max(pre_monitor_peak.get().to_bits(), Relaxed);
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
    thread: Option<std::thread::JoinHandle<()>>,
}

enum Action {
    Play(Session, f64, f64),
    PlayLive(Session, f64, f64),
    SourceControl(crate::sc_session::ParameterChange),
    Pause,
    Resume,
    Stop,
    Volume(f64),
    Seek(u64),
    Loop(Option<(u64, u64)>),
    #[cfg(all(feature = "vst3-live", target_os = "macos"))]
    Parameter(crate::live_plugins::ParameterChange),
    Status,
}
struct Envelope {
    action: Action,
    reply: std::sync::mpsc::SyncSender<Result<Value, String>>,
}
struct Active {
    _stream: cpal::Stream,
    #[cfg(all(feature = "vst3-live", target_os = "macos"))]
    plugin_worker: Option<crate::live_plugins::WorkerGuard>,
    sc_worker: Option<crate::sc_stream::Worker>,
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
        #[cfg(all(feature = "vst3-live", target_os = "macos"))]
        let (renderer, plugin_worker) = PlaybackBuffer::prepare_native(
            session,
            config.sample_rate.0,
            config.channels as usize,
            seconds,
            1.0,
        )?;
        #[cfg(not(all(feature = "vst3-live", target_os = "macos")))]
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
            #[cfg(all(feature = "vst3-live", target_os = "macos"))]
            plugin_worker,
            sc_worker: None,
            stats,
            device: name,
            rate: config.sample_rate.0,
            deadline: Instant::now() + Duration::from_secs_f64(seconds),
            drain: None,
            loop_region: None,
        })
    }
    fn start_live(session: &Session, seconds: f64, volume: f64) -> Result<Self, String> {
        let frames = crate::sc_session::validate(session, seconds)?;
        validate_volume(volume)?;
        let (device, format, config) = sc_device()?;
        let name = device.name().map_err(|e| e.to_string())?;
        let (consumer, mut worker) = crate::sc_session::start(session, seconds)?;
        let renderer =
            PlaybackBuffer::from_stream(consumer, frames, config.channels as usize, 1.0)?;
        let stats = Arc::new(Stats::default());
        stats.volume.store(volume.to_bits(), Relaxed);
        stats.paused.store(true, Relaxed);
        let stream = match format {
            cpal::SampleFormat::F32 => stream::<f32>(&device, &config, renderer, stats.clone()),
            cpal::SampleFormat::F64 => stream::<f64>(&device, &config, renderer, stats.clone()),
            cpal::SampleFormat::I16 => stream::<i16>(&device, &config, renderer, stats.clone()),
            cpal::SampleFormat::U16 => stream::<u16>(&device, &config, renderer, stats.clone()),
            _ => return Err(format!("unsupported device format: {format:?}")),
        }?;
        worker.begin()?;
        worker.wait_ready()?;
        stats.paused.store(false, Release);
        stream.play().map_err(|e| e.to_string())?;
        Ok(Self {
            _stream: stream,
            #[cfg(all(feature = "vst3-live", target_os = "macos"))]
            plugin_worker: None,
            sc_worker: Some(worker),
            stats,
            device: name,
            rate: 48000,
            // A source failure cannot be disguised as a wall-clock completion.
            deadline: Instant::now() + Duration::from_secs_f64(seconds + 3.0),
            drain: None,
            loop_region: None,
        })
    }
    fn release(mut self) -> Result<Value, String> {
        let mut report = self.snapshot();
        // Stream destruction releases the callback consumer before stopping DSP.
        drop(self._stream);
        // Collect final scalar telemetry only after callbacks have stopped.
        report["submitted_frames"] = json!(self.stats.frames.load(Relaxed));
        report["timeline_frame"] = json!(self.stats.timeline.load(Relaxed));
        report["callbacks"] = json!(self.stats.calls.load(Relaxed));
        if let Some(worker) = &self.sc_worker {
            report["live_source_underruns"] = json!(worker.control.underruns());
            report["callback_signal_peak"] =
                json!(f64::from_bits(self.stats.pre_monitor_peak.load(Relaxed)));
            report["callback_source_digest"] =
                json!(format!("{:016x}", self.stats.source_digest.load(Relaxed)));
        }
        #[cfg(all(feature = "vst3-live", target_os = "macos"))]
        if let Some(mut worker) = self.plugin_worker {
            worker.finish()?;
        }
        if let Some(mut worker) = self.sc_worker.take() {
            report["source"] = worker.finish()?;
            report["source_control_update"] = worker
                .control_status(self.stats.timeline.load(Relaxed))
                .unwrap();
        }
        report["state"] = json!("stopped");
        report["level"] = json!(0);
        report["resources_released"] = json!(true);
        Ok(report)
    }
    fn snapshot(&mut self) -> Value {
        #[cfg(all(feature = "vst3-live", target_os = "macos"))]
        let underruns = self
            .plugin_worker
            .as_ref()
            .map_or(0, |worker| worker.underruns());
        #[cfg(not(all(feature = "vst3-live", target_os = "macos")))]
        let underruns = 0u64;
        #[cfg(all(feature = "vst3-live", target_os = "macos"))]
        let parameter_status = self
            .plugin_worker
            .as_mut()
            .map(|worker| worker.parameter_status());
        #[cfg(not(all(feature = "vst3-live", target_os = "macos")))]
        let parameter_status: Option<Value> = None;
        let pending = self.stats.command.load(Acquire) != 0;
        let observed = self.stats.observed.load(Relaxed);
        let paused = self.stats.paused.load(Relaxed);
        let state = match (paused, observed) {
            (true, 2) => "paused",
            (false, 1) => "playing",
            _ => "starting",
        };
        let mut report = json!({"state":state,"device":self.device,"sample_rate":self.rate,
            "level":f64::from_bits(self.stats.level.load(Relaxed)),
            "submitted_frames":self.stats.frames.load(Relaxed),
            "timeline_frame":self.stats.timeline.load(Relaxed),
            "callbacks":self.stats.calls.load(Relaxed),
            "max_render_microseconds":self.stats.max_ns.load(Relaxed) as f64 / 1000.0,
            "callbacks_over_buffer_budget":self.stats.over_budget.load(Relaxed),
            "timeline_command_pending":pending,"plugin_worker_underruns":underruns,"plugin_parameter_update":parameter_status,
            "loop_region":self.loop_region.map(|(start,end)| json!({"start_frame":start,"end_frame":end})),
            "volume":f64::from_bits(self.stats.volume.load(Relaxed))});
        report["source_mode"] = json!(if self.sc_worker.is_some() {
            "live"
        } else {
            "prepared"
        });
        if let Some(worker) = &mut self.sc_worker {
            report["source_control_update"] = worker
                .control_status(self.stats.timeline.load(Relaxed))
                .unwrap();
            report["runtime"] = worker
                .metadata
                .get("runtime")
                .cloned()
                .unwrap_or(json!("supercollider"));
            report["startup"] = json!("prefilled");
            report["source"] = worker.metadata.clone();
            report["live_source_underruns"] = json!(worker.control.underruns());
            report["callback_signal_peak"] =
                json!(f64::from_bits(self.stats.pre_monitor_peak.load(Relaxed)));
            report["callback_source_digest"] =
                json!(format!("{:016x}", self.stats.source_digest.load(Relaxed)));
        }
        report
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
            self.thread = Some(
                std::thread::Builder::new()
                    .name("daw-audio-owner".into())
                    .spawn(move || owner(receiver))
                    .map_err(|e| e.to_string())?,
            );
            self.sender = Some(sender);
        }
        let timeout = match &action {
            Action::PlayLive(..) => 60,
            Action::Stop => 25,
            _ => 10,
        };
        let (reply, result) = std::sync::mpsc::sync_channel(1);
        self.sender
            .as_ref()
            .unwrap()
            .try_send(Envelope { action, reply })
            .map_err(|e| format!("native control unavailable: {e}"))?;
        result
            .recv_timeout(Duration::from_secs(timeout))
            .map_err(|_| "native control timed out; restart the engine".to_string())?
    }
    pub fn start(&mut self, session: &Session, seconds: f64, volume: f64) -> Result<Value, String> {
        session.validate()?;
        crate::render::validate_duration(seconds)?;
        validate_volume(volume)?;
        self.request(Action::Play(session.clone(), seconds, volume))
    }
    pub fn start_live(
        &mut self,
        session: &Session,
        seconds: f64,
        volume: f64,
    ) -> Result<Value, String> {
        crate::sc_session::validate(session, seconds)?;
        validate_volume(volume)?;
        self.request(Action::PlayLive(session.clone(), seconds, volume))
    }
    #[cfg(all(feature = "vst3-live", target_os = "macos"))]
    pub(crate) fn parameter(
        &mut self,
        change: crate::live_plugins::ParameterChange,
    ) -> Result<Value, String> {
        self.request(Action::Parameter(change))
    }
    pub(crate) fn source_control(
        &mut self,
        change: crate::sc_session::ParameterChange,
    ) -> Result<Value, String> {
        self.request(Action::SourceControl(change))
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
fn stopped_error(live: bool, error: String) -> Value {
    if live {
        json!({"state":"stopped","level":0,"source_mode":"live","resources_released":!error.contains("stop timed out"),"error":{"code":"runtime_error","message":error}})
    } else {
        json!({"state":"error","level":0,"error":error})
    }
}
fn owner(receiver: std::sync::mpsc::Receiver<Envelope>) {
    let mut active: Option<Active> = None;
    let mut idle = json!({"state":"stopped","level":0});
    loop {
        if let Some(current) = active.as_mut() {
            let now = Instant::now();
            if current.stats.error.load(Relaxed)
                || current
                    .sc_worker
                    .as_ref()
                    .is_some_and(|worker| worker.control.failed())
            {
                let live = current.sc_worker.is_some();
                let result = active.take().expect("active stream").release();
                let error = result
                    .err()
                    .unwrap_or_else(|| "Audio device failed. Playback stopped.".into());
                idle = stopped_error(live, error);
            } else {
                if now >= current.deadline && current.sc_worker.is_some() {
                    current.stats.error.store(true, Relaxed);
                } else if now >= current.deadline {
                    current.stats.done.store(true, Relaxed);
                }
                if current.stats.done.load(Relaxed) && current.drain.is_none() {
                    let tail =
                        current.stats.max_frames.load(Relaxed) as f64 / f64::from(current.rate);
                    current.drain = Some(now + Duration::from_secs_f64(tail.min(1.0) + 0.1));
                }
                if current.drain.is_some_and(|deadline| now >= deadline) {
                    let live = current.sc_worker.is_some();
                    let result = active.take().expect("active stream").release();
                    idle = match result {
                        Ok(report) => report,
                        Err(error) => stopped_error(live, error),
                    };
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
                Action::PlayLive(session, seconds, volume) => {
                    if active.is_some() {
                        return Err("stop native playback before starting again".into());
                    }
                    active = Some(Active::start_live(&session, seconds, volume)?);
                }
                Action::Stop => {
                    if let Some(current) = active.take() {
                        let live = current.sc_worker.is_some();
                        match current.release() {
                            Ok(report) => idle = report,
                            Err(error) => {
                                idle = stopped_error(live, error.clone());
                                return Err(error);
                            }
                        }
                    }
                    if idle["state"] != "stopped" {
                        idle = json!({"state":"stopped","level":0});
                    }
                }
                #[cfg(all(feature = "vst3-live", target_os = "macos"))]
                Action::Parameter(change) => {
                    let current = active.as_mut().ok_or("native playback is stopped")?;
                    if current.stats.done.load(Relaxed)
                        || current.stats.error.load(Relaxed)
                        || current.drain.is_some()
                        || Instant::now() >= current.deadline
                    {
                        return Err("native playback is finishing".into());
                    }
                    current
                        .plugin_worker
                        .as_mut()
                        .ok_or("native playback has no live plugins")?
                        .queue_parameter(change)?;
                }
                Action::SourceControl(change) => {
                    let current = active.as_mut().ok_or("native playback is stopped")?;
                    if current.stats.done.load(Relaxed)
                        || current.stats.error.load(Relaxed)
                        || current.drain.is_some()
                    {
                        return Err("native playback is finishing".into());
                    }
                    current
                        .sc_worker
                        .as_mut()
                        .ok_or("native playback has no live SC sources")?
                        .queue_control(change)?;
                }
                Action::Status => {}
                Action::Pause | Action::Resume => {
                    let current = active.as_ref().ok_or("native playback is stopped")?;
                    if current.sc_worker.is_some() {
                        return Err("live SuperCollider playback does not support pause or resume; stop and restart".into());
                    }
                    current.stats.observed.store(0, Relaxed);
                    current
                        .stats
                        .paused
                        .store(matches!(envelope.action, Action::Pause), Relaxed);
                }
                Action::Seek(_) | Action::Loop(_) => {
                    let current = active.as_mut().ok_or("native playback is stopped")?;
                    if current.sc_worker.is_some() {
                        return Err(
                            "live SuperCollider playback does not support seek or loops".into()
                        );
                    }
                    #[cfg(all(feature = "vst3-live", target_os = "macos"))]
                    if current.plugin_worker.is_some() {
                        return Err("live VST3 playback does not support seek or loops".into());
                    }
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
                .as_mut()
                .map_or_else(|| idle.clone(), Active::snapshot)
        });
        if envelope.reply.send(result).is_err() {
            if let Some(current) = active.take() {
                let _ = current.release();
            }
            idle = json!({"state":"error","level":0,"error":"Native command caller disconnected; playback stopped."});
        }
    }
    if let Some(current) = active.take() {
        let _ = current.release();
    }
}

impl Drop for Transport {
    fn drop(&mut self) {
        if self.sender.is_some() {
            let _ = self.stop();
        }
        self.sender.take();
        if let Some(thread) = self.thread.take() {
            let deadline = Instant::now() + Duration::from_secs(60);
            while !thread.is_finished() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(1));
            }
            if thread.is_finished() {
                let _ = thread.join();
            }
        }
    }
}
