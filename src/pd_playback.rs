//! Pd DSP belongs to an owned worker. The hardware callback only consumes a
//! fixed SPSC queue and publishes atomic transport targets.
use crate::{
    engine::{Engine, MixerPeaks},
    session::{MAX_FRAME, Session},
};
use std::{
    cell::UnsafeCell,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering::*},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

const FRAMES: usize = 256;
const CAPACITY: usize = 4;
#[derive(Clone, Copy)]
struct Block {
    audio: [[f64; 2]; FRAMES],
    timeline: [u64; FRAMES],
    peaks: MixerPeaks,
    generation: u64,
    session_revision: u64,
    tempo: u32,
}
impl Default for Block {
    fn default() -> Self {
        Self {
            audio: [[0.0; 2]; FRAMES],
            timeline: [0; FRAMES],
            peaks: MixerPeaks::default(),
            generation: 0,
            session_revision: 0,
            tempo: 120000,
        }
    }
}
struct Shared {
    blocks: Box<[UnsafeCell<Block>]>,
    write: AtomicUsize,
    read: AtomicUsize,
    stop: AtomicBool,
    failed: AtomicBool,
    underruns: AtomicU64,
    transport_waits: AtomicU64,
    revision: AtomicU64,
    target: AtomicU64,
    loop_start: AtomicU64,
    loop_end: AtomicU64,
    audible_revision: AtomicU64,
}
// SPSC slot ownership: producer publishes with Release; consumer returns the
// slot with Release. Neither accesses a slot while the other owns it.
unsafe impl Sync for Shared {}
pub(crate) struct Reader {
    shared: Arc<Shared>,
    block: Block,
    offset: usize,
    generation: u64,
    timeline: u64,
    output: u64,
    region: Option<(u64, u64)>,
    waiting_generation: bool,
}
pub(crate) struct Worker {
    shared: Arc<Shared>,
    handle: Option<JoinHandle<Result<(), String>>>,
    updates: std::sync::mpsc::SyncSender<(Engine, Session, u64)>,
    session: Session,
}
impl Worker {
    #[cfg(test)]
    pub(crate) fn update(&mut self, session: Session, revision: u64) -> Result<(), String> {
        validate_live_update(&self.session, &session)?;
        let engine = Engine::prepare(&session)?;
        self.update_prepared(engine, session, revision)
    }
    pub(crate) fn update_prepared(
        &mut self,
        engine: Engine,
        session: Session,
        revision: u64,
    ) -> Result<(), String> {
        validate_live_update(&self.session, &session)?;
        if self.shared.failed.load(Acquire) || self.shared.stop.load(Acquire) {
            return Err("arrangement worker is unavailable".into());
        }
        self.updates
            .try_send((engine, session.clone(), revision))
            .map_err(|_| "arrangement update queue is full or closed".to_string())?;
        self.session = session;
        Ok(())
    }

