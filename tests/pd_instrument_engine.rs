use daw::{
    engine::Engine,
    pd_instrument::Instrument,
    session::{Device, Session},
};
use serde_json::json;
fn session() -> Session {
    let mut device = serde_json::to_value(Instrument::default()).unwrap();
    device["kind"] = json!("pd_instrument");
    serde_json::from_value(json!({"schema_version":12,"sample_rate":48000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"pd","mode":"sequenced","device":device,"effects":[],
        "clips":[{"kind":"notes","id":"melody","start_frame":0,"length_frames":16000,
        "notes":[{"id":"a","start_frame":65,"duration_frames":4032,"frequency_hz":440,"velocity":0.8},
        {"id":"b","start_frame":8192,"duration_frames":4096,"frequency_hz":660,"velocity":0.4}]}]}]})).unwrap()
}
#[test]
fn portable_schema_and_embedding_roundtrip() {
    let s = session();
    s.validate().unwrap();
    let saved = serde_json::to_vec(&s).unwrap();
    assert_eq!(s, serde_json::from_slice::<Session>(&saved).unwrap());
    let mut bad = s.clone();
    bad.schema_version = 11;
    assert!(bad.validate().is_err());
    let mut bad = s.clone();
    bad.sample_rate = 44100;
    assert!(bad.validate().is_err());
    let Device::PdInstrument(i) = &mut bad.tracks[0].device else {
        unreachable!()
    };
    i.program.push_str("#X obj 0 0 print;\n");
    assert!(bad.validate().is_err());
}
#[test]
fn portable_control_and_monophony_validation() {
    let mut s = session();
    let Device::PdInstrument(i) = &mut s.tracks[0].device else {
        unreachable!()
    };
    i.controls[0].value = 12001.0;
    assert!(s.validate().is_err());
    let mut s = session();
    let daw::session::Clip::Notes(c) = &mut s.tracks[0].clips.as_mut().unwrap()[0] else {
        unreachable!()
    };
    c.notes[1].start_frame = 128;
    assert!(s.validate().unwrap_err().contains("monophonic"));
    let mut s = session();
    let daw::session::Clip::Notes(c) = &mut s.tracks[0].clips.as_mut().unwrap()[0] else {
        unreachable!()
    };
    c.notes[0].duration_frames = 63;
    assert!(s.validate().unwrap_err().contains("64"));
}
fn render(s: &Session, chunks: &[usize], frames: usize) -> Vec<[f64; 2]> {
    let mut e = Engine::prepare(s).unwrap();
    let mut out = vec![[0.0; 2]; frames];
    let mut start = 0;
    let mut n = 0;
    while start < frames {
        let end = (start + chunks[n % chunks.len()]).min(frames);
        e.render_block(&mut out[start..end]);
        start = end;
        n += 1;
    }
    out
}
fn rms(data: &[[f64; 2]]) -> f64 {
    (data.iter().map(|f| f[0] * f[0]).sum::<f64>() / data.len() as f64).sqrt()
}
#[test]
fn configured_runtime_sequences_notes_and_resets() {
    if std::env::var_os("DAW_LIBPD_LIBRARY").is_none() {
        return;
    }
    let s = session();
    let out = render(&s, &[16000], 16000);
    let chunked = render(&s, &[1, 17, 257, 63], 16000);
    assert_eq!(
        out, chunked,
        "Pd rendering must not depend on caller block size"
    );
    assert!(out[..128].iter().all(|f| *f == [0.0; 2]));
    assert!(rms(&out[512..4000]) > 0.08);
    assert!(
        out[4160..8192].iter().all(|f| *f == [0.0; 2]),
        "scheduled gate-off must silence patch"
    );
    assert!(rms(&out[8500..12000]) > 0.04);
    assert!(out[12288..].iter().all(|f| *f == [0.0; 2]));
    let mut e = Engine::prepare(&s).unwrap();
    let mut first = [[0.0; 2]; 2048];
    e.render_block(&mut first);
    e.seek(0).unwrap();
    let mut replay = [[0.0; 2]; 2048];
    e.render_block(&mut replay);
    assert_eq!(
        first, replay,
        "seek reset must clear oscillator and filter state"
    );
    e.set_loop(Some((0, 2048))).unwrap();
    e.seek(0).unwrap();
    let mut loops = [[0.0; 2]; 4096];
    e.render_block(&mut loops);
    assert_eq!(loops[..2048], loops[2048..]);
}
#[test]
fn configured_runtime_controls_mixer_and_instances() {
    if std::env::var_os("DAW_LIBPD_LIBRARY").is_none() {
        return;
    }
    let s = session();
    let baseline = render(&s, &[127], 16000);
    let mut quiet = s.clone();
    let Device::PdInstrument(i) = &mut quiet.tracks[0].device else {
        unreachable!()
    };
    i.gain *= 0.5;
    let reduced = render(&quiet, &[4096], 16000);
    assert_eq!(rms(&reduced[512..4000]), rms(&baseline[512..4000]) * 0.5);
    let mut dark = s.clone();
    let Device::PdInstrument(i) = &mut dark.tracks[0].device else {
        unreachable!()
    };
    i.controls[0].value = 100.0;
    let filtered = render(&dark, &[89], 16000);
    assert!(rms(&filtered[512..4000]) < rms(&baseline[512..4000]) * 0.3);
    let mut mixed = s.clone();
    mixed.tracks[0].mixer = Some(daw::session::Mixer {
        gain: 0.5,
        pan: 1.0,
        mute: false,
        solo: false,
    });
    mixed.tracks[0].effects = Some(
        serde_json::from_value(json!([{"kind":"gain","id":"attenuate","gain":0.5,"bypass":false}]))
            .unwrap(),
    );
    let balanced = render(&mixed, &[31], 16000);
    assert!(balanced.iter().all(|f| f[0] == 0.0));
    for (actual, expected) in balanced.iter().zip(&baseline) {
        assert_eq!(actual[1], expected[1] * 0.25);
    }
    // Simultaneous independent Pd instances cannot leak receivers/audio across tracks.
    let mut two = s.clone();
    let mut other = s.tracks[0].clone();
    other.id = "second-pd".into();
    two.tracks.push(other);
    let doubled = render(&two, &[47], 16000);
    for (actual, expected) in doubled.iter().zip(&baseline) {
        assert_eq!(*actual, [expected[0] * 2.0, expected[1] * 2.0]);
    }
}
// This covers Rust scheduling on the owned renderer thread. Foreign libpd
// mutexes are permitted here; the native ring callback has its own proof.
struct CountedAllocator;
thread_local! {
    static WATCH: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
    static OPERATIONS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}
