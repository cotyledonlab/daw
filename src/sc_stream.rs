//! SC queue ownership and finite source drainer. All mapping and foreign calls stay on
//! this non-realtime worker; the hardware callback consumes live_ring only.
use crate::{
    effects::PreparedChain,
    live_ring::{self, Consumer, Control, Frame},
    session::Session,
};
use serde_json::{Value, json};
use std::{
    ffi::{CString, c_char, c_int, c_void},
    path::Path,
    ptr::NonNull,
    sync::mpsc::{self, Receiver},
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

type Open = unsafe extern "C" fn(*const c_char, u64, *mut c_char, usize) -> *mut c_void;
type Pop = unsafe extern "C" fn(*mut c_void, *mut f32) -> c_int;
type Fault = unsafe extern "C" fn(*mut c_void) -> u32;
type Close = unsafe extern "C" fn(*mut c_void);
unsafe extern "C" {
    fn dlopen(path: *const c_char, flags: c_int) -> *mut c_void;
    fn dlsym(handle: *mut c_void, symbol: *const c_char) -> *mut c_void;
    fn dlclose(handle: *mut c_void) -> c_int;
}
pub(crate) struct Queue {
    library: NonNull<c_void>,
    handle: NonNull<c_void>,
    pop: Pop,
    fault: Fault,
    close: Close,
}
impl Queue {
    pub(crate) fn open(path: &Path, nonce: u64) -> Result<Self, String> {
        let library_path = std::env::var_os("DAW_SC_QUEUE_LIBRARY")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| {
                Path::new(env!("CARGO_MANIFEST_DIR")).join("output/sc-stream/libdaw-sc-queue.dylib")
            });
        if !library_path.is_absolute() || !library_path.is_file() {
            return Err("SC queue library unavailable; build_stream.py required".into());
        }
        let library_path =
            CString::new(library_path.as_os_str().as_encoded_bytes()).map_err(|e| e.to_string())?;
        let path = CString::new(path.as_os_str().as_encoded_bytes()).map_err(|e| e.to_string())?;
        // SAFETY: owned NUL-terminated path and macOS RTLD_NOW|RTLD_LOCAL.
        let library = NonNull::new(unsafe { dlopen(library_path.as_ptr(), 6) })
            .ok_or("cannot load SC queue library")?;
        // SAFETY: exact owned C exports in stream_bridge.cpp, retained until close.
        unsafe {
            let open = dlsym(library.as_ptr(), c"daw_sc_queue_open".as_ptr());
            let pop = dlsym(library.as_ptr(), c"daw_sc_queue_pop".as_ptr());
            let fault = dlsym(library.as_ptr(), c"daw_sc_queue_fault".as_ptr());
            let close = dlsym(library.as_ptr(), c"daw_sc_queue_close".as_ptr());
            if open.is_null() || pop.is_null() || fault.is_null() || close.is_null() {
                dlclose(library.as_ptr());
                return Err("invalid SC queue library exports".into());
            }
            let open = std::mem::transmute::<*mut c_void, Open>(open);
            let mut error = [0 as c_char; 256];
            let handle = open(path.as_ptr(), nonce, error.as_mut_ptr(), error.len());
            let Some(handle) = NonNull::new(handle) else {
                dlclose(library.as_ptr());
                let error = error
                    .iter()
                    .take_while(|&&value| value != 0)
                    .map(|&value| value as u8)
                    .collect::<Vec<_>>();
                return Err(format!(
                    "SC queue open failed: {}",
                    String::from_utf8_lossy(&error)
                ));
            };
            Ok(Self {
                library,
                handle,
                pop: std::mem::transmute::<*mut c_void, Pop>(pop),
                fault: std::mem::transmute::<*mut c_void, Fault>(fault),
                close: std::mem::transmute::<*mut c_void, Close>(close),
            })
        }
    }
    pub(crate) fn pop(&mut self, buffer: &mut [f32; 128]) -> Result<bool, String> {
        // SAFETY: this worker owns the queue and writable 128-sample block.
        match unsafe { (self.pop)(self.handle.as_ptr(), buffer.as_mut_ptr()) } {
            0 => Ok(false),
            1 => Ok(true),
            _ => {
                // SAFETY: same retained queue handle.
                let fault = unsafe { (self.fault)(self.handle.as_ptr()) };
                Err(format!("SC shared queue fault {fault}"))
            }
        }
    }
}