    pub(crate) fn audible_revision(&self) -> u64 {
        self.shared.audible_revision.load(Acquire)
    }
    pub(crate) fn live_edits_available(&self) -> bool {
        validate_live_update(&self.session, &self.session).is_ok()
    }
    pub(crate) fn underruns(&self) -> u64 {
        self.shared.underruns.load(Relaxed)
    }
    pub(crate) fn transport_waits(&self) -> u64 {
        self.shared.transport_waits.load(Relaxed)
    }
    pub(crate) fn finish(&mut self) -> Result<(), String> {
        self.shared.stop.store(true, Release);
        let deadline = Instant::now() + Duration::from_secs(2);
        if let Some(handle) = self.handle.take() {
            while !handle.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(2));
            }
            if !handle.is_finished() {
                return Err("Pd instrument worker stop timed out".into());
            }
            handle
                .join()
                .map_err(|_| "Pd instrument worker panicked".to_string())??;
        }
        Ok(())
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}
impl Reader {
    pub(crate) fn begin_callback(&self, peaks: &mut MixerPeaks) {
        if self.offset < FRAMES {
            peaks.merge(&self.block.peaks);
        }
    }
    pub(crate) fn tempo(&self) -> u32 {
        self.block.tempo
    }
    pub(crate) fn frame_position(&self) -> u64 {
        self.timeline
    }
    pub(crate) fn output_position(&self) -> u64 {
        self.output
    }
    fn publish(&mut self, target: u64) {
        self.shared.revision.fetch_add(1, AcqRel);
        self.shared.target.store(target, Relaxed);
        let (start, end) = self.region.unwrap_or((0, 0));
        self.shared.loop_start.store(start, Relaxed);
        self.shared.loop_end.store(end, Relaxed);
        self.generation = self.shared.revision.fetch_add(1, Release) + 1;
        self.offset = FRAMES;
        self.waiting_generation = true;
        self.timeline = target;
    }
    pub(crate) fn seek(&mut self, frame: u64) -> Result<(), String> {
        if frame > MAX_FRAME {
            return Err("frame exceeds timeline limit".into());
        }
        let frame = match self.region {
            Some((start, end)) if frame >= end => start + (frame - start) % (end - start),
            _ => frame,
        };
        self.publish(frame);
        Ok(())
    }
    pub(crate) fn set_loop(&mut self, region: Option<(u64, u64)>) -> Result<(), String> {
        if region.is_some_and(|(start, end)| start >= end || end > MAX_FRAME) {
            return Err("invalid loop region".into());
        }
        self.region = region;
        self.seek(self.timeline)
    }
    pub(crate) fn next(
        &mut self,
        peaks: &mut MixerPeaks,
    ) -> Result<Option<[f64; 2]>, &'static str> {
        if self.shared.failed.load(Acquire) {
            return Err("Pd instrument worker failed");
        }
        if self.offset == FRAMES {
            // At most CAPACITY stale blocks can have been queued before a seek.
            for _ in 0..CAPACITY {
                let read = self.shared.read.load(Relaxed);
                let write = self.shared.write.load(Acquire);
                if read == write {
                    break;
                }
                // SAFETY: Release/Acquire transfers the published slot to us.
                let block = unsafe { *self.shared.blocks[read % CAPACITY].get() };
                self.shared.read.store(read.wrapping_add(1), Release);
                if block.generation == self.generation {
                    self.block = block;
                    self.offset = 0;
                    self.waiting_generation = false;
                    break;
                }
            }
            if self.offset == FRAMES {
                if self.waiting_generation {
                    self.shared.transport_waits.fetch_add(1, Relaxed);
                } else {
                    self.shared.underruns.fetch_add(1, Relaxed);
                }
                return Ok(None);
            }
        }
        if self.offset == 0 {
            self.shared
                .audible_revision
                .store(self.block.session_revision, Release);
            peaks.merge(&self.block.peaks);
        }
        let sample = self.block.audio[self.offset];
        self.timeline = self.block.timeline[self.offset];
        self.output += 1;
        self.offset += 1;
        Ok(Some(sample))
    }
}
pub(crate) fn validate_live_update(old: &Session, new: &Session) -> Result<(), String> {
    crate::browser_stream::validate_live_update(old, new)
}

