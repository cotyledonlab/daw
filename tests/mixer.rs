use daw::{audio_buffer::PlaybackBuffer, engine::Engine, session::Session};
use serde_json::{Value, json};

fn track(id: &str, gain: f64, pan: f64, mute: bool, solo: bool) -> Value {
    json!({"id":id,"mode":"continuous","clips":[],"effects":[{"kind":"gain","id":"fx","gain":2.0,"bypass":false}],"device":{"kind":"sine","frequency_hz":1000.0,"gain":0.25},"mixer":{"gain":gain,"pan":pan,"mute":mute,"solo":solo}})
}
fn session(tracks: Vec<Value>) -> Session {
    serde_json::from_value(
        json!({"schema_version":10,"sample_rate":8000,"tempo_milli_bpm":120000,"tracks":tracks}),
    )
    .unwrap()
}
fn samples(session: &Session) -> Vec<[f64; 2]> {
    let mut engine = Engine::prepare(session).unwrap();
    let mut output = vec![[0.0; 2]; 32];
    engine.render_block(&mut output);
    output
}

#[test]
fn gain_balance_and_effect_order_are_exact() {
    let centered = samples(&session(vec![track("a", 1.0, 0.0, false, false)]));
    assert!((centered[2][0] - 0.5).abs() < 1e-12);
    for pan in [-1.0, -0.5, 0.0, 0.5, 1.0] {
        let output = samples(&session(vec![track("a", 0.5, pan, false, false)]));
        let expected = if pan.abs() == 1.0 {
            0.0
        } else {
            (pan.abs() * std::f64::consts::FRAC_PI_2).cos()
        };
        assert!((output[2][if pan > 0.0 { 0 } else { 1 }] - 0.25 * expected).abs() < 1e-12);
        assert!((output[2][if pan > 0.0 { 1 } else { 0 }] - 0.25).abs() < 1e-12);
    }
}

#[test]
fn inclusive_solo_and_mute_override() {
    let output = samples(&session(vec![
        track("a", 0.5, -1.0, false, true),
        track("b", 0.5, 1.0, false, true),
        track("c", 2.0, 0.0, false, false),
        track("d", 2.0, 0.0, true, true),
    ]));
    assert!((output[2][0] - 0.25).abs() < 1e-12);
    assert!((output[2][1] - 0.25).abs() < 1e-12);
    assert!(
        samples(&session(vec![track("a", 1.0, 0.0, true, false)]))
            .iter()
            .all(|f| *f == [0.0; 2])
    );
}

#[test]
fn step_effect_automation_multiplies_independent_saved_mixer_gain() {
    let mut value = track("a", 0.4, 0.0, false, false);
    value["automation"] = json!([{"effect_id":"fx","parameter":"gain","interpolation":"step","points":[{"frame":0,"value":0.5},{"frame":8,"value":1.5}]}]);
    let original = session(vec![value]);
    let output = samples(&original);
    assert!((output[2][0] - 0.05).abs() < 1e-12);
    assert!((output[10][0] - 0.15).abs() < 1e-12);
    let saved = serde_json::to_value(&original).unwrap();
    assert_eq!(saved["tracks"][0]["mixer"]["gain"], json!(0.4));
    assert_eq!(saved["tracks"][0]["effects"][0]["gain"], json!(2.0));
    assert_eq!(
        saved["tracks"][0]["automation"][0]["points"][1]["value"],
        json!(1.5)
    );
}

#[test]
fn saved_settings_match_export_and_callback_and_meter_preclamp() {
    let original = session(vec![
        track("a", 2.0, -1.0, false, true),
        track("b", 2.0, -1.0, false, true),
    ]);
    let saved = serde_json::to_string(&original).unwrap();
    let loaded: Session = serde_json::from_str(&saved).unwrap();
    assert_eq!(loaded, original);
    let mut playback = PlaybackBuffer::prepare(&loaded, 8000, 2, 0.08, 0.1).unwrap();
    let mut output = vec![0.0; 1280];
    playback.fill(&mut output, |x| x).unwrap();
    let meters = playback.mixer_peaks().unwrap();
    assert_eq!(meters.master, [2.0, 0.0]);
    assert_eq!(meters.tracks[0], [1.0, 0.0]);
    assert_eq!(output[4], 0.1);
    let mut wav = std::io::Cursor::new(Vec::new());
    daw::render::render(&loaded, 0.08, &mut wav).unwrap();
    wav.set_position(0);
    let decoded: Vec<_> = hound::WavReader::new(wav)
        .unwrap()
        .samples::<i16>()
        .map(Result::unwrap)
        .collect();
    for (actual, callback) in decoded.iter().zip(&output) {
        assert_eq!(
            *actual,
            (callback / 0.1 * f64::from(i16::MAX)).round() as i16
        );
    }
}

