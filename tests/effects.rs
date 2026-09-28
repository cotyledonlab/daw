use daw::{engine::Engine, session::Session};
use serde_json::{Value, json};
use std::{f64::consts::TAU, path::Path};
use tempfile::tempdir;

fn effect(id: &str, gain: f64, bypass: bool) -> Value {
    json!({"kind":"gain", "id":id, "gain":gain, "bypass":bypass})
}

fn v3(tracks: Vec<Value>) -> Session {
    serde_json::from_value(json!({
        "schema_version":3, "sample_rate":8000, "tempo_milli_bpm":120000,
        "tracks":tracks
    }))
    .unwrap()
}

fn sine_track(id: &str, gain: f64, effects: Vec<Value>) -> Value {
    json!({
        "id":id, "mode":"continuous", "clips":[],
        "device":{"kind":"sine", "frequency_hz":1000.0, "gain":gain},
        "effects":effects
    })
}

fn render(session: &Session, frames: usize, blocks: &[usize]) -> (Vec<[f64; 2]>, u64) {
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
    (output, engine.frame_position())
}

#[test]
fn ordered_gain_cascade_and_bypass_are_applied_per_track() {
    let baseline = v3(vec![sine_track("dry", 0.25, vec![])]);
    let bypassed = v3(vec![sine_track(
        "dry",
        0.25,
        vec![effect("muted", 0.0, true)],
    )]);
    let cascaded = v3(vec![sine_track(
        "dry",
        0.25,
        vec![effect("boost", 2.0, false), effect("trim", 0.5, false)],
    )]);
    let (expected, _) = render(&baseline, 32, &[32]);
    assert_eq!(render(&bypassed, 32, &[32]).0, expected);
    assert_eq!(render(&cascaded, 32, &[32]).0, expected);

    let nonzero = 3;
    let sine = (TAU * 1000.0 * nonzero as f64 / 8000.0).sin();
    assert!((expected[nonzero][0] - sine * 0.25).abs() < 1e-12);
}

#[test]
fn boost_then_attenuation_does_not_clip_between_effects() {
    let session = v3(vec![sine_track(
        "tone",
        1.0,
        vec![
            effect("boost", 4.0, false),
            effect("attenuate", 0.25, false),
        ],
    )]);
    let (output, _) = render(&session, 8, &[8]);
    for (frame, sample) in output.iter().enumerate() {
        let expected = (TAU * frame as f64 / 8.0).sin();
        assert!((sample[0] - expected).abs() < 1e-12, "frame {frame}");
        assert_eq!(sample[0], sample[1]);
    }
}

#[test]
fn note_overlap_is_summed_before_effect_and_clipped_afterward() {
    let session = v3(vec![json!({
        "id":"notes", "mode":"sequenced",
        "device":{"kind":"sine", "frequency_hz":1000.0, "gain":1.0},
        "clips":[{"kind":"notes", "id":"clip", "start_frame":0,
            "length_frames":80, "notes":[
                {"id":"a", "start_frame":0, "duration_frames":80, "frequency_hz":1000.0, "velocity":1.0},
                {"id":"b", "start_frame":0, "duration_frames":80, "frequency_hz":1000.0, "velocity":1.0}
            ]}],
        "effects":[effect("boost", 2.0, false)]
    })]);
    let (output, _) = render(&session, 48, &[48]);
    assert_eq!(output[41], [1.0, 1.0]); // the overlapping voices exceed full scale
    assert_eq!(output[0], [0.0, 0.0]);
    assert!(output[2..].iter().any(|frame| frame == &[1.0, 1.0]));
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
    for sample in [8192_i16, -16384, 16384, -8192, 24576, -24576] {
        writer.write_sample(sample).unwrap();
    }
    writer.finalize().unwrap();
}

#[test]
fn stereo_pcm_gain_preserves_channels_before_master_clipping() {
    let dir = tempdir().unwrap();
    write_stereo_wav(&dir.path().join("stereo.wav"));
    let mut session = v3(vec![json!({
        "id":"audio", "mode":"sequenced",
        "device":{"kind":"audio", "gain":1.0},
        "clips":[{"kind":"audio", "id":"source", "start_frame":0,
            "length_frames":3, "source_path":"stereo.wav", "source_offset_frames":0, "gain":1.0}],
        "effects":[effect("boost", 2.0, false)]
    })]);
    session.asset_root = Some(dir.path().to_path_buf());
    let (output, _) = render(&session, 3, &[3]);
    assert_eq!(output, vec![[0.5, -1.0], [1.0, -0.5], [1.0, -1.0]]);
}

