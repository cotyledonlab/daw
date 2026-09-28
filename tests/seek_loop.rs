use daw::{audio_buffer::PlaybackBuffer, engine::Engine, session::Session};
use std::path::Path;

fn session(clips: serde_json::Value, device: serde_json::Value) -> Session {
    serde_json::from_value(serde_json::json!({
        "schema_version":2,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"track","mode":"sequenced","device":device,"clips":clips}]
    }))
    .unwrap()
}

fn write_impulse(path: &Path) {
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: 8000,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::create(path, spec).unwrap();
    for sample in [8192i16, 16384, 24576, -8192, 0, 4096, 0] {
        writer.write_sample(sample).unwrap();
    }
    writer.finalize().unwrap();
}

fn audio_session(root: &Path) -> Session {
    let mut session = session(
        serde_json::json!([{
            "kind":"audio","id":"clip","start_frame":0,"length_frames":7,
            "source_path":"source.wav","source_offset_frames":0,"gain":1.0
        }]),
        serde_json::json!({"kind":"audio","gain":1.0}),
    );
    session.asset_root = Some(root.to_path_buf());
    session
}

#[test]
fn seek_skips_old_note_onsets_but_keeps_destination_events_and_counts_output() {
    let s = session(
        serde_json::json!([{
            "kind":"notes","id":"notes","start_frame":0,"length_frames":80,
            "notes":[
                {"id":"old","start_frame":4,"duration_frames":20,"frequency_hz":1000.0,"velocity":1.0},
                {"id":"at","start_frame":12,"duration_frames":20,"frequency_hz":1000.0,"velocity":1.0}
            ]
        }]),
        serde_json::json!({"kind":"sine","frequency_hz":440.0,"gain":1.0}),
    );
    let mut engine = Engine::prepare(&s).unwrap();
    engine.seek(8).unwrap();
    let mut skipped = [[0.0; 2]; 4];
    engine.render_block(&mut skipped);
    assert_eq!(skipped, [[0.0; 2]; 4]);
    let mut onset = [[0.0; 2]; 2];
    engine.render_block(&mut onset);
    assert_eq!(onset[0], [0.0; 2]);
    assert!(onset[1][0] > 0.0);
    assert_eq!(engine.frame_position(), 14);
    assert_eq!(engine.output_position(), 6);
}

#[test]
fn seek_enters_audio_at_source_offset_and_works_through_playback_buffer() {
    let dir = tempfile::tempdir().unwrap();
    write_impulse(&dir.path().join("source.wav"));
    let s = audio_session(dir.path());
    let mut buffer = PlaybackBuffer::prepare(&s, 8000, 2, 1.0, 1.0).unwrap();
    buffer.seek(3).unwrap();
    let mut out = [0.0f64; 4];
    buffer.fill(&mut out, |sample| sample).unwrap();
    assert_eq!(out, [-0.25, -0.25, 0.0, 0.0]);
    assert_eq!(buffer.frame_position(), 5);
    assert_eq!(buffer.output_position(), 2);
}

#[test]
fn loop_wrap_excludes_end_and_uneven_blocks_match_one_block() {
    let dir = tempfile::tempdir().unwrap();
    write_impulse(&dir.path().join("source.wav"));
    let s = audio_session(dir.path());
    let mut whole = Engine::prepare(&s).unwrap();
    whole.set_loop(Some((1, 4))).unwrap();
    let mut expected = [[0.0; 2]; 9];
    whole.render_block(&mut expected);
    assert_eq!(whole.frame_position(), 3);
    assert_eq!(whole.output_position(), 9);
    assert_eq!(
        expected.iter().map(|x| x[0]).collect::<Vec<_>>(),
        vec![0.25, 0.5, 0.75, -0.25, 0.5, 0.75, -0.25, 0.5, 0.75]
    );

    let mut split = Engine::prepare(&s).unwrap();
    split.set_loop(Some((1, 4))).unwrap();
    let mut actual = [[0.0; 2]; 9];
    split.render_block(&mut actual[..2]);
    split.render_block(&mut actual[2..7]);
    split.render_block(&mut actual[7..]);
    assert_eq!(actual, expected);
}

#[test]
fn seek_and_loop_reject_positions_outside_contract() {
    let s = session(
        serde_json::json!([]),
        serde_json::json!({"kind":"sine","frequency_hz":440.0,"gain":1.0}),
    );
    let mut engine = Engine::prepare(&s).unwrap();
    assert!(engine.seek(daw::session::MAX_FRAME + 1).is_err());
    assert!(engine.set_loop(Some((0, 0))).is_err());
    assert!(engine.set_loop(Some((2, 1))).is_err());
    assert!(
        engine
            .set_loop(Some((0, daw::session::MAX_FRAME + 1)))
            .is_err()
    );
    engine.seek(daw::session::MAX_FRAME).unwrap();
    engine
        .set_loop(Some((daw::session::MAX_FRAME - 1, daw::session::MAX_FRAME)))
        .unwrap();
}

#[test]
fn seek_clears_continuous_oscillator_phase() {
    let s: Session = serde_json::from_value(serde_json::json!({
        "schema_version":2,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"tone","mode":"continuous","device":{"kind":"sine","frequency_hz":1000.0,"gain":1.0},"clips":[]}]
    })).unwrap();
    let mut engine = Engine::prepare(&s).unwrap();
    let mut first = [[0.0; 2]; 3];
    engine.render_block(&mut first);
    engine.seek(10).unwrap();
    let mut after_seek = [[0.0; 2]; 2];
    engine.render_block(&mut after_seek);
    assert_eq!(after_seek[0], [0.0, 0.0]);
    assert!((after_seek[1][0] - std::f64::consts::FRAC_1_SQRT_2).abs() < 1e-12);
    assert!((after_seek[1][1] - std::f64::consts::FRAC_1_SQRT_2).abs() < 1e-12);
}