pub(crate) fn create_queue(path: &Path, nonce: u64) -> Result<(), String> {
    type Create = unsafe extern "C" fn(*const c_char, u64, *mut c_char, usize) -> c_int;
    let library_path = std::env::var_os("DAW_SC_QUEUE_LIBRARY")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR")).join("output/sc-stream/libdaw-sc-queue.dylib")
        });
    if !library_path.is_absolute() || !library_path.is_file() {
        return Err("SC queue library unavailable; build_stream.py required".into());
    }
    let library_path =
        CString::new(library_path.as_os_str().as_encoded_bytes()).map_err(|e| e.to_string())?;
    let path = CString::new(path.as_os_str().as_encoded_bytes()).map_err(|e| e.to_string())?;
    // SAFETY: owned path; creation export is the exact non-realtime C ABI.
    unsafe {
        let library =
            NonNull::new(dlopen(library_path.as_ptr(), 6)).ok_or("cannot load SC queue library")?;
        let symbol = dlsym(library.as_ptr(), c"daw_sc_queue_create".as_ptr());
        if symbol.is_null() {
            dlclose(library.as_ptr());
            return Err("SC queue creation export unavailable".into());
        }
        let create = std::mem::transmute::<*mut c_void, Create>(symbol);
        let mut error = [0 as c_char; 256];
        let result = create(path.as_ptr(), nonce, error.as_mut_ptr(), error.len());
        dlclose(library.as_ptr());
        if result != 0 {
            let error = error
                .iter()
                .take_while(|&&value| value != 0)
                .map(|&value| value as u8)
                .collect::<Vec<_>>();
            return Err(format!(
                "SC queue creation failed: {}",
                String::from_utf8_lossy(&error)
            ));
        }
    }
    Ok(())
}
impl Drop for Queue {
    fn drop(&mut self) {
        // SAFETY: teardown runs on the owning worker after its final pop.
        unsafe {
            (self.close)(self.handle.as_ptr());
            dlclose(self.library.as_ptr());
        }
    }
}

pub(crate) struct Worker {
    pub(crate) control: Control,
    ready: Receiver<Result<(), String>>,
    thread: Option<JoinHandle<Result<Value, String>>>,
    begin: Option<mpsc::SyncSender<()>>,
    stop_timeout: Duration,
}
impl Worker {
    pub(crate) fn new(
        control: Control,
        ready: Receiver<Result<(), String>>,
        thread: JoinHandle<Result<Value, String>>,
        begin: Option<mpsc::SyncSender<()>>,
    ) -> Self {
        Self {
            control,
            ready,
            thread: Some(thread),
            begin,
            stop_timeout: Duration::from_secs(2),
        }
    }
    pub(crate) fn with_stop_timeout(mut self, timeout: Duration) -> Self {
        self.stop_timeout = timeout;
        self
    }
    pub(crate) fn begin(&mut self) -> Result<(), String> {
        if let Some(begin) = self.begin.take() {
            begin
                .try_send(())
                .map_err(|_| "SC source start unavailable".to_string())?;
        }
        Ok(())
    }
    pub(crate) fn wait_ready(&self) -> Result<(), String> {
        self.ready
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| "SC stream prefill timed out".to_string())?
    }
    pub(crate) fn finish(&mut self) -> Result<Value, String> {
        self.control.request_stop();
        self.begin.take();
        let Some(worker) = self.thread.take() else {
            return Err("SC stream worker already released".into());
        };
        let deadline = Instant::now() + self.stop_timeout;
        while !worker.is_finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        if !worker.is_finished() {
            return Err("SC stream worker stop timed out; its queue lease remains owned".into());
        }
        worker
            .join()
            .map_err(|_| "SC stream worker panicked".to_string())?
    }
}
impl Drop for Worker {
    fn drop(&mut self) {
        if self.thread.is_some() {
            let _ = self.finish();
        }
    }
}