#[test]
fn track_chains_are_isolated_and_overlapping_audio_is_attenuated_before_master_clip() {
    let isolated = v3(vec![
        sine_track("muted", 0.5, vec![effect("mute", 0.0, false)]),
        sine_track("audible", 0.25, vec![]),
    ]);
    let reference = v3(vec![sine_track("audible", 0.25, vec![])]);
    assert_eq!(
        render(&isolated, 32, &[32]).0,
        render(&reference, 32, &[32]).0
    );

    let dir = tempdir().unwrap();
    write_stereo_wav(&dir.path().join("stereo.wav"));
    let mut session = v3(vec![json!({
        "id":"audio", "mode":"sequenced",
        "device":{"kind":"audio", "gain":1.0},
        "clips":[
            {"kind":"audio", "id":"left", "start_frame":0, "length_frames":1,
                "source_path":"stereo.wav", "source_offset_frames":2, "gain":1.0},
            {"kind":"audio", "id":"right", "start_frame":0, "length_frames":1,
                "source_path":"stereo.wav", "source_offset_frames":2, "gain":1.0}
        ],
        "effects":[effect("attenuate", 0.5, false)]
    })]);
    session.asset_root = Some(dir.path().to_path_buf());
    assert_eq!(render(&session, 1, &[1]).0, vec![[0.75, -0.75]]);
}

#[test]
fn effects_keep_samples_stable_across_uneven_blocks_seek_and_loop() {
    let session = v3(vec![sine_track(
        "tone",
        0.4,
        vec![effect("trim", 1.5, false), effect("bypass", 0.0, true)],
    )]);
    let expected = render(&session, 80, &[80]).0;
    assert_eq!(render(&session, 80, &[1, 7, 3, 19, 2, 48]).0, expected);

    let mut whole = Engine::prepare(&session).unwrap();
    let mut split = Engine::prepare(&session).unwrap();
    whole.set_loop(Some((5, 23))).unwrap();
    split.set_loop(Some((5, 23))).unwrap();
    whole.seek(5).unwrap();
    split.seek(5).unwrap();
    let mut a = vec![[0.0; 2]; 53];
    let mut b = vec![[0.0; 2]; 53];
    whole.render_block(&mut a);
    let mut offset = 0;
    for size in [1, 4, 2, 11, 3, 32] {
        let end = (offset + size).min(b.len());
        split.render_block(&mut b[offset..end]);
        offset = end;
    }
    assert_eq!(offset, b.len());
    assert_eq!(a, b);
}

#[test]
fn schema_three_effect_state_round_trips_and_invalid_effects_fail_before_prepare() {
    let value = json!({
        "schema_version":3, "sample_rate":8000, "tempo_milli_bpm":120000,
        "tracks":[sine_track("tone", 0.4,
            vec![effect("trim", 2.0, false), effect("guard", 1.0, true)])]
    });
    let session: Session = serde_json::from_value(value.clone()).unwrap();
    assert_eq!(serde_json::to_value(&session).unwrap(), value);

    let mut invalid = value;
    invalid["tracks"][0]["effects"][0]["gain"] = json!(4.01);
    let invalid: Session = serde_json::from_value(invalid).unwrap();
    assert!(invalid.validate().is_err());
    assert!(Engine::prepare(&invalid).is_err());

    let mut missing = serde_json::to_value(&session).unwrap();
    missing["tracks"][0]
        .as_object_mut()
        .unwrap()
        .remove("effects");
    let missing: Session = serde_json::from_value(missing).unwrap();
    assert!(missing.validate().is_err());
    assert!(Engine::prepare(&missing).is_err());

    let mut over_limit = serde_json::to_value(&session).unwrap();
    over_limit["tracks"][0]["effects"] = json!(
        (0..17)
            .map(|n| effect(&n.to_string(), 1.0, false))
            .collect::<Vec<_>>()
    );
    let over_limit: Session = serde_json::from_value(over_limit).unwrap();
    assert!(over_limit.validate().is_err());
    assert!(Engine::prepare(&over_limit).is_err());
}

#[test]
fn schema_one_and_two_sessions_retain_their_old_rendering_behavior() {
    let v1: Session = serde_json::from_value(json!({
        "schema_version":1, "sample_rate":8000,
        "tracks":[{"id":"tone", "device":{"kind":"sine", "frequency_hz":1000.0, "gain":0.4}}]
    }))
    .unwrap();
    let v2: Session = serde_json::from_value(json!({
        "schema_version":2, "sample_rate":8000, "tempo_milli_bpm":120000,
        "tracks":[{"id":"tone", "mode":"continuous", "clips":[],
            "device":{"kind":"sine", "frequency_hz":1000.0, "gain":0.4}}]
    }))
    .unwrap();
    assert_eq!(render(&v1, 32, &[32]).0, render(&v2, 32, &[32]).0);
    let actual = render(&v1, 32, &[32]).0;
    for (frame, sample) in actual.iter().enumerate() {
        let expected = (TAU * 1000.0 * frame as f64 / 8000.0).sin() * 0.4;
        assert!((sample[0] - expected).abs() < 1e-12, "frame {frame}");
        assert_eq!(sample[0], sample[1]);
    }
}
