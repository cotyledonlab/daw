use daw::{engine::Engine, session::Session};
use serde_json::json;
fn song() -> Session {
    serde_json::from_value(json!({"schema_version":11,"sample_rate":48000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"lead","mode":"sequenced","device":{"kind":"sine","frequency_hz":440,"gain":0.2},
        "mixer":{"gain":1.0,"pan":0.0,"mute":false,"solo":false},
        "effects":[{"id":"lp","kind":"lowpass","cutoff_hz":1500.0,"bypass":false},{"id":"echo","kind":"delay","time_ms":5.0,"feedback":0.3,"mix":0.25,"bypass":false}],
        "clips":[{"id":"c","kind":"notes","start_frame":0,"length_frames":48000,"notes":[{"id":"held","start_frame":0,"duration_frames":24000,"frequency_hz":440,"velocity":0.8}]}]}]})).unwrap()
}
#[test]
fn replacement_retains_held_note_phase_filters_delay_tails_and_clocks() {
    let session = song();
    let mut original = Engine::prepare(&session).unwrap();
    let mut block = [[0.0; 2]; 1024];
    original.render_block(&mut block);
    let mut updated = Engine::prepare(&session).unwrap();
    updated.adopt_live(&mut original, &session, &session);
    let mut expected = [[0.0; 2]; 1024];
    original.render_block(&mut expected);
    updated.render_block(&mut block);
    assert_eq!(block, expected);
    assert_eq!(updated.frame_position(), original.frame_position());
    assert_eq!(updated.output_position(), original.output_position());
}
#[test]
fn future_note_and_clip_edit_preserves_current_voice_and_plays_next_onset() {
    let session = song();
    let mut value = serde_json::to_value(&session).unwrap();
    value["tracks"][0]["clips"].as_array_mut().unwrap().push(json!({"id":"new","kind":"notes","start_frame":2048,"length_frames":4096,"notes":[{"id":"new-note","start_frame":0,"duration_frames":1024,"frequency_hz":880,"velocity":0.5}]}));
    let next: Session = serde_json::from_value(value).unwrap();
    let mut original = Engine::prepare(&session).unwrap();
    let mut block = [[0.0; 2]; 1024];
    original.render_block(&mut block);
    let mut updated = Engine::prepare(&next).unwrap();
    updated.adopt_live(&mut original, &session, &next);
    let mut expected = [[0.0; 2]; 1024];
    original.render_block(&mut expected);
    updated.render_block(&mut block);
    assert_eq!(
        block, expected,
        "future edits must not disturb the held note"
    );
    original.render_block(&mut expected);
    updated.render_block(&mut block);
    assert_ne!(
        block, expected,
        "the newly inserted clip must sound at its scheduled frame"
    );
}
#[test]
fn live_gain_change_keeps_dsp_state_and_loop_without_moving_saved_frames() {
    let session = song();
    let mut value = serde_json::to_value(&session).unwrap();
    value["tracks"][0]["mixer"]["gain"] = json!(0.5);
    let next: Session = serde_json::from_value(value).unwrap();
    let mut original = Engine::prepare(&session).unwrap();
    original.set_loop(Some((0, 4096))).unwrap();
    let mut block = [[0.0; 2]; 1024];
    original.render_block(&mut block);
    let mut updated = Engine::prepare(&next).unwrap();
    updated.adopt_live(&mut original, &session, &next);
    for _ in 0..8 {
        let mut expected = [[0.0; 2]; 1024];
        original.render_block(&mut expected);
        updated.render_block(&mut block);
        for (actual, reference) in block.iter().zip(expected) {
            assert_eq!(*actual, [reference[0] * 0.5, reference[1] * 0.5]);
        }
        assert_eq!(updated.frame_position(), original.frame_position());
    }
    assert_eq!(session.tracks[0].clips, next.tracks[0].clips);
}
