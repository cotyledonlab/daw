//! Counts heap activity on this test thread only; engine preparation is excluded.
use daw::{
    audio_buffer::PlaybackBuffer,
    engine::Engine,
    session::{Device, Session, Track},
};
use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::Cell,
};

struct CountedAllocator;
thread_local! {
    static WATCH: Cell<bool> = const { Cell::new(false) };
    static OPERATIONS: Cell<usize> = const { Cell::new(0) };
}

#[test]
fn device_buffer_adapter_does_not_allocate_or_free() {
    let session = Session {
        tracks: vec![Track {
            mode: None,
            clips: None,
            effects: None,
            automation: None,
            mixer: None,
            id: "tone".into(),
            device: Device::Sine {
                frequency_hz: 440.0,
                gain: 0.1,
            },
        }],
        ..Session::default()
    };
    let mut playback = PlaybackBuffer::prepare(&session, 44_100, 2, 1.0, 0.25).unwrap();
    let mut output = [0.0_f32; 1024];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..100 {
        let _ = std::hint::black_box(playback.fill(&mut output, |x| x as f32));
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0);
    assert_eq!(playback.remaining_frames(), 0);
    assert!(output.iter().all(|&sample| sample == 0.0));
}
fn count() {
    if WATCH.try_with(Cell::get).unwrap_or(false) {
        let _ = OPERATIONS.try_with(|count| count.set(count.get() + 1));
    }
}
// SAFETY: every operation delegates its original pointer/layout to System.
unsafe impl GlobalAlloc for CountedAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc(layout) }
    }
    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count();
        unsafe { System.alloc_zeroed(layout) }
    }
    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        count();
        unsafe { System.dealloc(ptr, layout) }
    }
    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
        count();
        unsafe { System.realloc(ptr, layout, size) }
    }
}
#[global_allocator]
static ALLOCATOR: CountedAllocator = CountedAllocator;

#[test]
fn prepared_render_blocks_do_not_allocate_or_free() {
    let session = Session {
        tracks: (0..64)
            .map(|i| Track {
                mode: None,
                clips: None,
                effects: None,
                automation: None,
                mixer: None,
                id: i.to_string(),
                device: Device::Sine {
                    frequency_hz: 100.0 + i as f64,
                    gain: 0.01,
                },
            })
            .collect(),
        ..Session::default()
    };
    let mut engine = Engine::prepare(&session).unwrap();
    let mut output = [[0.0; 2]; 257];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..100 {
        engine.seek(5).unwrap();
        engine.set_loop(Some((3, 7))).unwrap();
        std::hint::black_box(engine.render_block(&mut output));
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0, "rendering performed heap operations");
    assert!(output.iter().any(|frame| frame[0] != 0.0));
}

#[test]
fn sequenced_onsets_release_and_voice_reuse_do_not_allocate() {
    let notes: Vec<_> = (0..128)
        .map(|i| {
            serde_json::json!({
                "id":format!("n{i:03}"), "start_frame":(i / 64) * 140,
                "duration_frames":100, "frequency_hz":100.0 + (i % 64) as f64,
                "velocity":0.5
            })
        })
        .collect();
    let session: Session = serde_json::from_value(serde_json::json!({
        "schema_version":2,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"notes","mode":"sequenced","device":{"kind":"sine","frequency_hz":440,"gain":0.01},
        "clips":[{"kind":"notes","id":"c","start_frame":0,"length_frames":400,"notes":notes}]}]
    })).unwrap();
    let mut playback = PlaybackBuffer::prepare(&session, 8000, 2, 0.1, 0.25).unwrap();
    let mut output = [0.0_f64; 34];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..60 {
        playback.seek(80).unwrap();
        playback.set_loop(Some((70, 90))).unwrap();
        let _ = std::hint::black_box(playback.fill(&mut output, |x| x));
    }
    WATCH.set(false);
    assert_eq!(
        OPERATIONS.get(),
        0,
        "note scheduling performed heap operations"
    );
    assert_eq!(playback.remaining_frames(), 0);
}

#[test]
fn preloaded_audio_clip_callbacks_do_not_allocate_or_free() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("source.wav");
    let mut writer = hound::WavWriter::create(
        &path,
        hound::WavSpec {
            channels: 2,
            sample_rate: 8000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    for _ in 0..400 {
        writer.write_sample(1024_i16).unwrap();
        writer.write_sample(-512_i16).unwrap();
    }
    writer.finalize().unwrap();
    let mut session:Session=serde_json::from_value(serde_json::json!({
        "schema_version":2,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"a","mode":"sequenced","device":{"kind":"audio","gain":1},"clips":[
            {"kind":"audio","id":"first","start_frame":1,"length_frames":100,"fade_in_frames":10,"fade_out_frames":30,"source_offset_frames":0,"source_path":"source.wav","gain":0.5},
            {"kind":"audio","id":"second","start_frame":101,"length_frames":200,"fade_in_frames":20,"fade_out_frames":40,"source_offset_frames":100,"source_path":"source.wav","gain":0.5}
        ]}]
    })).unwrap();
    session.asset_root = Some(dir.path().to_path_buf());
    let mut playback = PlaybackBuffer::prepare(&session, 8000, 2, 0.1, 0.25).unwrap();
    std::fs::remove_file(path).unwrap();
    let mut output = [0.0; 34];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..60 {
        playback.seek(80).unwrap();
        playback.set_loop(Some((70, 90))).unwrap();
        let _ = std::hint::black_box(playback.fill(&mut output, |x| x));
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0);
    assert_eq!(playback.remaining_frames(), 0);
}

