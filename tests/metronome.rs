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

use daw::{
    audio_buffer::PlaybackBuffer,
    metronome::Metronome,
    session::{Session, tick_to_frame},
};
fn session(tempo: u32) -> Session {
    serde_json::from_value(serde_json::json!({"schema_version":10,"sample_rate":48000,"tempo_milli_bpm":tempo,"tracks":[]})).unwrap()
}
#[test]
fn beats_follow_exact_half_up_grid_and_accent_every_four() {
    for tempo in [120000, 137123, 300000] {
        let click = Metronome::new(48000, tempo, true, 0).unwrap();
        for beat in 0..16 {
            let frame = tick_to_frame(beat * 960, 48000, tempo).unwrap();
            assert_eq!(click.sample(frame), if beat % 4 == 0 { 0.32 } else { 0.2 });
            if frame != 0 {
                assert_eq!(click.sample(frame - 1), 0.0);
            }
            assert_eq!(click.sample(frame + 480), 0.0);
        }
    }
}
#[test]
fn count_in_freezes_both_positions_then_renders_from_requested_seek() {
    let session = session(120000);
    let mut playback = PlaybackBuffer::prepare(&session, 48000, 4, 0.01, 0.5).unwrap();
    playback.configure_metronome(&session, false, 1).unwrap();
    playback.seek(12345).unwrap();
    let mut output = vec![7.0; 96000 * 4];
    assert_eq!(playback.fill(&mut output, |s| s).unwrap(), 0);
    assert_eq!(playback.frame_position(), 12345);
    assert_eq!(playback.output_position(), 0);
    assert_eq!(playback.remaining_frames(), 480);
    assert_eq!(playback.count_in_remaining_frames(), 0);
    assert_eq!(output[0], 0.16);
    assert_eq!(output[24000 * 4], 0.1);
    assert!(
        output
            .chunks_exact(4)
            .all(|frame| frame[2] == 0.0 && frame[3] == 0.0)
    );
    let mut tail = [7.0; 2000];
    assert_eq!(playback.fill(&mut tail, |s| s).unwrap(), 480);
    assert_eq!(playback.frame_position(), 12825);
    assert_eq!(playback.output_position(), 480);
    assert!(tail.iter().all(|s| *s == 0.0));
}
#[test]
fn count_in_transitions_within_callback_and_seek_does_not_restart_it() {
    let session = session(300000);
    let mut playback = PlaybackBuffer::prepare_until_stopped(&session, 48000, 2, 1.0).unwrap();
    playback.configure_metronome(&session, true, 1).unwrap();
    let count = playback.count_in_remaining_frames() as usize;
    let mut output = vec![0.0; (count + 3) * 2];
    assert_eq!(playback.fill(&mut output, |s| s).unwrap(), 3);
    assert_eq!(output[count * 2], 0.32);
    assert_eq!(playback.frame_position(), 3);
    assert_eq!(playback.output_position(), 3);
    playback.seek(9600).unwrap();
    let mut output = [0.0; 2];
    playback.fill(&mut output, |s| s).unwrap();
    assert_eq!(output[0], 0.2);
    assert_eq!(playback.count_in_remaining_frames(), 0);
}
#[test]
fn loop_wraps_click_with_timeline_and_malformed_buffers_do_not_advance() {
    let session = session(120000);
    let mut playback = PlaybackBuffer::prepare_until_stopped(&session, 48000, 2, 1.0).unwrap();
    playback.configure_metronome(&session, true, 0).unwrap();
    playback.set_loop(Some((0, 24000))).unwrap();
    playback.seek(23999).unwrap();
    let mut bad = [7.0; 3];
    assert!(playback.fill(&mut bad, |s| s).is_err());
    assert_eq!(playback.frame_position(), 23999);
    assert_eq!(bad, [0.0; 3]);
    let mut output = [7.0; 4];
    playback.fill(&mut output, |s| s).unwrap();
    assert_eq!(output, [0.0, 0.0, 0.32, 0.32]);
    assert_eq!(playback.frame_position(), 1);
}
#[test]
fn validation_and_monitor_headroom() {
    assert!(Metronome::new(48000, 120000, true, 3).is_err());
    let mut session = session(120000);
    session.tracks = serde_json::from_value(serde_json::json!([{"id":"tone","mode":"continuous","device":{"kind":"sine","frequency_hz":12000,"gain":1.0},"effects":[],"clips":[]}])).unwrap();
    let mut playback = PlaybackBuffer::prepare(&session, 48000, 2, 0.01, 1.0).unwrap();
    playback.configure_metronome(&session, true, 0).unwrap();
    let mut output = [0.0; 960];
    playback.fill(&mut output, |s| s).unwrap();
    assert!(
        output
            .iter()
            .all(|s| s.is_finite() && (-1.0..=1.0).contains(s))
    );
    assert_eq!(output[2], 1.0);
}

#[test]
fn click_callback_count_in_and_transport_changes_are_allocation_free() {
    let session = session(300000);
    let mut playback = PlaybackBuffer::prepare_until_stopped(&session, 48000, 2, 1.0).unwrap();
    playback.configure_metronome(&session, true, 1).unwrap();
    let mut output = [0.0; 1024];
    OPERATIONS.set(0);
    WATCH.set(true);
    let mut valid = true;
    for _ in 0..100 {
        valid &= playback.fill(&mut output, |s| s).is_ok();
    }
    valid &= playback.seek(0).is_ok();
    valid &= playback.set_loop(Some((0, 24000))).is_ok();
    for _ in 0..100 {
        valid &= playback.fill(&mut output, |s| s).is_ok();
    }
    WATCH.set(false);
    assert!(valid);
    assert_eq!(OPERATIONS.get(), 0);
    assert_eq!(playback.count_in_remaining_frames(), 0);
}

#[test]
fn transport_rejects_invalid_click_params_before_starting_audio() {
    let mut controller = daw::control::Controller::default();
    for params in [
        serde_json::json!({"seconds":1,"volume":0,"count_in_bars":3}),
        serde_json::json!({"seconds":1,"volume":0,"count_in_bars":-1}),
        serde_json::json!({"seconds":1,"volume":0,"count_in_bars":0.5}),
        serde_json::json!({"seconds":1,"volume":0,"metronome":"true"}),
        serde_json::json!({"seconds":1,"volume":0,"metronome":true,"source_mode":"live"}),
    ] {
        let response = serde_json::to_value(controller.handle_line(&serde_json::json!({
            "protocol_version":1,"id":"click-validation","method":"transport.play","params":params
        }).to_string())).unwrap();
        assert_eq!(response["ok"], false, "{response}");
        assert_eq!(response["error"]["code"], "invalid_params", "{response}");
    }
}

#[test]
fn runtime_sources_are_rejected_before_click_preparation() {
    let runtime: Session = serde_json::from_value(serde_json::json!({
        "schema_version":7,"sample_rate":48000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"runtime","mode":"continuous","clips":[],"effects":[],
        "device":{"kind":"csound","program":"<CsoundSynthesizer/>" ,
        "duration_frames":48,"gain":0.5,"controls":[]}}]
    }))
    .unwrap();
    runtime.validate().unwrap();
    assert!(!daw::metronome::supports_session(&runtime));
    let builtin = session(120000);
    let mut playback = PlaybackBuffer::prepare(&builtin, 48000, 2, 0.01, 0.5).unwrap();
    assert!(playback.configure_metronome(&runtime, true, 0).is_err());
    assert!(!playback.metronome_enabled());
}
