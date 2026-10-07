use daw::{audio_buffer::PlaybackBuffer, engine::Engine, render, session::Session};
use serde_json::{Value, json};
use std::{f64::consts::TAU, io::Cursor};

fn project(effects: Value) -> Session {
    serde_json::from_value(json!({
        "schema_version":11,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"tone","mode":"continuous","clips":[],
        "device":{"kind":"sine","frequency_hz":1000,"gain":0.25},"effects":effects,
        "mixer":{"gain":0.75,"pan":0.2,"mute":false,"solo":false}}]
    }))
    .unwrap()
}
fn lowpass(cutoff: f64, bypass: bool) -> Value {
    json!({"kind":"lowpass","id":"filter","cutoff_hz":cutoff,"bypass":bypass})
}
fn delay(time: f64, feedback: f64, mix: f64, bypass: bool) -> Value {
    json!({"kind":"delay","id":"echo","time_ms":time,"feedback":feedback,"mix":mix,"bypass":bypass})
}
fn samples(session: &Session, frames: usize) -> Vec<[f64; 2]> {
    let mut engine = Engine::prepare(session).unwrap();
    let mut output = vec![[0.0; 2]; frames];
    engine.render_block(&mut output);
    output
}
fn impulse(effects: Value) -> (tempfile::TempDir, Session) {
    let directory = tempfile::tempdir().unwrap();
    let mut wav = hound::WavWriter::create(
        directory.path().join("impulse.wav"),
        hound::WavSpec {
            channels: 2,
            sample_rate: 8000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    wav.write_sample(16384_i16).unwrap();
    wav.write_sample(-8192_i16).unwrap();
    wav.finalize().unwrap();
    let mut session = project(effects);
    let track = &mut session.tracks[0];
    track.device = daw::session::Device::Audio { gain: 1.0 };
    track.mode = Some(daw::session::TrackMode::Sequenced);
    track.mixer = None;
    track.clips = Some(
        serde_json::from_value(json!([{"kind":"audio","id":"impulse","start_frame":0,
        "length_frames":1,"source_path":"impulse.wav","source_offset_frames":0,"gain":1.0}]))
        .unwrap(),
    );
    session.asset_root = Some(directory.path().to_path_buf());
    (directory, session)
}

#[test]
fn lowpass_impulse_is_one_pole_and_preserves_stereo_and_tails() {
    let (_directory, session) = impulse(json!([lowpass(200.0, false)]));
    let output = samples(&session, 64);
    let alpha = 1.0 - (-TAU * 200.0 / 8000.0).exp();
    for (frame, sample) in output.iter().enumerate() {
        let decay = alpha * (1.0 - alpha).powi(frame as i32);
        assert!((sample[0] - 0.5 * decay).abs() < 1e-14);
        assert!((sample[1] + 0.25 * decay).abs() < 1e-14);
    }
    let wet = samples(&project(json!([lowpass(80.0, false)])), 512);
    let dry = samples(&project(json!([])), 512);
    let energy = |values: &[[f64; 2]]| values[128..].iter().map(|s| s[0] * s[0]).sum::<f64>();
    assert!(energy(&wet) < energy(&dry) * 0.01);
}

#[test]
fn delay_impulse_has_exact_rounded_spacing_feedback_and_dry_wet_mix() {
    let (_directory, session) = impulse(json!([delay(1.06, 0.5, 0.25, false)]));
    let output = samples(&session, 33);
    assert_eq!(output[0], [0.375, -0.1875]);
    for (frame, sample) in output.iter().enumerate().skip(1) {
        let expected = if frame % 8 == 0 {
            0.125 * 0.5_f64.powi((frame / 8 - 1) as i32)
        } else {
            0.0
        };
        assert_eq!(*sample, [expected, -expected / 2.0], "frame {frame}");
    }
    let mut zero_feedback = session.clone();
    zero_feedback.tracks[0].effects =
        Some(serde_json::from_value(json!([delay(1.0, 0.0, 1.0, false)])).unwrap());
    let output = samples(&zero_feedback, 25);
    assert_eq!(output[8], [0.5, -0.25]);
    assert!(
        output
            .iter()
            .enumerate()
            .all(|(i, s)| i == 8 || *s == [0.0; 2])
    );
}

#[test]
fn bypass_block_boundaries_seek_and_loop_have_defined_deterministic_state() {
    let dry = project(json!([]));
    let bypassed = project(json!([lowpass(50.0, true), delay(1.0, 0.95, 1.0, true)]));
    assert_eq!(samples(&dry, 257), samples(&bypassed, 257));
    let session = project(json!([lowpass(200.0, false), delay(1.0, 0.6, 0.5, false)]));
    let expected = samples(&session, 257);
    let mut engine = Engine::prepare(&session).unwrap();
    let mut split = vec![[0.0; 2]; 257];
    let mut offset = 0;
    for count in [1, 7, 39, 2, 104, 104] {
        engine.render_block(&mut split[offset..offset + count]);
        offset += count;
    }
    assert_eq!(expected, split);
    engine.seek(0).unwrap();
    engine.render_block(&mut split);
    assert_eq!(expected, split);
    let mut fresh = Engine::prepare(&session).unwrap();
    fresh.seek(32).unwrap();
    engine.seek(32).unwrap();
    let mut a = [[0.0; 2]; 16];
    let mut b = a;
    fresh.render_block(&mut a);
    engine.render_block(&mut b);
    assert_eq!(a, b); // no pre-seek filter/delay history
    engine.set_loop(Some((32, 48))).unwrap();
    engine.seek(32).unwrap();
    let mut repeated = [[0.0; 2]; 48];
    engine.render_block(&mut repeated);
    assert_eq!(&repeated[..16], &repeated[16..32]);
    assert_eq!(&repeated[..16], &repeated[32..]);
}

#[test]
fn native_export_and_reopened_session_match_with_gain_automation_and_mixer() {
    let mut session = project(json!([
        {"kind":"gain","id":"trim","gain":0.5,"bypass":false},
        lowpass(400.0,false),delay(2.0,0.5,0.4,false)]));
    session.tracks[0].automation = Some(serde_json::from_value(json!([
        {"effect_id":"trim","parameter":"gain","interpolation":"step","points":[{"frame":128,"value":0.75}]}])).unwrap());
    let reopened: Session =
        serde_json::from_value(serde_json::to_value(&session).unwrap()).unwrap();
    assert_eq!(reopened, session);
    let expected = samples(&reopened, 400);
    let mut native = PlaybackBuffer::prepare_until_stopped(&reopened, 8000, 2, 1.0).unwrap();
    let mut output = [0.0; 800];
    native.fill(&mut output, |s| s).unwrap();
    assert_eq!(
        output.as_slice(),
        expected.iter().flatten().copied().collect::<Vec<_>>()
    );
    let mut encoded = Cursor::new(Vec::new());
    let report = render::render(&reopened, 0.05, &mut encoded).unwrap();
    assert_eq!(report.frames, 400);
    let wav = hound::WavReader::new(Cursor::new(encoded.into_inner())).unwrap();
    let actual: Vec<_> = wav.into_samples::<i16>().map(Result::unwrap).collect();
    let expected: Vec<_> = output
        .iter()
        .map(|s| (s.clamp(-1.0, 1.0) * i16::MAX as f64).round() as i16)
        .collect();
    assert_eq!(actual, expected);
    assert_eq!(render::max_render_seconds(&reopened), 180.0);
    assert!(render::validate_render_duration(&reopened, 181.0).is_err());
}

#[test]
fn schema_and_aggregate_state_limits_reject_before_preparation() {
    let valid = project(json!([
        lowpass(3999.0, false),
        delay(2000.0, 0.95, 1.0, false)
    ]));
    valid.validate().unwrap();
    for version in 1..=10 {
        let mut old = valid.clone();
        old.schema_version = version;
        assert!(old.validate().is_err(), "schema {version}");
    }
    for effects in [
        json!([lowpass(4000.0, false)]),
        json!([lowpass(19.9, false)]),
        json!([delay(0.9, 0.5, 0.5, false)]),
        json!([delay(2000.1, 0.5, 0.5, false)]),
        json!([delay(1.0, 0.951, 0.5, false)]),
        json!([delay(1.0, 0.5, 1.01, false)]),
    ] {
        assert!(Engine::prepare(&project(effects)).is_err());
    }
    let mut missing = serde_json::to_value(&valid).unwrap();
    missing["tracks"][0]
        .as_object_mut()
        .unwrap()
        .remove("effects");
    assert!(
        serde_json::from_value::<Session>(missing)
            .unwrap()
            .validate()
            .is_err()
    );
    let mut foreign = valid.clone();
    foreign.tracks[0]
        .effects
        .as_mut()
        .unwrap()
        .push(daw::session::Effect::Vst3 {
            id: "foreign".into(),
            bypass: true,
            bundle_path: "/plugin.vst3".into(),
            class_id: "0".repeat(32),
            state_hex: String::new(),
            controller_state_hex: String::new(),
            parameters: vec![],
        });
    assert!(foreign.validate().is_err());
    let mut huge = valid.clone();
    huge.sample_rate = 192000;
    huge.tracks[0].effects = Some(
        (0..16)
            .map(|i| daw::session::Effect::Delay {
                id: format!("echo{i}"),
                time_ms: 2000.0,
                feedback: 0.95,
                mix: 0.5,
                bypass: false,
            })
            .collect(),
    );
    assert!(
        huge.validate()
            .unwrap_err()
            .contains("aggregate delay state")
    );
}

#[test]
fn delay_reset_does_not_read_old_ring_content_before_refill() {
    let (_directory, session) = impulse(json!([delay(1.0, 0.95, 1.0, false)]));
    let mut engine = Engine::prepare(&session).unwrap();
    let mut before = [[0.0; 2]; 25];
    engine.render_block(&mut before);
    assert!(before.iter().any(|s| s[0] > 0.0)); // populated feedback ring
    engine.seek(1).unwrap();
    let mut after = [[0.0; 2]; 33];
    engine.render_block(&mut after);
    assert!(after.iter().all(|s| *s == [0.0; 2])); // includes two complete refill cycles
    engine.seek(0).unwrap();
    engine.render_block(&mut before);
    assert_eq!(before, samples(&session, 25).as_slice());
    engine.set_loop(Some((0, 1))).unwrap();
    engine.seek(0).unwrap();
    let mut tiny_loop = [[0.0; 2]; 1024];
    engine.render_block(&mut tiny_loop);
    assert!(tiny_loop.iter().all(|s| *s == [0.0; 2])); // no delay history crosses the loop
}