#[test]
fn loop_wrap_clears_notes_skips_spanning_note_chase_and_excludes_end_onsets() {
    let s = session(
        serde_json::json!([{
            "kind":"notes","id":"notes","start_frame":0,"length_frames":20,
            "notes":[
                {"id":"spanning","start_frame":0,"duration_frames":18,"frequency_hz":1000.0,"velocity":1.0},
                {"id":"inside","start_frame":4,"duration_frames":2,"frequency_hz":1000.0,"velocity":1.0},
                {"id":"at_end","start_frame":8,"duration_frames":2,"frequency_hz":1000.0,"velocity":1.0}
            ]
        }]),
        serde_json::json!({"kind":"sine","frequency_hz":440.0,"gain":1.0}),
    );
    let mut engine = Engine::prepare(&s).unwrap();
    engine.set_loop(Some((2, 8))).unwrap();
    let mut output = [[0.0; 2]; 14];
    engine.render_block(&mut output);
    // The spanning gate plays through the first pass, but is cleared at wrap and
    // does not restart at frame 2. The event at excluded frame 8 never starts.
    assert!(output[2][0] != 0.0);
    assert_eq!(output[8], [0.0; 2]);
    assert_eq!(output[9], [0.0; 2]);
    assert_eq!(output[10], [0.0; 2]); // frame 4 starts the inner gate at phase zero.
    assert!(output[11][0] > 0.0);
}

#[test]
fn seek_at_or_past_loop_end_wraps_before_sampling_and_disabling_loop_is_linear() {
    let dir = tempfile::tempdir().unwrap();
    write_impulse(&dir.path().join("source.wav"));
    let s = audio_session(dir.path());
    let mut engine = Engine::prepare(&s).unwrap();
    engine.set_loop(Some((1, 4))).unwrap();
    engine.seek(4).unwrap();
    assert_eq!(engine.frame_position(), 4); // Wrap is applied when the next sample is requested.
    let mut sample = [[0.0; 2]; 1];
    engine.render_block(&mut sample);
    assert_eq!(sample[0], [0.5, 0.5]); // loop frame 1, before its sample is emitted.
    assert_eq!(engine.frame_position(), 2);
    engine.seek(9).unwrap();
    engine.render_block(&mut sample);
    assert_eq!(sample[0], [0.5, 0.5]);
    assert_eq!(engine.frame_position(), 2);

    let mut linear = Engine::prepare(&s).unwrap();
    linear.set_loop(Some((1, 3))).unwrap();
    let mut first = [[0.0; 2]; 4];
    linear.render_block(&mut first);
    assert_eq!(linear.frame_position(), 2);
    linear.set_loop(None).unwrap();
    linear.render_block(&mut sample);
    assert_eq!(linear.frame_position(), 3);
    linear.render_block(&mut sample);
    assert_eq!(linear.frame_position(), 4);
}

#[test]
fn repeated_seeks_do_not_reset_playback_duration() {
    let dir = tempfile::tempdir().unwrap();
    write_impulse(&dir.path().join("source.wav"));
    let s = audio_session(dir.path());
    let mut buffer = PlaybackBuffer::prepare(&s, 8000, 2, 0.001, 1.0).unwrap();
    assert_eq!(buffer.remaining_frames(), 8);
    buffer.set_loop(Some((1, 4))).unwrap();
    for position in [3, 1, 4, 0, 2] {
        buffer.seek(position).unwrap();
    }
    assert_eq!(buffer.remaining_frames(), 8);
    let mut out = [0.0f64; 16];
    assert_eq!(buffer.fill(&mut out, |sample| sample).unwrap(), 8);
    assert_eq!(buffer.remaining_frames(), 0);
    assert_eq!(buffer.output_position(), 8);
}

#[test]
fn seek_restores_overlapping_audio_in_identity_order() {
    let dir = tempfile::tempdir().unwrap();
    let samples = [
        ("a.wav", [0i16, 0, 16384, 0, 0, 0, 0]),
        ("b.wav", [0i16, 16384, 0, 0, 0, 0, 0]),
        ("c.wav", [0i16, 0, 0, -16384, 0, 0, 0]),
    ];
    for (name, frames) in samples {
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: 8000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut writer = hound::WavWriter::create(dir.path().join(name), spec).unwrap();
        for sample in frames {
            writer.write_sample(sample).unwrap();
        }
        writer.finalize().unwrap();
    }
    let mut s = session(
        serde_json::json!([
            {"kind":"audio","id":"c","start_frame":0,"length_frames":5,"source_path":"c.wav","source_offset_frames":0,"gain":1.0},
            {"kind":"audio","id":"a","start_frame":1,"length_frames":5,"source_path":"a.wav","source_offset_frames":0,"gain":1.0},
            {"kind":"audio","id":"b","start_frame":2,"length_frames":5,"source_path":"b.wav","source_offset_frames":0,"gain":1e-16}
        ]),
        serde_json::json!({"kind":"audio","gain":1.0}),
    );
    s.asset_root = Some(dir.path().to_path_buf());
    let mut sequential = Engine::prepare(&s).unwrap();
    let mut discard = [[0.0; 2]; 3];
    sequential.render_block(&mut discard);
    let mut expected = [[0.0; 2]; 1];
    sequential.render_block(&mut expected);
    let mut engine = Engine::prepare(&s).unwrap();
    engine.seek(3).unwrap();
    let mut output = [[0.0; 2]; 1];
    engine.render_block(&mut output);
    // Start order is c, a, b; identity order is a(+0.5), b(tiny), c(-0.5).
    assert_eq!(expected[0], [0.0, 0.0]);
    assert_eq!(output, expected);
}
