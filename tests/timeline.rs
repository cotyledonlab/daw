use daw::{audio_buffer::PlaybackBuffer, engine::Engine, session::Session};

fn note_session(clips: Vec<serde_json::Value>) -> Session {
    serde_json::from_value(serde_json::json!({
        "schema_version": 2,
        "sample_rate": 8000,
        "tempo_milli_bpm": 120000,
        "tracks": [{
            "id": "track",
            "mode": "sequenced",
            "device": {"kind": "sine", "frequency_hz": 1000.0, "gain": 0.5},
            "clips": clips
        }]
    }))
    .unwrap()
}

fn clip(id: &str, start: u64, length: u64, notes: Vec<serde_json::Value>) -> serde_json::Value {
    serde_json::json!({
        "kind": "notes", "id": id, "start_frame": start,
        "length_frames": length, "notes": notes
    })
}

fn note(id: &str, start: u64, duration: u64, frequency: f64, velocity: f64) -> serde_json::Value {
    serde_json::json!({
        "id": id, "start_frame": start, "duration_frames": duration,
        "frequency_hz": frequency, "velocity": velocity
    })
}

fn render(session: &Session, frames: usize, blocks: &[usize]) -> Vec<[f64; 2]> {
    let mut engine = Engine::prepare(session).unwrap();
    let mut result = vec![[0.0; 2]; frames];
    let mut offset = 0;
    for &size in blocks {
        if offset == frames {
            break;
        }
        let end = (offset + size).min(frames);
        engine.render_block(&mut result[offset..end]);
        offset = end;
    }
    if offset < frames {
        engine.render_block(&mut result[offset..]);
    }
    assert_eq!(engine.frame_position(), frames as u64);
    result
}

#[test]
fn note_on_is_phase_zero_and_attack_release_follow_five_ms_formula() {
    let session = note_session(vec![clip("c", 0, 128, vec![note("n", 0, 80, 1000.0, 1.0)])]);
    let samples = render(&session, 128, &[128]);
    let gain = 0.5;
    let sine = |frame: usize| (std::f64::consts::TAU * 1000.0 * frame as f64 / 8000.0).sin();
    assert_eq!(samples[0], [0.0, 0.0]);
    for frame in [1, 8, 39] {
        let expected = sine(frame) * gain * (frame as f64 / 40.0);
        assert!(
            (samples[frame][0] - expected).abs() < 1e-12,
            "frame {frame}"
        );
    }
    assert!((samples[40][0] - sine(40) * gain).abs() < 1e-12);
    for frame in [80, 81, 100, 119] {
        let expected = sine(frame) * gain * (1.0 - (frame - 80) as f64 / 40.0);
        assert!(
            (samples[frame][0] - expected).abs() < 1e-12,
            "frame {frame}"
        );
    }
    assert!(samples[120..].iter().all(|sample| *sample == [0.0, 0.0]));
}

#[test]
fn short_gate_releases_from_partial_attack_level() {
    let session = note_session(vec![clip("c", 0, 100, vec![note("n", 0, 10, 1000.0, 1.0)])]);
    let samples = render(&session, 60, &[60]);
    let sine = |frame: usize| (std::f64::consts::TAU * frame as f64 / 8.0).sin();
    let release_start = 10.0 / 40.0;
    for frame in [10, 11, 20, 49] {
        let expected = sine(frame) * 0.5 * release_start * (1.0 - (frame - 10) as f64 / 40.0);
        assert!(
            (samples[frame][0] - expected).abs() < 1e-12,
            "frame {frame}"
        );
    }
    assert_eq!(samples[50], [0.0, 0.0]);
}

#[test]
fn clip_bounds_silence_and_cuts_release_at_half_open_end() {
    let session = note_session(vec![clip("c", 8, 20, vec![note("n", 4, 12, 1000.0, 1.0)])]);
    let samples = render(&session, 40, &[3, 2, 1, 7]);
    assert!(samples[..12].iter().all(|sample| *sample == [0.0, 0.0]));
    assert!(samples[28..].iter().all(|sample| *sample == [0.0, 0.0]));
    assert!(samples[16..28].iter().any(|sample| sample[0] != 0.0));
}

#[test]
fn adjacent_clips_do_not_leak_a_release_tail() {
    let session = note_session(vec![
        clip("a", 0, 16, vec![note("one", 0, 16, 1000.0, 1.0)]),
        clip("b", 16, 24, vec![note("two", 0, 16, 1000.0, 1.0)]),
    ]);
    let samples = render(&session, 48, &[16, 1, 5]);
    assert_eq!(samples[16], [0.0, 0.0]);
    let expected = (std::f64::consts::TAU / 8.0).sin() * 0.5 / 40.0;
    assert!((samples[17][0] - expected).abs() < 1e-12);
    let tail_at_33 =
        (std::f64::consts::TAU * 33.0 / 8.0).sin() * 0.5 * (16.0 / 40.0) * (1.0 - 1.0 / 40.0);
    assert!((samples[33][0] - tail_at_33).abs() < 1e-12);
    assert!(samples[40..].iter().all(|sample| *sample == [0.0, 0.0]));
}

