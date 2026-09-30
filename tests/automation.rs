use daw::{engine::Engine, session::Session};
use serde_json::{Value, json};
use std::{f64::consts::TAU, fs, path::Path};

fn effect(id: &str, gain: f64, bypass: bool) -> Value {
    json!({"kind":"gain", "id":id, "gain":gain, "bypass":bypass})
}

fn lane(effect_id: &str, points: &[(u64, f64)]) -> Value {
    json!({
        "effect_id":effect_id,
        "parameter":"gain",
        "interpolation":"step",
        "points":points.iter().map(|(frame, value)| json!({"frame":frame,"value":value})).collect::<Vec<_>>()
    })
}

fn v3(tracks: Vec<Value>) -> Session {
    serde_json::from_value(json!({
        "schema_version":3, "sample_rate":8000, "tempo_milli_bpm":120000,
        "tracks":tracks
    }))
    .unwrap()
}

fn sine_track(id: &str, gain: f64, effects: Vec<Value>, automation: Vec<Value>) -> Value {
    json!({
        "id":id, "mode":"continuous", "clips":[],
        "device":{"kind":"sine", "frequency_hz":1000.0, "gain":gain},
        "effects":effects, "automation":automation
    })
}

fn render(session: &Session, frames: usize, blocks: &[usize]) -> Vec<[f64; 2]> {
    let mut engine = Engine::prepare(session).unwrap();
    let mut output = vec![[0.0; 2]; frames];
    let mut offset = 0;
    for &block in blocks {
        if offset == frames {
            break;
        }
        let end = (offset + block).min(frames);
        engine.render_block(&mut output[offset..end]);
        offset = end;
    }
    if offset < frames {
        engine.render_block(&mut output[offset..]);
    }
    output
}

fn sine(frame: usize, gain: f64) -> [f64; 2] {
    let sample = (TAU * frame as f64 / 8.0).sin() * gain;
    [sample, sample]
}