pub(crate) fn start(session: &Session) -> Result<(Reader, Worker), String> {
    session.validate()?;
    let shared = Arc::new(Shared {
        blocks: (0..CAPACITY)
            .map(|_| UnsafeCell::new(Block::default()))
            .collect::<Vec<_>>()
            .into_boxed_slice(),
        write: AtomicUsize::new(0),
        read: AtomicUsize::new(0),
        stop: AtomicBool::new(false),
        failed: AtomicBool::new(false),
        underruns: AtomicU64::new(0),
        transport_waits: AtomicU64::new(0),
        revision: AtomicU64::new(0),
        target: AtomicU64::new(0),
        loop_start: AtomicU64::new(0),
        loop_end: AtomicU64::new(0),
        audible_revision: AtomicU64::new(0),
    });
    let mut session = session.clone();
    let saved_session = session.clone();
    let (updates, pending_updates) = std::sync::mpsc::sync_channel::<(Engine, Session, u64)>(1);
    let producer = Arc::clone(&shared);
    let (ready, startup) = std::sync::mpsc::sync_channel(1);
    let handle = thread::Builder::new()
        .name("daw-pd-instrument".into())
        .spawn(move || {
            // Covers a panic as well as normal failure: the callback must not
            // consume a dead worker's queue indefinitely.
            struct FailureNotice(Arc<Shared>);
            impl Drop for FailureNotice {
                fn drop(&mut self) {
                    if !self.0.stop.load(Acquire) {
                        self.0.failed.store(true, Release);
                    }
                }
            }
            let _failure_notice = FailureNotice(Arc::clone(&producer));
            let result = (|| {
                let mut engine = match Engine::prepare(&session) {
                    Ok(engine) => engine,
                    Err(error) => {
                        let _ = ready.send(Err(error.clone()));
                        return Err(error);
                    }
                };
                let mut generation = 0;
                let mut session_revision = 0;
                let mut announced = false;
                let mut block = Block::default();
                let mut transition = [[0.0; 2]; 64];
                let mut transition_pending = false;
                while !producer.stop.load(Acquire) {
                    let revision = producer.revision.load(Acquire);
                    if revision % 2 != 0 {
                        thread::yield_now();
                        continue;
                    }
                    if revision != generation {
                        let target = producer.target.load(Relaxed);
                        let start = producer.loop_start.load(Relaxed);
                        let end = producer.loop_end.load(Relaxed);
                        std::sync::atomic::fence(Acquire);
                        if producer.revision.load(Acquire) != revision {
                            continue;
                        }
                        engine.set_loop((end > start).then_some((start, end)))?;
                        engine.seek(target)?;
                        generation = revision;
                    }
                    if let Ok((mut prepared, next_session, revision)) = pending_updates.try_recv() {
                        prepared.adopt_live(&mut engine, &session, &next_session);
                        if session != next_session {
                            engine.render_block(&mut transition);
                            transition_pending = true;
                        }
                        engine = prepared;
                        session = next_session;
                        session_revision = revision;
                    }
                    let write = producer.write.load(Relaxed);
                    let read = producer.read.load(Acquire);
                    if write.wrapping_sub(read) == CAPACITY {
                        if !announced {
                            let _ = ready.send(Ok(()));
                            announced = true;
                        }
                        thread::sleep(Duration::from_micros(100));
                        continue;
                    }
                    block.generation = generation;
                    block.session_revision = session_revision;
                    block.tempo = session.tempo_milli_bpm.unwrap_or(120000);
                    block.peaks = MixerPeaks::default();
                    for index in 0..FRAMES {
                        engine.render_block(&mut block.audio[index..index + 1]);
                        block.timeline[index] = engine.frame_position();
                        block.peaks.merge(engine.mixer_peaks());
                    }
                    if transition_pending {
                        for (index, prior) in transition.iter().enumerate() {
                            let blend = (index + 1) as f64 / transition.len() as f64;
                            for (channel, sample) in prior.iter().enumerate() {
                                block.audio[index][channel] =
                                    sample * (1.0 - blend) + block.audio[index][channel] * blend;
                            }
                        }
                        transition_pending = false;
                    }
                    // SAFETY: capacity check reserves this producer-owned slot.
                    unsafe {
                        *producer.blocks[write % CAPACITY].get() = block;
                    }
                    producer.write.store(write.wrapping_add(1), Release);
                }
                Ok(())
            })();
            if result.is_err() {
                producer.failed.store(true, Release);
            }
            result
        })
        .map_err(|e| e.to_string())?;
    let mut worker = Worker {
        shared: Arc::clone(&shared),
        handle: Some(handle),
        updates,
        session: saved_session,
    };
    match startup.recv_timeout(Duration::from_secs(15)) {
        Ok(Ok(())) => Ok((
            Reader {
                shared,
                block: Block::default(),
                offset: FRAMES,
                generation: 0,
                timeline: 0,
                output: 0,
                region: None,
                waiting_generation: false,
            },
            worker,
        )),
        Ok(Err(error)) => {
            let _ = worker.finish();
            Err(error)
        }
        Err(_) => {
            let _ = worker.finish();
            Err("Pd instrument worker startup timed out".into())
        }
    }
}