pub(crate) fn start(
    path: &Path,
    nonce: u64,
    blocks: u32,
    gain: f64,
) -> Result<(Consumer, Worker), String> {
    if !path.is_absolute()
        || nonce == 0
        || !(16..=7500).contains(&blocks)
        || !gain.is_finite()
        || !(0.0..=4.0).contains(&gain)
    {
        return Err(
            "SC diagnostic requires absolute queue, nonzero nonce, 16..7500 blocks and gain 0..4"
                .into(),
        );
    }
    let session: Session = serde_json::from_value(json!({"schema_version":3,"sample_rate":48000,"tempo_milli_bpm":120000,"tracks":[{"id":"stream","mode":"continuous","clips":[],"device":{"kind":"sine","frequency_hz":440,"gain":1},"effects":[{"kind":"gain","id":"gain","gain":gain,"bypass":false}],"automation":[]}]})).map_err(|e| e.to_string())?;
    session.validate()?;
    let mut chain = PreparedChain::prepare(&session.tracks[0]);
    let path = path.to_owned();
    let (mut producer, consumer, control) = live_ring::pair();
    let (opened, opening) = mpsc::sync_channel(1);
    let (ready, prepared) = mpsc::sync_channel(1);
    let worker = thread::Builder::new().name("daw-sc-stream".into()).spawn(move || {
        let result: Result<Value, String> = (|| {
            let mut queue = Queue::open(&path, nonce)?;
            let _ = opened.send(Ok(()));
            let deadline = Instant::now() + Duration::from_secs(15);
            let mut position = 0u64;
            let mut peak = 0.0f64;
            let mut digest = live_ring::DIGEST_START;
            let mut buffer = [0.0f32;128];
            'stream: for _ in 0..blocks {
                loop {
                    if producer.stop_requested() { break 'stream; }
                    if Instant::now() >= deadline { return Err("SC stream producer deadline exceeded".into()); }
                    if queue.pop(&mut buffer)? { break; }
                    thread::sleep(Duration::from_millis(1));
                }
                for pair in buffer.chunks_exact(2) {
                    if pair.iter().any(|value| !value.is_finite()) { return Err("SC stream contains nonfinite samples".into()); }
                    let mut audio = [f64::from(pair[0]), f64::from(pair[1])];
                    chain.process(&mut audio, position);
                    peak = peak.max(audio[0].abs()).max(audio[1].abs());
                    let mut pending = Frame {audio: [audio[0].clamp(-1.0,1.0), audio[1].clamp(-1.0,1.0)], timeline: position + 1};
                    loop {
                        if producer.stop_requested() { break 'stream; }
                        if Instant::now() >= deadline { return Err("SC callback queue deadline exceeded".into()); }
                        match producer.push(pending) {
                            Ok(()) => break,
                            Err(frame) => { pending = frame; thread::sleep(Duration::from_millis(1)); }
                        }
                    }
                    digest = live_ring::digest_frame(digest, pending);
                    position += 1;
                    if position == (live_ring::CAPACITY as u64).min(u64::from(blocks) * 64) { let _ = ready.send(Ok(())); }
                }
            }
            Ok(json!({"source_frames":position,"source_digest":format!("{digest:016x}"),"post_effect_peak":peak,"effect_gain":gain,"queue_released":true}))
        })();
        if let Err(error) = &result {
            producer.set_failed();
            let _ = opened.try_send(Err(error.clone()));
            let _ = ready.try_send(Err(error.clone()));
        }
        producer.set_done();
        result
    }).map_err(|e| e.to_string())?;
    let mut guard = Worker {
        control,
        ready: prepared,
        thread: Some(worker),
        begin: None,
        stop_timeout: Duration::from_secs(2),
    };
    match opening.recv_timeout(Duration::from_secs(2)) {
        Ok(Ok(())) => Ok((consumer, guard)),
        outcome => {
            let error = match outcome {
                Ok(Err(error)) => error,
                _ => "SC stream mapping timed out".into(),
            };
            let _ = guard.finish();
            Err(error)
        }
    }
}