#[test]
fn schema_scope_defaults_and_strict_mixer_validation() {
    let mut value =
        serde_json::to_value(session(vec![track("a", 1.0, 0.0, false, false)])).unwrap();
    for schema in 1..10 {
        value["schema_version"] = json!(schema);
        assert!(
            serde_json::from_value::<Session>(value.clone())
                .unwrap()
                .validate()
                .is_err()
        );
    }
    value["schema_version"] = json!(10);
    for (field, invalid) in [
        ("gain", json!(-0.1)),
        ("gain", json!(2.1)),
        ("pan", json!(-1.1)),
        ("pan", json!(1.1)),
    ] {
        let mut candidate = value.clone();
        candidate["tracks"][0]["mixer"][field] = invalid;
        assert!(
            serde_json::from_value::<Session>(candidate)
                .unwrap()
                .validate()
                .is_err()
        );
    }
    for invalid in [
        Value::Null,
        json!({"gain":1.0}),
        json!({"gain":1,"pan":0,"mute":false,"solo":"yes"}),
        json!({"gain":1,"pan":0,"mute":false,"solo":false,"extra":0}),
    ] {
        let mut candidate = value.clone();
        candidate["tracks"][0]["mixer"] = invalid;
        assert!(serde_json::from_value::<Session>(candidate).is_err());
    }
    value["tracks"][0].as_object_mut().unwrap().remove("mixer");
    assert_eq!(
        samples(&serde_json::from_value(value.clone()).unwrap()),
        samples(&session(vec![track("a", 1.0, 0.0, false, false)]))
    );
    value["tracks"][0]["device"] = json!({"kind":"supercollider","synthdef_hex":"00","synth_name":"x","duration_frames":800,"gain":1,"controls":[]});
    assert!(
        serde_json::from_value::<Session>(value)
            .unwrap()
            .validate()
            .unwrap_err()
            .contains("gain effects only")
    );
}

#[test]
fn balance_preserves_stereo_audio_channels() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("stereo.wav");
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
    for _ in 0..80 {
        writer.write_sample(8192_i16).unwrap();
        writer.write_sample(-16384_i16).unwrap();
    }
    writer.finalize().unwrap();
    let mut value = track("audio", 1.0, 0.5, false, false);
    value["mode"] = json!("sequenced");
    value["device"] = json!({"kind":"audio","gain":1.0});
    value["effects"] = json!([]);
    value["clips"] = json!([{"kind":"audio","id":"clip","start_frame":0,"length_frames":80,"source_path":"stereo.wav","source_offset_frames":0,"gain":1.0}]);
    let mut audio_session = session(vec![value]);
    audio_session.asset_root = Some(directory.path().to_path_buf());
    let output = samples(&audio_session);
    assert!((output[0][0] - 0.25 * std::f64::consts::FRAC_1_SQRT_2).abs() < 1e-9);
    assert_eq!(output[0][1], -0.5);
    let mut playback = PlaybackBuffer::prepare_until_stopped(&audio_session, 8000, 2, 0.5).unwrap();
    let mut callback = [0.0; 1200];
    playback.fill(&mut callback, |x| x).unwrap();
    // The active audio is entirely in the first 256-frame chunk. The later
    // chunks are silent, but must not erase this callback's earlier peaks.
    assert!((playback.mixer_peaks().unwrap().master[0] - output[0][0]).abs() < 1e-12);
    assert_eq!(playback.mixer_peaks().unwrap().master[1], 0.5);
    playback.fill(&mut callback, |x| x).unwrap();
    assert_eq!(playback.mixer_peaks().unwrap().master, [0.0; 2]);
    assert_eq!(playback.mixer_peaks().unwrap().tracks[0], [0.0; 2]);
}