fn write_constant_mono_wav(path: &Path, frames: usize) {
    let mut writer = hound::WavWriter::create(
        path,
        hound::WavSpec {
            channels: 1,
            sample_rate: 8000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    for _ in 0..frames {
        writer.write_sample(16384_i16).unwrap();
    }
    writer.finalize().unwrap();
}

fn pcm_session(root: &Path, points: &[(u64, f64)]) -> Session {
    let mut session = v3(vec![json!({
        "id":"pcm", "mode":"sequenced", "device":{"kind":"audio", "gain":1.0},
        "clips":[{"kind":"audio", "id":"clip", "start_frame":0, "length_frames":32,
            "source_path":"constant.wav", "source_offset_frames":0, "gain":1.0}],
        "effects":[effect("trim", 0.1, false)],
        "automation":[lane("trim", points)]
    })]);
    session.asset_root = Some(root.to_path_buf());
    session
}

#[test]
fn step_automation_uses_baseline_before_first_point_and_applies_points_at_their_frame() {
    let session = v3(vec![sine_track(
        "tone",
        1.0,
        vec![effect("trim", 0.125, false)],
        vec![lane("trim", &[(2, 0.5), (4, 0.75)])],
    )]);
    let output = render(&session, 7, &[7]);
    for (frame, sample) in output.iter().enumerate() {
        let gain = match frame {
            0..=1 => 0.125,
            2..=3 => 0.5,
            _ => 0.75,
        };
        assert!(
            (sample[0] - sine(frame, gain)[0]).abs() < 1e-12,
            "frame {frame}"
        );
        assert_eq!(sample[0], sample[1]);
    }
}

#[test]
fn frame_zero_automation_and_multiple_tracks_are_independent() {
    let session = v3(vec![
        sine_track(
            "first",
            0.5,
            vec![effect("trim", 0.25, false)],
            vec![lane("trim", &[(0, 0.75)])],
        ),
        sine_track(
            "second",
            0.25,
            vec![effect("trim", 0.5, false)],
            vec![lane("trim", &[(3, 1.0)])],
        ),
    ]);
    let output = render(&session, 6, &[6]);
    for (frame, sample) in output.iter().enumerate() {
        let first_gain = 0.5 * 0.75;
        let second_gain = 0.25 * if frame < 3 { 0.5 } else { 1.0 };
        let expected = (sine(frame, first_gain)[0] + sine(frame, second_gain)[0]).clamp(-1.0, 1.0);
        assert!((sample[0] - expected).abs() < 1e-12, "frame {frame}");
    }
}

#[test]
fn automation_multiplies_with_other_effects_and_bypassed_targets_stay_bypassed() {
    let session = v3(vec![sine_track(
        "tone",
        0.5,
        vec![
            effect("boost", 2.0, false),
            effect("trim", 0.5, false),
            effect("guard", 0.0, true),
        ],
        vec![
            lane("boost", &[(2, 1.0)]),
            lane("trim", &[(0, 0.25)]),
            lane("guard", &[(0, 4.0)]),
        ],
    )]);
    let output = render(&session, 6, &[6]);
    for (frame, sample) in output.iter().enumerate() {
        let boost = if frame < 2 { 2.0 } else { 1.0 };
        let expected = sine(frame, 0.5 * boost * 0.25)[0];
        assert!((sample[0] - expected).abs() < 1e-12, "frame {frame}");
    }
}

#[test]
fn automation_targets_effect_identity_when_chain_is_reordered() {
    let original = v3(vec![sine_track(
        "tone",
        1.0,
        vec![effect("A", 1.0, false), effect("B", 2.0, true)],
        vec![lane("B", &[(0, 0.0)])],
    )]);
    let reordered = v3(vec![sine_track(
        "tone",
        1.0,
        vec![effect("B", 2.0, true), effect("A", 1.0, false)],
        vec![lane("B", &[(0, 0.0)])],
    )]);
    let baseline = v3(vec![sine_track(
        "tone",
        1.0,
        vec![effect("A", 1.0, false), effect("B", 2.0, true)],
        vec![],
    )]);
    let expected = sine(1, 1.0)[0];
    for session in [&original, &reordered, &baseline] {
        let output = render(session, 2, &[2]);
        assert!((output[1][0] - expected).abs() < 1e-12);
    }

    let automate_active = v3(vec![sine_track(
        "tone",
        1.0,
        vec![effect("A", 1.0, false), effect("B", 2.0, true)],
        vec![lane("A", &[(0, 0.5)])],
    )]);
    let output = render(&automate_active, 2, &[2]);
    assert!((output[1][0] - sine(1, 0.5)[0]).abs() < 1e-12);
}

#[test]
fn output_is_block_independent_and_seek_reconstructs_lane_value_at_destination() {
    let dir = tempfile::tempdir().unwrap();
    write_constant_mono_wav(&dir.path().join("constant.wav"), 32);
    let session = pcm_session(dir.path(), &[(2, 0.2), (4, 0.6), (9, 0.8)]);

    for (destination, gain) in [
        (0, 0.1),
        (1, 0.1),
        (2, 0.2),
        (3, 0.2),
        (4, 0.6),
        (8, 0.6),
        (9, 0.8),
        (17, 0.8),
    ] {
        let mut engine = Engine::prepare(&session).unwrap();
        engine.seek(destination).unwrap();
        let mut sample = [[0.0; 2]; 1];
        engine.render_block(&mut sample);
        assert!(
            (sample[0][0] - 0.5 * gain).abs() < 1e-12,
            "seek {destination}"
        );
    }

    let mut backward = Engine::prepare(&session).unwrap();
    backward.seek(12).unwrap();
    backward.seek(1).unwrap();
    let mut sample = [[0.0; 2]; 1];
    backward.render_block(&mut sample);
    assert!((sample[0][0] - 0.05).abs() < 1e-12);
}

#[test]
fn looping_uses_lane_state_at_each_destination_without_stale_history() {
    let dir = tempfile::tempdir().unwrap();
    write_constant_mono_wav(&dir.path().join("constant.wav"), 32);
    let session = pcm_session(dir.path(), &[(0, 0.2), (3, 0.7), (8, 0.9)]);
    let mut engine = Engine::prepare(&session).unwrap();
    engine.set_loop(Some((2, 6))).unwrap();
    let mut output = [[0.0; 2]; 10];
    engine.render_block(&mut output);
    let expected_frames = [0, 1, 2, 3, 4, 5, 2, 3, 4, 5];
    for (sample, frame) in output.iter().zip(expected_frames) {
        let gain = if frame < 3 { 0.2 } else { 0.7 };
        assert!(
            (sample[0] - 0.5 * gain).abs() < 1e-12,
            "timeline frame {frame}"
        );
    }
}

fn write_stereo_wav(path: &Path) {
    let mut writer = hound::WavWriter::create(
        path,
        hound::WavSpec {
            channels: 2,
            sample_rate: 8000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    for sample in [8192_i16, -16384, 16384, -8192, 24576, -24576, -8192, 8192] {
        writer.write_sample(sample).unwrap();
    }
    writer.finalize().unwrap();
}

#[test]
fn automation_applies_to_stereo_pcm_after_source_offset_and_assets_are_preloaded() {
    let dir = tempfile::tempdir().unwrap();
    let asset = dir.path().join("stereo.wav");
    write_stereo_wav(&asset);
    let mut session = v3(vec![json!({
        "id":"audio", "mode":"sequenced", "device":{"kind":"audio", "gain":1.0},
        "clips":[{"kind":"audio", "id":"clip", "start_frame":0,"length_frames":3,
            "source_path":"stereo.wav", "source_offset_frames":1, "gain":1.0}],
        "effects":[effect("trim", 0.25, false)],
        "automation":[lane("trim", &[(2, 0.5)])]
    })]);
    session.asset_root = Some(dir.path().to_path_buf());
    let mut engine = Engine::prepare(&session).unwrap();
    fs::remove_file(asset).unwrap();
    let mut output = [[0.0; 2]; 3];
    engine.render_block(&mut output);
    assert_eq!(
        output,
        [[0.125, -0.0625], [0.1875, -0.1875], [-0.125, 0.125]]
    );
}
