//! Owned live SC/Csound/Pd source playback from saved v6/v7/v8 models. Each source
//! gets an isolated process/queue; callbacks consume only the final fixed ring.
use crate::{
    effects::PreparedChain,
    engine::Engine,
    live_ring::{self, Consumer, Frame},
    sc_server::{Arg, Server},
    sc_stream::{self, Queue, Worker},
    session::{Device, Effect, Session, Track},
    synthdef,
};
use serde_json::{Value, json};
use std::{
    io::Read,
    sync::mpsc,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

// This finite CLI owns child processes: graceful interruption must run their
// destructors instead of leaving servers behind when the CLI receives SIGINT.
static INTERRUPTED: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
pub(crate) fn interrupted() -> bool {
    INTERRUPTED.load(std::sync::atomic::Ordering::Relaxed)
}
extern "C" fn on_signal(_: i32) {
    INTERRUPTED.store(true, std::sync::atomic::Ordering::Relaxed);
}
unsafe extern "C" {
    fn signal(number: i32, handler: usize) -> usize;
}
pub(crate) struct InterruptGuard {
    old: [usize; 2],
}
impl InterruptGuard {
    pub(crate) fn install() -> Result<Self, String> {
        INTERRUPTED.store(false, std::sync::atomic::Ordering::Relaxed);
        // SAFETY: macOS signal() installs a C handler that only stores a lock-free
        // atomic. This synchronous CLI owns the installation and restores it.
        unsafe {
            let int = signal(2, on_signal as *const () as usize);
            if int == usize::MAX {
                return Err("cannot install SC interruption handler".into());
            }
            let term = signal(15, on_signal as *const () as usize);
            if term == usize::MAX {
                signal(2, int);
                return Err("cannot install SC termination handler".into());
            }
            Ok(Self { old: [int, term] })
        }
    }
}
impl Drop for InterruptGuard {
    fn drop(&mut self) {
        // SAFETY: restore the prior process handlers after all owned cleanup.
        unsafe {
            signal(2, self.old[0]);
            signal(15, self.old[1]);
        }
    }
}

pub(crate) const MAX_PENDING_CONTROLS: usize = 8;
#[derive(Clone)]
pub(crate) struct ParameterChange {
    pub(crate) track_id: String,
    pub(crate) target: ControlTarget,
    pub(crate) revision: u64,
}
#[derive(Clone)]
pub(crate) enum ControlTarget {
    Supercollider { index: u32, values: Vec<f32> },
    Csound { name: String, value: f64 },
}
pub(crate) struct Updates {
    sender: mpsc::SyncSender<ParameterChange>,
    acks: mpsc::Receiver<(u64, u64)>,
    pending: usize,
    applied: Option<(u64, u64)>,
}
impl Updates {
    fn collect(&mut self) {
        while let Ok(ack) = self.acks.try_recv() {
            self.pending -= 1;
            self.applied = Some(ack);
        }
    }
    pub(crate) fn queue(&mut self, change: ParameterChange) -> Result<(), String> {
        self.collect();
        if self.pending >= MAX_PENDING_CONTROLS {
            return Err("source control queue full; poll status before retrying".into());
        }
        self.sender
            .try_send(change)
            .map_err(|_| "source control queue unavailable".to_string())?;
        self.pending += 1;
        Ok(())
    }
    pub(crate) fn status(&mut self, callback_frame: u64) -> Value {
        self.collect();
        json!({"pending":self.pending,"applied_revision":self.applied.map(|(revision,_)|revision.to_string()),"applied_frame":self.applied.map(|(_,frame)|frame),"callback_observed":self.applied.is_some_and(|(_,frame)|callback_frame>=frame)})
    }
}

const SOURCE_NAME: &str = "daw_sc_source";
const CAPTURE_NAME: &str = "daw_stream_capture";

fn renamed(bytes: &[u8]) -> Result<Vec<u8>, String> {
    synthdef::inspect(bytes)?;
    let mut result = bytes[..10].to_vec();
    result.push(SOURCE_NAME.len() as u8);
    result.extend(SOURCE_NAME.as_bytes());
    result.extend(&bytes[11 + usize::from(bytes[10])..]);
    synthdef::inspect(&result)?;
    Ok(result)
}
enum SourceProcess {
    Supercollider(Server),
    Csound(crate::csound_server::Server),
    Puredata(crate::puredata_server::Server),
}
impl SourceProcess {
    fn pid(&self) -> u32 {
        match self {
            Self::Supercollider(server) => server.pid(),
            Self::Csound(server) => server.pid(),
            Self::Puredata(server) => server.pid(),
        }
    }
    fn check(&mut self) -> Result<(), String> {
        match self {
            Self::Supercollider(server) => server.check(),
            Self::Csound(server) => server.check(),
            Self::Puredata(server) => server.check(),
        }
    }
    fn finite(&self) -> bool {
        matches!(self, Self::Csound(_) | Self::Puredata(_))
    }
    fn name(&self) -> &'static str {
        match self {
            Self::Supercollider(_) => "supercollider",
            Self::Csound(_) => "csound",
            Self::Puredata(_) => "puredata",
        }
    }
    fn begin_control(&mut self, change: &ParameterChange) -> Result<(), String> {
        match (self, &change.target) {
            (Self::Supercollider(server), ControlTarget::Supercollider { index, values }) => {
                server.begin_control(*index, values)
            }
            (Self::Csound(server), ControlTarget::Csound { name, value }) => {
                server.begin_control(name, *value, change.revision)
            }
            _ => Err("live control target does not match its runtime".into()),
        }
    }
    fn poll_control(
        &mut self,
        change: &ParameterChange,
        queue: &Queue,
    ) -> Result<Option<u64>, String> {
        match (self, &change.target) {
            (Self::Supercollider(server), ControlTarget::Supercollider { index, values }) => {
                Ok(server
                    .poll_control(*index, values)?
                    .then_some((u64::from(queue.written()) + 1) * 64))
            }
            (Self::Csound(server), ControlTarget::Csound { value, .. }) => {
                let frame = server.poll_control(*value, change.revision)?;
                if frame.is_some_and(|frame| frame > u64::from(queue.written()) * 64) {
                    return Err("Csound acknowledgment precedes queue publication".into());
                }
                Ok(frame)
            }
            _ => Err("live control target does not match its runtime".into()),
        }
    }
}
struct Runtime {
    // Mapping is released before its server/temp files on all exit paths.
    queue: Queue,
    server: SourceProcess,
    track: Track,
    chain: PreparedChain,
}
impl Runtime {
    fn prepare(
        track: &Track,
        directory: &std::path::Path,
        index: usize,
        frames: u64,
    ) -> Result<Self, String> {
        if let Device::Puredata(source) = &track.device {
            let (server, queue) =
                crate::puredata_server::Server::start(source, directory, index, frames)?;
            return Ok(Self {
                queue,
                server: SourceProcess::Puredata(server),
                track: track.clone(),
                chain: PreparedChain::prepare(track),
            });
        }
        if let Device::Csound(source) = &track.device {
            let (server, queue) =
                crate::csound_server::Server::start(source, directory, index, frames)?;
            return Ok(Self {
                queue,
                server: SourceProcess::Csound(server),
                track: track.clone(),
                chain: PreparedChain::prepare(track),
            });
        }
        let Device::Supercollider(source) = &track.device else {
            return Err("expected SC source".into());
        };
        let mut random = [0; 8];
        std::fs::File::open("/dev/urandom")
            .and_then(|mut file| file.read_exact(&mut random))
            .map_err(|e| e.to_string())?;
        let nonce = u64::from_ne_bytes(random).max(1);
        let path = directory.join(format!("track-{index}.queue"));
        sc_stream::create_queue(&path, nonce)?;
        let mut server = Server::start(&path, nonce)?;
        server.done("/notify", &[Arg::Int(1)])?;
        server.node("/g_new", 1, &[Arg::Int(0), Arg::Int(0)])?;
        server.pause_group(1, true)?;
        let bytes = synthdef::decode_hex(&source.synthdef_hex)?;
        let metadata = synthdef::inspect(&bytes)?;
        server.definition(&renamed(&bytes)?)?;
        let capture = synthdef::decode_hex(
            include_str!("../native/supercollider/stream_capture.hex").trim(),
        )?;
        server.definition(&capture)?;
        server.done("/b_alloc", &[Arg::Int(0), Arg::Int(64), Arg::Int(2)])?;
        let mut controls = Vec::new();
        for control in &source.controls {
            let index = metadata
                .controls
                .iter()
                .find(|item| item.name == control.name)
                .ok_or("missing validated control")?
                .index;
            let values = control
                .points
                .first()
                .filter(|point| point.frame == 0)
                .map_or(&control.values, |point| &point.values);
            for (offset, &value) in values.iter().enumerate() {
                controls.extend([Arg::Int((index + offset) as i32), Arg::Float(value as f32)]);
            }
        }
        server.synth(SOURCE_NAME, 1000, 0, 1, &controls)?;
        server.synth(CAPTURE_NAME, 1001, 1, 1, &[])?;
        let queue = Queue::open(&path, nonce)?;
        Ok(Self {
            queue,
            server: SourceProcess::Supercollider(server),
            track: track.clone(),
            chain: PreparedChain::prepare(track),
        })
    }
    fn duration_gain(&self) -> (u64, f64) {
        match &self.track.device {
            Device::Supercollider(source) => (source.duration_frames, source.gain),
            Device::Csound(source) => (source.duration_frames, source.gain),
            Device::Puredata(source) => (source.duration_frames, source.gain),
            _ => unreachable!(),
        }
    }
    fn arm(&mut self, start: f64, frames: u64) -> Result<(), String> {
        let server = match &mut self.server {
            SourceProcess::Csound(server) => return server.arm(),
            SourceProcess::Puredata(server) => return server.arm(),
            SourceProcess::Supercollider(server) => server,
        };
        let Device::Supercollider(source) = &self.track.device else {
            unreachable!()
        };
        let metadata = synthdef::inspect(&synthdef::decode_hex(&source.synthdef_hex)?)?;
        server.schedule(start, "/n_run", &[Arg::Int(1), Arg::Int(1)])?;
        for control in &source.controls {
            let index = metadata
                .controls
                .iter()
                .find(|item| item.name == control.name)
                .unwrap()
                .index;
            for point in control
                .points
                .iter()
                .filter(|point| point.frame > 0 && point.frame < frames)
            {
                let mut values = vec![
                    Arg::Int(1000),
                    Arg::Int(index as i32),
                    Arg::Int(point.values.len() as i32),
                ];
                values.extend(point.values.iter().map(|&value| Arg::Float(value as f32)));
                server.schedule(start + point.frame as f64 / 48000.0, "/n_setn", &values)?;
            }
        }
        if source.duration_frames <= frames {
            server.schedule(
                start + source.duration_frames as f64 / 48000.0,
                "/n_free",
                &[Arg::Int(1000)],
            )?;
        }
        server.sync()?;
        Ok(())
    }
}

