//! Experimental in-process VST3 DSP. Foreign code runs only on this worker.
use crate::{
    effects::PreparedChain,
    engine::Engine,
    live_ring::{self, Consumer, Control, Frame},
    session::{Effect, Session},
};
use std::{
    cell::Cell,
    ffi::{CString, c_char, c_int, c_void},
    path::PathBuf,
    ptr::NonNull,
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

type Create = unsafe extern "C" fn(
    *const c_char,
    *const c_char,
    *const u8,
    usize,
    f64,
    *mut c_char,
    usize,
) -> *mut c_void;
type Process = unsafe extern "C" fn(*mut c_void, *mut f64, u32, u64) -> c_int;
type Destroy = unsafe extern "C" fn(*mut c_void) -> c_int;
unsafe extern "C" {
    fn dlopen(path: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}
struct Library {
    handle: NonNull<c_void>,
    create: Create,
    process: Process,
    destroy: Destroy,
    teardown_failed: Cell<bool>,
}
impl Library {
    fn open() -> Result<Self, String> {
        let path = std::env::var_os("DAW_VST3_LIBRARY")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("output/vst3-spike/libdaw-vst3.dylib")
            });
        if !path.is_absolute() || !path.is_file() {
            return Err("live VST3 library unavailable; run native/vst3/build.py or set DAW_VST3_LIBRARY to an absolute path".into());
        }
        let path = CString::new(path.as_os_str().as_encoded_bytes()).map_err(|e| e.to_string())?;
        // SAFETY: owned NUL-terminated path, macOS RTLD_NOW | RTLD_LOCAL.
        let handle = NonNull::new(unsafe { dlopen(path.as_ptr(), 6) })
            .ok_or("cannot load live VST3 library")?;
        // SAFETY: symbols are the versioned C exports in native/vst3/live.cpp.
        unsafe {
            let create = dlsym(handle.as_ptr(), c"daw_vst3_create".as_ptr());
            let process = dlsym(handle.as_ptr(), c"daw_vst3_process".as_ptr());
            let destroy = dlsym(handle.as_ptr(), c"daw_vst3_destroy".as_ptr());
            if create.is_null() || process.is_null() || destroy.is_null() {
                dlclose(handle.as_ptr());
                return Err("invalid live VST3 library exports".into());
            }
            Ok(Self {
                handle,
                teardown_failed: Cell::new(false),
                create: std::mem::transmute::<*mut c_void, Create>(create),
                process: std::mem::transmute::<*mut c_void, Process>(process),
                destroy: std::mem::transmute::<*mut c_void, Destroy>(destroy),
            })
        }
    }
}
impl Drop for Library {
    fn drop(&mut self) {
        // SAFETY: all plugin handles have been destroyed on this worker before the library.
        unsafe {
            dlclose(self.handle.as_ptr());
        }
    }
}
struct Plugin<'a> {
    handle: NonNull<c_void>,
    library: &'a Library,
}
impl<'a> Plugin<'a> {
    fn new(library: &'a Library, effect: &Effect, tempo: f64, frames: u64) -> Result<Self, String> {
        let Effect::Vst3 {
            bundle_path,
            class_id,
            state_hex,
            controller_state_hex,
            parameters,
            ..
        } = effect
        else {
            unreachable!()
        };
        let mut config = b"DWV4".to_vec();
        for state in [state_hex, controller_state_hex] {
            let bytes: Vec<u8> = state
                .as_bytes()
                .chunks_exact(2)
                .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
                .collect();
            config.extend((bytes.len() as u32).to_le_bytes());
            config.extend(bytes);
        }
        config.extend((parameters.len() as u32).to_le_bytes());
        for parameter in parameters {
            config.extend(parameter.id.to_le_bytes());
            config.extend(parameter.value.to_le_bytes());
            let points: Vec<_> = parameter
                .points
                .iter()
                .filter(|p| p.frame < frames)
                .collect();
            config.extend((points.len() as u32).to_le_bytes());
            for point in points {
                config.extend((point.frame as u32).to_le_bytes());
                config.extend(point.value.to_le_bytes());
            }
        }
        config.extend(0u32.to_le_bytes());
        let path = CString::new(bundle_path.as_str()).map_err(|e| e.to_string())?;
        let cid = CString::new(class_id.as_str()).map_err(|e| e.to_string())?;
        let mut error = [0i8; 1024];
        // SAFETY: config is validated and owned for the call, error buffer is writable.
        let handle = unsafe {
            (library.create)(
                path.as_ptr(),
                cid.as_ptr(),
                config.as_ptr(),
                config.len(),
                tempo,
                error.as_mut_ptr(),
                error.len(),
            )
        };
        NonNull::new(handle)
            .map(|handle| Self { handle, library })
            .ok_or_else(|| {
                let bytes: Vec<u8> = error
                    .iter()
                    .take_while(|b| **b != 0)
                    .map(|b| *b as u8)
                    .collect();
                format!(
                    "live VST3 preparation failed: {}",
                    String::from_utf8_lossy(&bytes)
                )
            })
    }
    fn process(&mut self, samples: &mut [[f64; 2]], frame: u64) -> Result<(), String> {
        // SAFETY: handle belongs to this thread; arrays provide contiguous stereo f64s.
        if unsafe {
            (self.library.process)(
                self.handle.as_ptr(),
                samples.as_mut_ptr().cast(),
                samples.len() as u32,
                frame,
            )
        } != 0
        {
            return Err("live VST3 processing failed".into());
        }
        Ok(())
    }
}
impl Drop for Plugin<'_> {
    fn drop(&mut self) {
        // SAFETY: unique handle destroyed on the same worker that created it.
        unsafe {
            if (self.library.destroy)(self.handle.as_ptr()) != 0 {
                self.library.teardown_failed.set(true);
            }
        }
    }
}
enum Processor<'a> {
    Gain(PreparedChain),
    Plugin(Plugin<'a>),
}
struct Track<'a> {
    engine: Engine,
    effects: Vec<Processor<'a>>,
}
fn prepare<'a>(
    session: &Session,
    library: &'a Library,
    frames: u64,
) -> Result<Vec<Track<'a>>, String> {
    let mut tracks = Vec::new();
    for track in &session.tracks {
        let mut source = session.clone();
        source.schema_version = 3;
        source.tracks = vec![track.clone()];
        source.tracks[0].effects = Some(Vec::new());
        source.tracks[0].automation = None;
        let engine = Engine::prepare(&source)?;
        let mut effects = Vec::new();
        for effect in track.effects.as_deref().unwrap_or_default() {
            match effect {
                Effect::Gain { .. } => {
                    let mut gain = track.clone();
                    gain.effects = Some(vec![effect.clone()]);
                    effects.push(Processor::Gain(PreparedChain::prepare(&gain)));
                }
                Effect::Vst3 { bypass: true, .. } => {}
                Effect::Vst3 { .. } => effects.push(Processor::Plugin(Plugin::new(
                    library,
                    effect,
                    f64::from(session.tempo_milli_bpm.unwrap_or(120_000)) / 1000.0,
                    frames,
                )?)),
            }
        }
        tracks.push(Track { engine, effects });
    }
    Ok(tracks)
}

