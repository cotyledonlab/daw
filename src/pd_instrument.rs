//! One bounded monophonic Pd preset. Foreign calls run on the renderer worker,
//! never the hardware callback: public libpd APIs take internal mutexes.
use crate::session::{Clip, Device, Track};
use serde::{Deserialize, Serialize};
pub const PROGRAM: &str = include_str!("../native/puredata/instrument.pd");
pub const BLOCK: u64 = 64;
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Instrument {
    pub program: String,
    pub abstractions: Vec<crate::puredata_source::Abstraction>,
    pub gain: f64,
    pub controls: Vec<Control>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Control {
    pub name: String,
    #[serde(rename = "type")]
    pub kind: ControlType,
    pub default: f64,
    pub min: f64,
    pub max: f64,
    pub value: f64,
}
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ControlType {
    Float,
}
impl Default for Instrument {
    fn default() -> Self {
        Self {
            program: PROGRAM.into(),
            abstractions: vec![],
            gain: 0.2,
            controls: vec![Control {
                name: "cutoff".into(),
                kind: ControlType::Float,
                default: 4000.0,
                min: 100.0,
                max: 12000.0,
                value: 4000.0,
            }],
        }
    }
}
impl Instrument {
    pub fn preflight(&self, rate: u32) -> Result<(), String> {
        self.validate(rate)?;
        let _runtime = runtime::Runtime::prepare(self, rate)?;
        Ok(())
    }
    pub fn validate(&self, rate: u32) -> Result<(), String> {
        if rate != 48000 {
            return Err("Pd instrument requires 48000 Hz".into());
        }
        if self.program.replace("\r\n", "\n") != PROGRAM || !self.abstractions.is_empty() {
            return Err("Pd instrument MVP supports only the embedded monophonic preset and no abstractions".into());
        }
        if !self.gain.is_finite() || !(0.0..=1.0).contains(&self.gain) {
            return Err("Pd instrument gain must be finite in 0..1".into());
        }
        let [c] = self.controls.as_slice() else {
            return Err("Pd instrument requires one typed cutoff control".into());
        };
        if c.name != "cutoff"
            || c.default != 4000.0
            || c.min != 100.0
            || c.max != 12000.0
            || !c.value.is_finite()
            || !(c.min..=c.max).contains(&c.value)
        {
            return Err("Pd cutoff metadata must be default 4000, min 100, max 12000 with a finite value in range".into());
        }
        Ok(())
    }
}
// Absolute timeline event times are rounded up to the next Pd tick. This keeps
// one-tick gates intact and gives identical output for arbitrary caller chunks.
fn tick(frame: u64) -> u64 {
    frame.div_ceil(BLOCK).saturating_mul(BLOCK)
}
pub fn validate_notes(track: &Track) -> Result<(), String> {
    let mut gates = Vec::new();
    for clip in track.clips.iter().flatten() {
        let Clip::Notes(clip) = clip else {
            return Err("Pd instruments require notes clips".into());
        };
        for n in &clip.notes {
            if n.duration_frames < BLOCK {
                return Err("Pd notes require at least 64 frames".into());
            }
            let start = clip
                .start_frame
                .checked_add(n.start_frame)
                .ok_or("Pd note start overflow")?;
            let off = start
                .checked_add(n.duration_frames)
                .ok_or("Pd note end overflow")?;
            gates.push((start, off));
        }
    }
    gates.sort_unstable();
    if gates.windows(2).any(|w| w[0].1 > w[1].0) {
        return Err("Pd instrument is monophonic; note gates must not overlap across clips".into());
    }
    Ok(())
}
pub fn has_instruments(session: &crate::session::Session) -> bool {
    session
        .tracks
        .iter()
        .any(|t| matches!(t.device, Device::PdInstrument(_)))
}
#[derive(Debug, Clone, Copy)]
struct Event {
    frame: u64,
    frequency: f32,
    velocity: f32,
    gate: f32,
}
#[derive(Debug)]
pub(crate) struct Prepared {
    pub track_index: usize,
    gain: f64,
    events: Vec<Event>,
    next: usize,
    position: usize,
    samples: [f32; 128],
    runtime: runtime::Runtime,
}
impl Prepared {
    pub fn prepare(track: &Track, track_index: usize, rate: u32) -> Result<Self, String> {
        let Device::PdInstrument(instrument) = &track.device else {
            unreachable!()
        };
        let runtime = runtime::Runtime::prepare(instrument, rate)?;
        let mut events = Vec::new();
        for clip in track.clips.iter().flatten() {
            let Clip::Notes(clip) = clip else {
                unreachable!()
            };
            for note in &clip.notes {
                let start = clip.start_frame + note.start_frame;
                events.push(Event {
                    frame: tick(start),
                    frequency: note.frequency_hz as f32,
                    velocity: note.velocity as f32,
                    gate: 1.0,
                });
                events.push(Event {
                    frame: tick(start + note.duration_frames),
                    frequency: 0.0,
                    velocity: 0.0,
                    gate: 0.0,
                });
            }
        }
        events.sort_by(|a, b| a.frame.cmp(&b.frame).then(a.gate.total_cmp(&b.gate)));
        Ok(Self {
            track_index,
            gain: instrument.gain,
            events,
            next: 0,
            position: 64,
            samples: [0.0; 128],
            runtime,
        })
    }
    pub fn reset(&mut self, frame: u64) {
        self.runtime.reset();
        self.next = self.events.partition_point(|e| e.frame < frame);
        self.samples.fill(0.0);
        self.position = 64;
    }
    pub fn sample(&mut self, frame: u64) -> [f64; 2] {
        // A seek may land inside a Pd tick. Emit silence until the first absolute
        // tick, then process aligned blocks. Previously sounding notes aren't chased.
        if self.position == 64 {
            if frame % BLOCK != 0 {
                return [0.0; 2];
            }
            while let Some(event) = self.events.get(self.next).copied() {
                if event.frame > frame {
                    break;
                }
                self.runtime
                    .note(event.frequency, event.velocity, event.gate);
                self.next += 1;
            }
            self.runtime.process(&mut self.samples);
            self.position = 0;
        }
        let index = self.position * 2;
        self.position += 1;
        [
            f64::from(self.samples[index]) * self.gain,
            f64::from(self.samples[index + 1]) * self.gain,
        ]
    }
}

#[cfg(not(unix))]
mod runtime {
    use super::Instrument;
    #[derive(Debug)]
    pub struct Runtime;
    impl Runtime {
        pub fn prepare(_: &Instrument, _: u32) -> Result<Self, String> {
            Err("Pd instruments require a Unix libpd MULTI build".into())
        }
        pub fn reset(&mut self) {}
        pub fn note(&mut self, _: f32, _: f32, _: f32) {}
        pub fn process(&mut self, _: &mut [f32; 128]) {}
    }
}
#[cfg(unix)]
mod runtime {
    use super::Instrument;
    use std::{
        ffi::{CString, c_char, c_int, c_void},
        path::PathBuf,
        sync::{Mutex, OnceLock},
    };
    unsafe extern "C" {
        fn dlopen(path: *const c_char, flags: c_int) -> *mut c_void;
        fn dlsym(handle: *mut c_void, name: *const c_char) -> *mut c_void;
    }
    type Void = unsafe extern "C" fn(*mut c_void);
    #[derive(Debug)]
    struct Api {
        _library: usize,
        path: PathBuf,
        new: unsafe extern "C" fn() -> *mut c_void,
        set: Void,
        free: Void,
        close: Void,
        audio: unsafe extern "C" fn(c_int, c_int, c_int) -> c_int,
        open: unsafe extern "C" fn(*const c_char, *const c_char) -> *mut c_void,
        dollar: unsafe extern "C" fn(*mut c_void) -> c_int,
        float: unsafe extern "C" fn(*const c_char, f32) -> c_int,
        start: unsafe extern "C" fn(c_int) -> c_int,
        add: unsafe extern "C" fn(f32),
        finish: unsafe extern "C" fn(*const c_char, *const c_char) -> c_int,
        process: unsafe extern "C" fn(c_int, *const f32, *mut f32) -> c_int,
    }
    static API: OnceLock<Api> = OnceLock::new();
    static SETUP: Mutex<()> = Mutex::new(());
    fn api() -> Result<&'static Api, String> {
        let path=PathBuf::from(std::env::var_os("DAW_LIBPD_LIBRARY").ok_or("Pd instrument runtime unavailable; set DAW_LIBPD_LIBRARY to an absolute MULTI libpd library")?);
        if !path.is_absolute() || !path.is_file() {
            return Err("DAW_LIBPD_LIBRARY must name an absolute libpd library file".into());
        }
        if let Some(api) = API.get() {
            if api.path != path {
                return Err(
                    "Changing DAW_LIBPD_LIBRARY requires restarting the renderer process".into(),
                );
            }
            return Ok(api);
        }
        use std::os::unix::ffi::OsStrExt;
        let library_path = path.clone();
        let path = CString::new(path.as_os_str().as_bytes()).map_err(|_| "invalid libpd path")?;
        let library = unsafe { dlopen(path.as_ptr(), 2) };
        if library.is_null() {
            return Err("cannot load Pd instrument libpd library".into());
        }
        macro_rules! symbol {
            ($name:literal,$ty:ty) => {{
                let ptr = unsafe { dlsym(library, concat!($name, "\0").as_ptr().cast()) };
                if ptr.is_null() {
                    return Err(format!("libpd is missing {}", $name));
                }
                unsafe { std::mem::transmute::<*mut c_void, $ty>(ptr) }
            }};
        }
        let init = symbol!("libpd_init", unsafe extern "C" fn() -> c_int);
        let size = symbol!("libpd_blocksize", unsafe extern "C" fn() -> c_int);
        let api = Api {
            _library: library as usize,
            path: library_path,
            new: symbol!("libpd_new_instance", unsafe extern "C" fn() -> *mut c_void),
            set: symbol!("libpd_set_instance", Void),
            free: symbol!("libpd_free_instance", Void),
            close: symbol!("libpd_closefile", Void),
            audio: symbol!(
                "libpd_init_audio",
                unsafe extern "C" fn(c_int, c_int, c_int) -> c_int
            ),
            open: symbol!(
                "libpd_openfile",
                unsafe extern "C" fn(*const c_char, *const c_char) -> *mut c_void
            ),
            dollar: symbol!(
                "libpd_getdollarzero",
                unsafe extern "C" fn(*mut c_void) -> c_int
            ),
            float: symbol!(
                "libpd_float",
                unsafe extern "C" fn(*const c_char, f32) -> c_int
            ),
            start: symbol!("libpd_start_message", unsafe extern "C" fn(c_int) -> c_int),
            add: symbol!("libpd_add_float", unsafe extern "C" fn(f32)),
            finish: symbol!(
                "libpd_finish_message",
                unsafe extern "C" fn(*const c_char, *const c_char) -> c_int
            ),
            process: symbol!(
                "libpd_process_float",
                unsafe extern "C" fn(c_int, *const f32, *mut f32) -> c_int
            ),
        };
        unsafe {
            init();
        }
        if unsafe { size() } != 64 {
            return Err("Pd instrument needs 64-frame libpd blocks".into());
        }
        let _ = API.set(api);
        Ok(API.get().expect("installed API"))
    }
    #[derive(Debug)]
    pub struct Runtime {
        api: &'static Api,
        instance: *mut c_void,
        patch: *mut c_void,
        names: [CString; 5],
        _directory: tempfile::TempDir,
    }
    // Exclusive ownership is moved between threads only outside processing. Each
    // operation selects this instance on its current thread; no Rust references
    // to Pd memory escape. Hardware callback never calls these methods.
    unsafe impl Send for Runtime {}
    impl Runtime {
        pub fn prepare(instrument: &Instrument, rate: u32) -> Result<Self, String> {
            let _setup = SETUP.lock().map_err(|_| "Pd setup lock poisoned")?;
            let api = api()?;
            let directory = tempfile::tempdir().map_err(|e| e.to_string())?;
            std::fs::write(directory.path().join("instrument.pd"), &instrument.program)
                .map_err(|e| e.to_string())?;
            use std::os::unix::ffi::OsStrExt;
            let path = CString::new(directory.path().as_os_str().as_bytes())
                .map_err(|_| "invalid Pd directory")?;
            let instance = unsafe { (api.new)() };
            if instance.is_null() {
                return Err("Pd instrument requires libpd built with MULTI=true".into());
            }
            let mut runtime = Self {
                api,
                instance,
                patch: std::ptr::null_mut(),
                names: std::array::from_fn(|_| CString::default()),
                _directory: directory,
            };
            unsafe {
                (api.set)(instance);
            }
            if unsafe { (api.audio)(0, 2, rate as c_int) } != 0 {
                return Err("Pd instrument audio initialization failed".into());
            }
            runtime.patch = unsafe { (api.open)(c"instrument.pd".as_ptr(), path.as_ptr()) };
            if runtime.patch.is_null() {
                return Err("Pd instrument patch failed to open".into());
            }
            let prefix = unsafe { (api.dollar)(runtime.patch) };
            if prefix <= 0 {
                return Err("Pd instrument patch identity missing".into());
            }
            runtime.names = ["frequency", "velocity", "gate", "reset", "cutoff"]
                .map(|name| CString::new(format!("{prefix}-{name}")).unwrap());
            for (index, value) in [
                (0, 440.0),
                (1, 0.0),
                (2, 0.0),
                (3, 0.0),
                (4, instrument.controls[0].value as f32),
            ] {
                if unsafe { (api.float)(runtime.names[index].as_ptr(), value) } != 0 {
                    return Err("Pd instrument required receiver missing".into());
                }
            }
            if unsafe { (api.start)(1) } != 0 {
                return Err("Pd DSP startup failed".into());
            }
            unsafe {
                (api.add)(1.0);
            }
            if unsafe { (api.finish)(c"pd".as_ptr(), c"dsp".as_ptr()) } != 0 {
                return Err("Pd DSP startup failed".into());
            }
            // Force DSP graph construction and lazy initialization on preparation.
            let mut warm = [0.0; 128];
            runtime.process(&mut warm);
            runtime.reset();
            Ok(runtime)
        }
        pub fn reset(&mut self) {
            unsafe {
                (self.api.set)(self.instance);
                (self.api.float)(self.names[3].as_ptr(), 0.0);
            }
        }
        pub fn note(&mut self, frequency: f32, velocity: f32, gate: f32) {
            unsafe {
                (self.api.set)(self.instance);
                if gate != 0.0 {
                    (self.api.float)(self.names[0].as_ptr(), frequency);
                    (self.api.float)(self.names[1].as_ptr(), velocity);
                }
                (self.api.float)(self.names[2].as_ptr(), gate);
            }
        }
        pub fn process(&mut self, output: &mut [f32; 128]) {
            unsafe {
                (self.api.set)(self.instance);
                (self.api.process)(1, std::ptr::null(), output.as_mut_ptr());
            }
        }
    }
    impl Drop for Runtime {
        fn drop(&mut self) {
            unsafe {
                (self.api.set)(self.instance);
                if !self.patch.is_null() {
                    (self.api.close)(self.patch);
                }
                (self.api.free)(self.instance);
            }
        }
    }
}