pub(crate) fn validate(session: &Session, seconds: f64) -> Result<u64, String> {
    session.validate()?;
    crate::render::validate_duration(seconds)?;
    if !matches!(session.schema_version, 6..=8) || session.sample_rate != 48000 || seconds > 10.0 {
        return Err(
            "live runtime sessions require schema v6/v7/v8, 48000 Hz and at most ten seconds"
                .into(),
        );
    }
    if !session.tracks.iter().any(|track| {
        matches!(
            track.device,
            Device::Supercollider(_) | Device::Csound(_) | Device::Puredata(_)
        )
    }) {
        return Err("live session requires a SuperCollider, Csound or Pure Data source".into());
    }
    if session
        .tracks
        .iter()
        .flat_map(|track| track.effects.as_deref().unwrap_or_default())
        .any(|effect| !matches!(effect, Effect::Gain { .. }))
    {
        return Err("live runtime effect routing currently supports gain only".into());
    }
    Ok((seconds * 48000.0).round() as u64)
}

pub(crate) fn start(session: &Session, seconds: f64) -> Result<(Consumer, Worker), String> {
    let frames = validate(session, seconds)?;
    let session = session.clone();
    let (mut producer, consumer, control) = live_ring::pair();
    let (opened, opening) = mpsc::sync_channel(1);
    let (ready, prepared) = mpsc::sync_channel(1);
    let (begin, starting) = mpsc::sync_channel(1);
    let (changes, receiving) = mpsc::sync_channel::<ParameterChange>(MAX_PENDING_CONTROLS);
    let (acknowledged, acknowledgements) = mpsc::sync_channel(MAX_PENDING_CONTROLS);
    let task = move || {
        let result: Result<Value, String> = (|| {
            let directory = tempfile::tempdir().map_err(|e| e.to_string())?;
            let mut builtin = session.clone();
            builtin.tracks.retain(|track| {
                !matches!(
                    track.device,
                    Device::Supercollider(_) | Device::Csound(_) | Device::Puredata(_)
                )
            });
            let mut engine = Engine::prepare(&builtin)?;
            let mut runtimes = Vec::new();
            for (index, track) in session.tracks.iter().enumerate() {
                if producer.stop_requested() || interrupted() {
                    return Err("SC session stopped during preparation".into());
                }
                if matches!(
                    track.device,
                    Device::Supercollider(_) | Device::Csound(_) | Device::Puredata(_)
                ) {
                    runtimes.push(Runtime::prepare(track, directory.path(), index, frames)?);
                }
            }
            let csound_count = runtimes
                .iter()
                .filter(|runtime| matches!(runtime.server, SourceProcess::Csound(_)))
                .count();
            let pd_count = runtimes
                .iter()
                .filter(|runtime| matches!(runtime.server, SourceProcess::Puredata(_)))
                .count();
            let runtime_name = if pd_count == runtimes.len() {
                "puredata"
            } else if csound_count == 0 && pd_count == 0 {
                "supercollider"
            } else if csound_count == runtimes.len() {
                "csound"
            } else {
                "mixed"
            };
            let source_processes: Vec<_> = runtimes.iter().map(|runtime| json!({"track_id":runtime.track.id,"runtime":runtime.server.name(),"pid":runtime.server.pid()})).collect();
            let _ = opened.send(Ok(json!({"owned_pids":runtimes.iter().map(|runtime|runtime.server.pid()).collect::<Vec<_>>(),"runtime_sources":runtimes.len(),"runtime":runtime_name,"source_processes":source_processes})));
            starting
                .recv_timeout(Duration::from_secs(5))
                .map_err(|_| "SC session start handshake timed out".to_string())?;
            let start = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|e| e.to_string())?
                .as_secs_f64()
                + 0.1;
            for runtime in &mut runtimes {
                runtime.arm(start, frames)?;
            }
            let deadline = Instant::now() + Duration::from_secs(15);
            let mut position = 0u64;
            let mut peak = 0.0f64;
            let mut digest = live_ring::DIGEST_START;
            let mut energies = [0.0f64; 4];
            let mut counts = [0u64; 4];
            let mut pending_acks: std::collections::VecDeque<(u64, u64)> =
                std::collections::VecDeque::new();
            let mut in_flight: Option<(ParameterChange, Instant)> = None;
            'play: while position < frames {
                if producer.stop_requested() || interrupted() {
                    break;
                }
                if let Some((change, sent)) = &in_flight {
                    let runtime = runtimes
                        .iter_mut()
                        .find(|runtime| runtime.track.id == change.track_id)
                        .ok_or("live source track not found")?;
                    if let Some(target) = runtime.server.poll_control(change, &runtime.queue)? {
                        let target = target.max(pending_acks.back().map_or(0, |(_, frame)| *frame));
                        pending_acks.push_back((change.revision, target));
                        in_flight = None;
                    } else if sent.elapsed() > Duration::from_secs(2) {
                        return Err("source control readback timed out".into());
                    }
                }
                if in_flight.is_none() {
                    if let Ok(change) = receiving.try_recv() {
                        let runtime = runtimes
                            .iter_mut()
                            .find(|runtime| runtime.track.id == change.track_id)
                            .ok_or("live source track not found")?;
                        if u64::from(runtime.queue.written()) * 64
                            >= runtime.duration_gain().0.min(frames)
                        {
                            return Err("live source ended before control delivery".into());
                        }
                        runtime.server.begin_control(&change)?;
                        in_flight = Some((change, Instant::now()));
                    }
                }
                let count = (frames - position).min(64) as usize;
                let mut mixed = [[0.0; 2]; 64];
                engine.render_block_unclipped(&mut mixed[..count]);
                for runtime in &mut runtimes {
                    let mut buffer = [0.0f32; 128];
                    let (duration_frames, gain) = runtime.duration_gain();
                    let needs_block = !runtime.server.finite() || position < duration_frames;
                    if needs_block {
                        loop {
                            if producer.stop_requested() || interrupted() {
                                break 'play;
                            }
                            if Instant::now() >= deadline {
                                return Err("SC session production deadline exceeded".into());
                            }
                            runtime.server.check()?;
                            if runtime.queue.pop(&mut buffer)? {
                                break;
                            }
                            if runtime.server.finite()
                                && u64::from(runtime.queue.written()) * 64
                                    >= duration_frames.min(frames)
                            {
                                // The final publication can race the empty pop above.
                                // Recheck after observing its release watermark.
                                if runtime.queue.pop(&mut buffer)? {
                                    break;
                                }
                                return Err(
                                    "finite runtime source ended with missing queue blocks".into(),
                                );
                            }
                            thread::sleep(Duration::from_millis(1));
                        }
                    }
                    for (index, sample) in mixed[..count].iter_mut().enumerate() {
                        let mut stem = if position + index as u64 >= duration_frames {
                            [0.0; 2]
                        } else {
                            [
                                f64::from(buffer[index * 2]) * gain,
                                f64::from(buffer[index * 2 + 1]) * gain,
                            ]
                        };
                        runtime.chain.process(&mut stem, position + index as u64);
                        sample[0] += stem[0];
                        sample[1] += stem[1];
                    }
                }
                for audio in &mixed[..count] {
                    if audio.iter().any(|value| !value.is_finite()) {
                        return Err("SC session mix is nonfinite".into());
                    }
                    peak = peak.max(audio[0].abs()).max(audio[1].abs());
                    let mut frame = Frame {
                        audio: [audio[0].clamp(-1.0, 1.0), audio[1].clamp(-1.0, 1.0)],
                        timeline: position + 1,
                    };
                    loop {
                        if producer.stop_requested() || interrupted() {
                            break 'play;
                        }
                        if Instant::now() >= deadline {
                            return Err("SC session callback queue deadline exceeded".into());
                        }
                        match producer.push(frame) {
                            Ok(()) => break,
                            Err(pending) => {
                                frame = pending;
                                for runtime in &mut runtimes {
                                    runtime.server.check()?;
                                }
                                thread::sleep(Duration::from_millis(1));
                            }
                        }
                    }
                    digest = live_ring::digest_frame(digest, frame);
                    let quarter = ((position * 4) / frames).min(3) as usize;
                    energies[quarter] += 0.5 * (audio[0] * audio[0] + audio[1] * audio[1]);
                    counts[quarter] += 1;
                    position += 1;
                    if position == frames.min(live_ring::CAPACITY as u64) {
                        let _ = ready.send(Ok(()));
                    }
                }
                while pending_acks
                    .front()
                    .is_some_and(|(_, target)| position >= *target)
                {
                    let ack = pending_acks.pop_front().unwrap();
                    acknowledged
                        .try_send(ack)
                        .map_err(|_| "SC acknowledgment queue unavailable".to_string())?;
                }
            }
            let count = runtimes.len();
            let mut hardware_bus_peaks = Vec::new();
            let owned_pids: Vec<_> = runtimes
                .iter()
                .map(|runtime| runtime.server.pid())
                .collect();
            for runtime in &mut runtimes {
                if let SourceProcess::Puredata(server) = &mut runtime.server {
                    if position == frames && !producer.stop_requested() && !interrupted() {
                        server.finish()?;
                        let Device::Puredata(source) = &runtime.track.device else {
                            unreachable!()
                        };
                        if u64::from(runtime.queue.written())
                            != source.duration_frames.min(frames).div_ceil(64)
                        {
                            return Err(
                                "Pure Data queue publication count mismatches completion".into()
                            );
                        }
                    } else {
                        server.stop()?;
                    }
                    continue;
                }
                if let SourceProcess::Csound(server) = &mut runtime.server {
                    server.stop()?;
                    continue;
                }
                let SourceProcess::Supercollider(server) = &mut runtime.server else {
                    unreachable!()
                };
                let samples = server.read_buffer(0, 0, 128)?;
                let bus_peak = samples
                    .iter()
                    .fold(0.0f32, |peak, sample| peak.max(sample.abs()));
                if bus_peak != 0.0 {
                    return Err("SC source hardware buses were not muted".into());
                }
                hardware_bus_peaks.push(bus_peak);
                server.node("/n_free", 1, &[])?;
                server.done("/b_free", &[Arg::Int(0)])?;
            }
            drop(runtimes);
            let rms_quarters: Vec<_> = energies
                .iter()
                .zip(counts)
                .map(|(sum, count)| {
                    if count == 0 {
                        0.0
                    } else {
                        (sum / count as f64).sqrt()
                    }
                })
                .collect();
            Ok(
                json!({"source_frames":position,"source_digest":format!("{digest:016x}"),"pre_master_peak":peak,"runtime_sources":count,"owned_pids":owned_pids,"source_processes":source_processes,"hardware_bus_peaks":hardware_bus_peaks,"rms_quarters":rms_quarters,"owned_servers_released":true,"owned_processes_released":true,"queue_released":true}),
            )
        })();
        if let Err(error) = &result {
            producer.set_failed();
            let _ = opened.try_send(Err(error.clone()));
            let _ = ready.try_send(Err(error.clone()));
        }
        producer.set_done();
        result
    };
    let worker = thread::Builder::new()
        .name("daw-sc-session".into())
        .spawn(task)
        .map_err(|e| e.to_string())?;
    let mut guard = Worker::new(control, prepared, worker, Some(begin))
        .with_stop_timeout(Duration::from_secs(20));
    guard.updates = Some(Updates {
        sender: changes,
        acks: acknowledgements,
        pending: 0,
        applied: None,
    });
    match opening.recv_timeout(Duration::from_secs(20)) {
        Ok(Ok(metadata)) => {
            guard.metadata = metadata;
            Ok((consumer, guard))
        }
        outcome => {
            let error = match outcome {
                Ok(Err(error)) => error,
                _ => "SC session preparation timed out".into(),
            };
            let _ = guard.finish();
            Err(error)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn live_v8_pd_validation_rejects_foreign_effects_without_loading_them() {
        let mut session: Session = serde_json::from_value(json!({
            "schema_version":8,"sample_rate":48000,"tempo_milli_bpm":120000,
            "tracks":[{"id":"pd","mode":"continuous","clips":[],"effects":[],
                "device":{"kind":"puredata","program":"x","abstractions":[],
                    "duration_frames":48000,"gain":0.5,"controls":[]}}]
        }))
        .unwrap();
        assert_eq!(validate(&session, 1.0).unwrap(), 48000);
        session.tracks[0].effects = Some(vec![Effect::Vst3 {
            id: "unsupported".into(),
            bypass: false,
            bundle_path: "/private/tmp/nonexistent-test-effect.vst3".into(),
            class_id: "0".repeat(32),
            state_hex: String::new(),
            controller_state_hex: String::new(),
            parameters: vec![],
        }]);
        assert!(validate(&session, 1.0).unwrap_err().contains("gain only"));
        session.tracks[0].effects = Some(vec![]);
        session.sample_rate = 44100;
        assert!(validate(&session, 1.0).unwrap_err().contains("48000 Hz"));
    }
    #[test]
    fn control_queue_bounds_unacknowledged_edits_and_callback_watermark() {
        let (sender, receiver) = mpsc::sync_channel(MAX_PENDING_CONTROLS);
        let (acks, acknowledgements) = mpsc::sync_channel(MAX_PENDING_CONTROLS);
        let mut updates = Updates {
            sender,
            acks: acknowledgements,
            pending: 0,
            applied: None,
        };
        let change = |revision| ParameterChange {
            track_id: "source".into(),
            target: ControlTarget::Supercollider {
                index: 0,
                values: vec![0.1],
            },
            revision,
        };
        for revision in 1..=8 {
            updates.queue(change(revision)).unwrap();
        }
        assert!(updates.queue(change(9)).is_err());
        // Receiving a command alone must not free capacity or claim DSP delivery.
        assert_eq!(receiver.recv().unwrap().revision, 1);
        assert!(updates.queue(change(9)).is_err());
        acks.send((1, 1024)).unwrap();
        let status = updates.status(1023);
        assert_eq!(status["pending"], 7);
        assert_eq!(status["applied_revision"], "1");
        assert_eq!(status["callback_observed"], false);
        updates.queue(change(9)).unwrap();
        assert_eq!(updates.status(1024)["callback_observed"], true);
    }
    #[test]
    fn capture_definition_is_bounded_and_stereo() {
        let bytes =
            synthdef::decode_hex(include_str!("../native/supercollider/stream_capture.hex").trim())
                .unwrap();
        let program = synthdef::inspect(&bytes).unwrap();
        assert_eq!(program.name, CAPTURE_NAME);
        assert_eq!(program.ugen_count, 6);
        assert!(program.controls.is_empty());
        assert!(bytes.len() < 1024);
    }
}