pub(crate) struct WorkerGuard {
    control: Control,
    thread: Option<JoinHandle<Result<(), String>>>,
}
impl WorkerGuard {
    pub(crate) fn underruns(&self) -> u64 {
        self.control.underruns()
    }
    pub(crate) fn finish(&mut self) -> Result<(), String> {
        self.control.request_stop();
        let deadline = Instant::now() + Duration::from_secs(2);
        if let Some(thread) = self.thread.take() {
            while !thread.is_finished() && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(1));
            }
            if !thread.is_finished() {
                return Err("live plugin worker did not release within two seconds; restart engine (worker detached)".into());
            }
            thread
                .join()
                .map_err(|_| "live plugin worker panicked".to_string())??;
        }
        Ok(())
    }
}
impl Drop for WorkerGuard {
    fn drop(&mut self) {
        let _ = self.finish();
    }
}
pub(crate) fn start(session: &Session, frames: u64) -> Result<(Consumer, WorkerGuard), String> {
    let (mut producer, consumer, control) = live_ring::pair();
    let session = session.clone();
    let (ready, prepared) = std::sync::mpsc::sync_channel(1);
    let worker = thread::Builder::new()
        .name("daw-vst3-dsp".into())
        .spawn(move || {
            let result: Result<(), String> =
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                    let library = Library::open()?;
                    let mut tracks = prepare(&session, &library, frames)?;
                    let mut notified = false;
                    'play: for position in (0..frames).step_by(256) {
                        if producer.stop_requested() {
                            break 'play;
                        }
                        let count = (frames - position).min(256) as usize;
                        let mut mixed = [[0.0f64; 2]; 256];
                        for track in &mut tracks {
                            let mut stem = [[0.0; 2]; 256];
                            track.engine.render_block_unclipped(&mut stem[..count]);
                            for effect in &mut track.effects {
                                match effect {
                                    Processor::Gain(chain) => {
                                        for (i, sample) in stem[..count].iter_mut().enumerate() {
                                            chain.process(sample, position + i as u64);
                                        }
                                    }
                                    Processor::Plugin(plugin) => {
                                        plugin.process(&mut stem[..count], position)?
                                    }
                                }
                            }
                            for i in 0..count {
                                mixed[i][0] += stem[i][0];
                                mixed[i][1] += stem[i][1];
                            }
                        }
                        for (i, audio) in mixed[..count].iter().enumerate() {
                            if audio.iter().any(|s| !s.is_finite()) {
                                return Err("live plugin mix is non-finite".into());
                            }
                            let mut pending = Frame {
                                audio: [audio[0].clamp(-1.0, 1.0), audio[1].clamp(-1.0, 1.0)],
                                timeline: position + i as u64 + 1,
                            };
                            loop {
                                if producer.stop_requested() {
                                    break 'play;
                                }
                                match producer.push(pending) {
                                    Ok(()) => break,
                                    Err(frame) => {
                                        pending = frame;
                                        thread::sleep(Duration::from_millis(1));
                                    }
                                }
                            }
                            if !notified
                                && position + i as u64 + 1 == frames.min(live_ring::CAPACITY as u64)
                            {
                                let _ = ready.send(Ok(()));
                                notified = true;
                            }
                        }
                    }
                    drop(tracks);
                    if library.teardown_failed.get() {
                        return Err("live VST3 teardown failed".into());
                    }
                    Ok(())
                }))
                .unwrap_or_else(|_| Err("live plugin worker panicked".into()));
            if let Err(error) = &result {
                producer.set_failed();
                let _ = ready.send(Err(error.clone()));
            }
            // Scope above drops all foreign instances and module handles on this thread.
            producer.set_done();
            result
        })
        .map_err(|e| e.to_string())?;
    let mut guard = WorkerGuard {
        control,
        thread: Some(worker),
    };
    match prepared.recv_timeout(Duration::from_secs(5)) {
        Ok(Ok(())) => Ok((consumer, guard)),
        result => {
            let error = match result {
                Ok(Err(error)) => error,
                _ => "live plugin preparation timed out or worker exited".into(),
            };
            match guard.finish() {
                Ok(()) => Err(error),
                Err(release) => Err(format!("{error}; {release}")),
            }
        }
    }
}