#[test]
fn full_automated_chains_do_not_allocate_during_render_seek_or_loop() {
    let tracks: Vec<_> = (0..64)
        .map(|i| {
            serde_json::json!({
                "id":format!("t{i}"), "mode":"continuous", "clips":[],
                "device":{"kind":"sine","frequency_hz":100.0+i as f64,"gain":0.01},
                "effects":(0..16).map(|j| serde_json::json!({
                    "kind":"gain","id":format!("g{j}"),"gain":0.9,"bypass":false
                })).collect::<Vec<_>>(),
                "automation":(0..16).map(|j| serde_json::json!({
                    "effect_id":format!("g{j}"),"parameter":"gain","interpolation":"step",
                    "points":(0..16).map(|f| serde_json::json!({"frame":f,"value":0.8+f as f64*0.01})).collect::<Vec<_>>()
                })).collect::<Vec<_>>()
            })
        })
        .collect();
    let session: Session = serde_json::from_value(serde_json::json!({
        "schema_version":3,"sample_rate":8000,"tempo_milli_bpm":120000,"tracks":tracks
    }))
    .unwrap();
    let mut engine = Engine::prepare(&session).unwrap();
    let mut output = [[0.0; 2]; 33];
    OPERATIONS.set(0);
    WATCH.set(true);
    engine.set_loop(Some((1, 17))).unwrap();
    for _ in 0..20 {
        engine.seek(10).unwrap();
        engine.render_block(&mut output);
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0);
    assert!(output.iter().any(|frame| frame[0] != 0.0));
}

#[test]
fn factory_drums_and_polyphonic_synth_do_not_allocate_in_callback() {
    let session: Session = serde_json::from_value(serde_json::json!({
        "schema_version":9,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[
            {"id":"drums","mode":"sequenced","effects":[],"device":{"kind":"drumkit","kit_id":"factory-v1","gain":0.2},"clips":[
                {"kind":"notes","id":"c","start_frame":0,"length_frames":5000,"notes":[
                    {"id":"kick","start_frame":0,"duration_frames":1,"frequency_hz":65.40639132514966,"velocity":0.8}]}]},
            {"id":"synth","mode":"sequenced","effects":[],"device":{"kind":"synth","waveform":"saw","gain":0.2,"attack_ms":5,"release_ms":100,"cutoff_hz":2000},"clips":[
                {"kind":"notes","id":"c","start_frame":0,"length_frames":5000,"notes":[
                    {"id":"a","start_frame":0,"duration_frames":200,"frequency_hz":440,"velocity":0.5},
                    {"id":"b","start_frame":0,"duration_frames":200,"frequency_hz":550,"velocity":0.5}]}]}
        ]
    })).unwrap();
    let mut engine = Engine::prepare(&session).unwrap();
    let mut output = [[0.0; 2]; 257];
    OPERATIONS.set(0);
    WATCH.set(true);
    engine.set_loop(Some((0, 1100))).unwrap();
    for _ in 0..50 {
        engine.seek(0).unwrap();
        for _ in 0..20 {
            std::hint::black_box(engine.render_block(&mut output));
        }
    }
    WATCH.set(false);
    assert_eq!(
        OPERATIONS.get(),
        0,
        "factory instrument callback performed heap operations"
    );
}

#[test]
fn mixer_callback_and_meter_aggregation_do_not_allocate() {
    let session: Session = serde_json::from_value(serde_json::json!({
        "schema_version":10,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"a","mode":"continuous","clips":[],"effects":[{"kind":"gain","id":"fx","gain":2.0,"bypass":false}],"device":{"kind":"sine","frequency_hz":1000,"gain":0.25},"mixer":{"gain":2.0,"pan":-0.5,"mute":false,"solo":true}}]
    })).unwrap();
    let mut playback = PlaybackBuffer::prepare_until_stopped(&session, 8000, 2, 0.5).unwrap();
    let mut output = [0.0; 1200];
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..100 {
        playback.fill(&mut output, |x| x).unwrap();
        std::hint::black_box(playback.mixer_peaks().unwrap());
    }
    WATCH.set(false);
    assert_eq!(
        OPERATIONS.get(),
        0,
        "mixer callback performed heap operations"
    );
    assert!((playback.mixer_peaks().unwrap().master[0] - 1.0).abs() < 1e-12);
}

#[test]
fn stateful_builtin_effects_do_not_allocate_during_callback_seek_or_loop() {
    let session: Session = serde_json::from_value(serde_json::json!({
        "schema_version":11,"sample_rate":48000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"processed","mode":"continuous","clips":[],
        "device":{"kind":"sine","frequency_hz":440,"gain":0.1},"effects":[
            {"kind":"gain","id":"trim","gain":0.5,"bypass":false},
            {"kind":"lowpass","id":"filter","cutoff_hz":800,"bypass":false},
            {"kind":"delay","id":"echo","time_ms":2000,"feedback":0.95,"mix":0.5,"bypass":false}
        ],"automation":[{"effect_id":"trim","parameter":"gain","interpolation":"step","points":[{"frame":5,"value":0.75}]}]}]
    })).unwrap();
    let mut playback = PlaybackBuffer::prepare_until_stopped(&session, 48000, 2, 1.0).unwrap();
    let mut output = [0.0; 514];
    OPERATIONS.set(0);
    WATCH.set(true);
    playback.set_loop(Some((5, 6))).unwrap();
    for _ in 0..256 {
        playback.seek(5).unwrap();
        playback.fill(&mut output, |s| s).unwrap();
    }
    WATCH.set(false);
    assert_eq!(
        OPERATIONS.get(),
        0,
        "stateful processing/reset performed heap operations"
    );
    assert!(output.iter().all(|s| s.is_finite()));
}