fn count_allocation() {
    if WATCH.try_with(std::cell::Cell::get).unwrap_or(false) {
        let _ = OPERATIONS.try_with(|v| v.set(v.get() + 1));
    }
}
unsafe impl std::alloc::GlobalAlloc for CountedAllocator {
    unsafe fn alloc(&self, l: std::alloc::Layout) -> *mut u8 {
        count_allocation();
        unsafe { std::alloc::System.alloc(l) }
    }
    unsafe fn alloc_zeroed(&self, l: std::alloc::Layout) -> *mut u8 {
        count_allocation();
        unsafe { std::alloc::System.alloc_zeroed(l) }
    }
    unsafe fn dealloc(&self, p: *mut u8, l: std::alloc::Layout) {
        count_allocation();
        unsafe { std::alloc::System.dealloc(p, l) }
    }
    unsafe fn realloc(&self, p: *mut u8, l: std::alloc::Layout, n: usize) -> *mut u8 {
        count_allocation();
        unsafe { std::alloc::System.realloc(p, l, n) }
    }
}
#[global_allocator]
static ALLOCATOR: CountedAllocator = CountedAllocator;
#[test]
fn configured_renderer_scheduling_reset_and_loop_have_no_rust_heap_activity() {
    if std::env::var_os("DAW_LIBPD_LIBRARY").is_none() {
        return;
    }
    let mut engine = Engine::prepare(&session()).unwrap();
    let mut output = [[0.0; 2]; 257];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..32 {
        engine.seek(0).unwrap();
        engine.set_loop(Some((0, 16000))).unwrap();
        for _ in 0..70 {
            std::hint::black_box(engine.render_block(&mut output));
        }
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0);
}