#[test]
fn uneven_blocks_and_reversed_serialization_have_stable_samples() {
    let a = note("a", 4, 28, 1000.0, 0.8);
    let b = note("b", 0, 32, 500.0, 0.6);
    let c = note("c", 8, 24, 250.0, 0.4);
    let d = note("d", 12, 20, 2000.0, 0.3);
    let make_session = |reverse: bool| {
        let first = clip(
            "z",
            0,
            64,
            if reverse {
                vec![b.clone(), a.clone()]
            } else {
                vec![a.clone(), b.clone()]
            },
        );
        let second = clip("a", 0, 64, vec![c.clone(), d.clone()]);
        let tracks = vec![
            serde_json::json!({"id":"z-track", "mode":"sequenced", "device":{"kind":"sine", "frequency_hz":1000.0, "gain":0.5}, "clips": if reverse {vec![first.clone(),second.clone()]} else {vec![second.clone(),first.clone()]}}),
            serde_json::json!({"id":"a-track", "mode":"sequenced", "device":{"kind":"sine", "frequency_hz":1000.0, "gain":0.5}, "clips": if reverse {vec![second,first]} else {vec![first,second]}}),
        ];
        let tracks = if reverse {
            tracks.into_iter().rev().collect::<Vec<_>>()
        } else {
            tracks
        };
        serde_json::from_value(serde_json::json!({
            "schema_version":2, "sample_rate":8000, "tempo_milli_bpm":120000, "tracks":tracks
        }))
        .unwrap()
    };
    let forward = make_session(false);
    let reversed = make_session(true);
    let whole = render(&forward, 96, &[96]);
    assert_eq!(whole, render(&forward, 96, &[1, 7, 3, 19, 2, 64]));
    assert_eq!(whole, render(&reversed, 96, &[11, 1, 84]));
}

#[test]
fn same_pitch_overlapping_voices_release_independently() {
    let session = note_session(vec![clip(
        "c",
        0,
        100,
        vec![
            note("early", 0, 16, 1000.0, 1.0),
            note("late", 8, 32, 1000.0, 1.0),
        ],
    )]);
    let samples = render(&session, 64, &[64]);
    let sine = |frame: usize| (std::f64::consts::TAU * frame as f64 / 8.0).sin();
    let at_21 = sine(21) * 0.5 * ((16.0 / 40.0) * (1.0 - 5.0 / 40.0) + 13.0 / 40.0);
    assert!((samples[21][0] - at_21).abs() < 1e-12);
    let at_57 = sine(57) * 0.5 * (32.0 / 40.0) * (1.0 - 17.0 / 40.0);
    assert!((samples[57][0] - at_57).abs() < 1e-12);
}

#[test]
fn explicit_v1_upgrade_keeps_continuous_samples_identical() {
    let v1 = serde_json::json!({
        "schema_version": 1, "sample_rate": 8000,
        "tracks": [
            {"id":"z", "device":{"kind":"sine", "frequency_hz":1000.0, "gain":0.2}},
            {"id":"a", "device":{"kind":"sine", "frequency_hz":500.0, "gain":0.3}}
        ]
    });
    let original: Session = serde_json::from_value(v1.clone()).unwrap();
    let upgraded: Session = serde_json::from_value(serde_json::json!({
        "schema_version": 2, "sample_rate": 8000, "tempo_milli_bpm":120000,
        "tracks": v1["tracks"].as_array().unwrap().iter().map(|track| {
            serde_json::json!({"id":track["id"], "mode":"continuous", "device":track["device"], "clips":[]})
        }).collect::<Vec<_>>()
    })).unwrap();
    assert_eq!(
        render(&original, 128, &[128]),
        render(&upgraded, 128, &[5, 1, 80])
    );
}

#[test]
fn native_buffer_accepts_rate_matched_sequenced_session_and_rejects_mismatch() {
    let session = note_session(vec![clip("c", 0, 32, vec![note("n", 0, 32, 1000.0, 1.0)])]);
    let mut buffer = PlaybackBuffer::prepare(&session, 8000, 2, 0.004, 1.0).unwrap();
    let mut output = [0.0f64; 16];
    assert_eq!(buffer.fill(&mut output, |sample| sample).unwrap(), 8);
    assert_eq!(output[0], 0.0);
    assert!(PlaybackBuffer::prepare(&session, 16000, 2, 0.004, 1.0).is_err());
}
