//! Portable checks for the indefinite built-in playback contract.
use daw::{
    audio_buffer::PlaybackBuffer,
    session::{Device, Effect, Session, Track},
};
use std::{
    alloc::{GlobalAlloc, Layout, System},
    cell::Cell,
};

struct Counted;
thread_local! {
    static WATCH: Cell<bool> = const { Cell::new(false) };
    static OPERATIONS: Cell<usize> = const { Cell::new(0) };
}
fn count() {
    if WATCH.try_with(Cell::get).unwrap_or(false) {
        OPERATIONS.with(|value| value.set(value.get() + 1));
    }
}
// SAFETY: pointers and layouts are forwarded unchanged to the system allocator.
unsafe impl GlobalAlloc for Counted {
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
static ALLOCATOR: Counted = Counted;

fn song() -> Session {
    serde_json::from_value(serde_json::json!({
        "schema_version":9,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[
            {"id":"sine","mode":"sequenced","effects":[],"device":{"kind":"sine","frequency_hz":440,"gain":0.1},"clips":[
                {"kind":"notes","id":"c","start_frame":0,"length_frames":4000,"notes":[
                    {"id":"n","start_frame":0,"duration_frames":1000,"frequency_hz":440,"velocity":0.5}]}]},
            {"id":"drums","mode":"sequenced","effects":[],"device":{"kind":"drumkit","kit_id":"factory-v1","gain":0.2},"clips":[
                {"kind":"notes","id":"c","start_frame":0,"length_frames":4000,"notes":[
                    {"id":"n","start_frame":0,"duration_frames":1,"frequency_hz":65.40639132514966,"velocity":0.8}]}]},
            {"id":"synth","mode":"sequenced","effects":[],"device":{"kind":"synth","waveform":"saw","gain":0.2,"attack_ms":5,"release_ms":100,"cutoff_hz":2000},"clips":[
                {"kind":"notes","id":"c","start_frame":0,"length_frames":4000,"notes":[
                    {"id":"n","start_frame":0,"duration_frames":1000,"frequency_hz":220,"velocity":0.5}]}]}
        ]
    })).unwrap()
}

#[test]
fn builtins_loop_past_sixty_seconds_without_callback_heap_activity() {
    let mut playback = PlaybackBuffer::prepare_until_stopped(&song(), 8000, 2, 0.5).unwrap();
    playback.set_loop(Some((0, 4000))).unwrap();
    let mut output = [0.0; 512];
    let mut rendered = 0;
    let mut late_peak = 0.0_f64;
    OPERATIONS.set(0);
    WATCH.set(true);
    for _ in 0..2000 {
        rendered += playback.fill(&mut output, |sample| sample).unwrap();
        if rendered > 8000 * 60 {
            for sample in output {
                late_peak = late_peak.max(sample.abs());
            }
        }
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0);
    assert_eq!(rendered, 512_000);
    assert_eq!(playback.output_position(), rendered);
    assert!(playback.frame_position() <= 4000);
    assert_eq!(playback.remaining_frames(), u64::MAX);
    assert!(
        late_peak > 0.01,
        "loop must remain audible past the old wall-clock cap"
    );
    playback.seek(123).unwrap();
    assert_eq!(playback.frame_position(), 123);
    assert_eq!(playback.output_position(), rendered);
}

#[test]
fn indefinite_preparation_rejects_foreign_devices_before_preparation() {
    let foreign = [
        Device::Supercollider(daw::sc_source::Source {
            synthdef_hex: String::new(),
            synth_name: "x".into(),
            duration_frames: 1,
            gain: 0.1,
            controls: vec![],
            prepared: None,
        }),
        Device::Csound(daw::csound_source::Source {
            program: String::new(),
            duration_frames: 1,
            gain: 0.1,
            controls: vec![],
            prepared: None,
        }),
        Device::Puredata(daw::puredata_source::Source {
            program: String::new(),
            duration_frames: 1,
            gain: 0.1,
            controls: vec![],
            abstractions: vec![],
            prepared: None,
        }),
    ];
    for device in foreign {
        let session = Session {
            tracks: vec![Track {
                id: "x".into(),
                device,
                mode: None,
                clips: None,
                effects: None,
                automation: None,
                mixer: None,
            }],
            ..Session::default()
        };
        let error = PlaybackBuffer::prepare_until_stopped(&session, 48000, 2, 0.5)
            .err()
            .unwrap();
        assert!(error.contains("built-in"), "{error}");
    }
    let mut session = song();
    session.tracks[0].effects = Some(vec![Effect::Vst3 {
        id: "foreign".into(),
        bypass: true,
        bundle_path: "/missing.vst3".into(),
        class_id: String::new(),
        state_hex: String::new(),
        controller_state_hex: String::new(),
        parameters: vec![],
    }]);
    assert!(
        PlaybackBuffer::prepare_until_stopped(&session, 8000, 2, 0.5)
            .err()
            .unwrap()
            .contains("built-in")
    );
    session.tracks[0].effects = Some(vec![Effect::Au {
        id: "foreign".into(),
        bypass: true,
        component_type: String::new(),
        component_subtype: String::new(),
        component_manufacturer: String::new(),
        state_hex: String::new(),
        parameters: vec![],
    }]);
    assert!(
        PlaybackBuffer::prepare_until_stopped(&session, 8000, 2, 0.5)
            .err()
            .unwrap()
            .contains("built-in")
    );
}

#[test]
fn indefinite_keeps_validation_and_silences_partial_frames() {
    let song = song();
    for (rate, channels, volume) in [
        (48000, 2, 0.5),
        (8000, 0, 0.5),
        (8000, 33, 0.5),
        (8000, 2, f64::NAN),
        (8000, 2, 1.1),
    ] {
        assert!(PlaybackBuffer::prepare_until_stopped(&song, rate, channels, volume).is_err());
    }
    let mut playback = PlaybackBuffer::prepare_until_stopped(&song, 8000, 2, 0.5).unwrap();
    let mut malformed = [1.0; 3];
    assert_eq!(
        playback.fill(&mut malformed, |sample| sample),
        Err("partial device frame")
    );
    assert_eq!(malformed, [0.0; 3]);
    assert_eq!(playback.output_position(), 0);
    assert_eq!(playback.remaining_frames(), u64::MAX);
}

fn write_pcm(path: &std::path::Path, rate: u32) {
    let mut writer = hound::WavWriter::create(
        path,
        hound::WavSpec {
            channels: 2,
            sample_rate: rate,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    for frame in 0..6000 {
        let sample = if frame % 2 == 0 { 4096_i16 } else { -4096_i16 };
        writer.write_sample(sample).unwrap();
        writer.write_sample(-sample).unwrap();
    }
    writer.finalize().unwrap();
}
fn mixed_song(root: &std::path::Path) -> Session {
    let mut session = song();
    let track = serde_json::from_value(serde_json::json!({
        "id":"audio","mode":"sequenced","device":{"kind":"audio","gain":0.6},
        "effects":[{"kind":"gain","id":"level","gain":0.8,"bypass":false}],
        "clips":[{"kind":"audio","id":"pcm","start_frame":0,"length_frames":4000,
            "source_path":"sample.wav","source_offset_frames":123,"gain":0.7}]
    }))
    .unwrap();
    session.tracks.push(track);
    session.asset_root = Some(root.to_path_buf());
    session
}

#[test]
fn mixed_pcm_and_instruments_seek_loop_past_sixty_seconds_without_io_or_allocations() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("sample.wav");
    write_pcm(&source, 8000);
    let mut playback =
        PlaybackBuffer::prepare_until_stopped(&mixed_song(directory.path()), 8000, 2, 0.5).unwrap();
    // Prepared PCM must own its data throughout playback, seeks and loop wraps.
    std::fs::remove_file(&source).unwrap();
    playback.set_loop(Some((0, 4000))).unwrap();
    let mut output = [0.0; 512];
    let mut late_peak = 0.0_f64;
    OPERATIONS.set(0);
    WATCH.set(true);
    for i in 0..2000 {
        if i % 41 == 0 {
            playback.seek(123).unwrap();
        }
        assert_eq!(playback.fill(&mut output, |sample| sample).unwrap(), 256);
        if playback.output_position() > 8000 * 60 {
            for sample in output {
                late_peak = late_peak.max(sample.abs());
            }
        }
    }
    WATCH.set(false);
    assert_eq!(OPERATIONS.get(), 0);
    assert_eq!(playback.output_position(), 512000);
    assert_eq!(playback.remaining_frames(), u64::MAX);
    assert!(late_peak > 0.01);
    // Seeking midway skips prior note onsets but chases the active PCM offset.
    playback.set_loop(None).unwrap();
    playback.seek(124).unwrap();
    let mut frame = [0.0; 2];
    playback.fill(&mut frame, |sample| sample).unwrap();
    let expected = -4096.0 / 32768.0 * (0.6 * 0.7) * 0.8 * 0.5;
    assert_eq!(frame, [expected, -expected]);
}

#[test]
fn failed_pcm_preparation_preserves_the_existing_prepared_buffer_and_session() {
    let directory = tempfile::tempdir().unwrap();
    let source = directory.path().join("sample.wav");
    write_pcm(&source, 8000);
    let session = mixed_song(directory.path());
    let original = session.clone();
    let mut existing = PlaybackBuffer::prepare_until_stopped(&session, 8000, 2, 0.5).unwrap();
    for invalid in 0..3 {
        match invalid {
            0 => std::fs::remove_file(&source).unwrap(),
            1 => std::fs::write(&source, b"invalid WAV bytes").unwrap(),
            _ => write_pcm(&source, 44100),
        }
        assert!(PlaybackBuffer::prepare_until_stopped(&session, 8000, 2, 0.5).is_err());
        assert_eq!(session, original);
        existing.seek(124).unwrap();
        let mut frame = [0.0; 2];
        assert_eq!(existing.fill(&mut frame, |sample| sample).unwrap(), 1);
        assert!(frame[0] != 0.0);
    }
    assert!(PlaybackBuffer::prepare_until_stopped(&session, 44100, 2, 0.5).is_err());
}
