use daw::control::{Controller, PROTOCOL_VERSION};
use serde_json::{Value, json};
use std::{fs, path::Path};
use tempfile::tempdir;

fn request(controller: &mut Controller, id: &str, method: &str, params: Value) -> Value {
    let line = json!({
        "protocol_version": PROTOCOL_VERSION,
        "id": id,
        "method": method,
        "params": params,
    })
    .to_string();
    serde_json::to_value(controller.handle_line(&line)).unwrap()
}

fn ok(response: &Value) -> &Value {
    assert_eq!(response["ok"], true, "unexpected response: {response}");
    response
        .get("result")
        .expect("successful response has result")
}

fn error_code(response: &Value) -> &str {
    assert_eq!(response["ok"], false, "expected an error: {response}");
    response["error"]["code"].as_str().expect("error has code")
}

fn session_with(track_id: &str, frequency_hz: f64, gain: f64) -> Value {
    json!({
        "schema_version": 1,
        "sample_rate": 48_000,
        "tracks": [{
            "id": track_id,
            "device": { "kind": "sine", "frequency_hz": frequency_hz, "gain": gain }
        }]
    })
}

fn replace(controller: &mut Controller, id: &str, session: Value) -> Value {
    request(
        controller,
        id,
        "session.replace",
        json!({ "session": session }),
    )
}

fn assert_session(controller: &mut Controller, expected: &Value) {
    let response = request(controller, "check-session", "session.get", json!({}));
    assert_eq!(ok(&response), expected);
}

fn path_str(path: &Path) -> &str {
    path.to_str().expect("temporary paths are UTF-8")
}

#[test]
fn capabilities_report_the_implemented_build_surface() {
    let mut controller = Controller::default();
    let response = request(&mut controller, "caps", "capabilities", json!({}));
    let capabilities = ok(&response);

    assert_eq!(
        capabilities["live_audio"],
        cfg!(all(feature = "native-audio", target_os = "macos"))
    );
    assert_eq!(
        capabilities["plugin_hosting"],
        cfg!(all(feature = "vst3-live", target_os = "macos"))
    );
    assert_eq!(capabilities["render"]["format"], "wav_pcm16");
    assert_eq!(capabilities["render"]["channels"], 2);
    assert_eq!(capabilities["render"]["max_seconds"], 60);
    assert!(
        capabilities["methods"]
            .as_array()
            .unwrap()
            .iter()
            .any(|m| m == "render")
    );
    assert_eq!(response["id"], "caps");
}

#[test]
fn invalid_replace_and_load_preserve_the_last_valid_session() {
    let mut controller = Controller::default();
    let valid = session_with("main", 440.0, 0.5);
    assert!(replace(&mut controller, "set-valid", valid.clone())["ok"] == true);

    let invalid = replace(
        &mut controller,
        "bad-replace",
        json!({
            "schema_version": 77,
            "sample_rate": 48_000,
            "tracks": []
        }),
    );
    assert_eq!(error_code(&invalid), "invalid_session");
    assert_eq!(invalid["id"], "bad-replace");
    assert_session(&mut controller, &valid);

    let dir = tempdir().unwrap();
    let bad_file = dir.path().join("invalid-session.json");
    fs::write(
        &bad_file,
        r#"{"schema_version":1,"sample_rate":48000,"tracks":[],"extra":true}"#,
    )
    .unwrap();
    let load = request(
        &mut controller,
        "bad-load",
        "session.load",
        json!({ "path": path_str(&bad_file) }),
    );
    assert_eq!(error_code(&load), "invalid_session");
    assert_eq!(load["id"], "bad-load");
    assert_session(&mut controller, &valid);
}

#[test]
fn session_validation_rejects_duplicate_ids_schema_mismatch_and_unknown_fields() {
    let mut controller = Controller::default();
    let duplicate_ids = json!({
        "schema_version": 1,
        "sample_rate": 48_000,
        "tracks": [
            { "id": "same", "device": { "kind": "sine", "frequency_hz": 440.0, "gain": 0.2 } },
            { "id": "same", "device": { "kind": "sine", "frequency_hz": 880.0, "gain": 0.2 } }
        ]
    });
    let duplicate = replace(&mut controller, "duplicate", duplicate_ids);
    assert_eq!(error_code(&duplicate), "invalid_session");
    assert_eq!(duplicate["id"], "duplicate");

    let wrong_schema = replace(
        &mut controller,
        "schema",
        json!({
            "schema_version": 2,
            "sample_rate": 48_000,
            "tracks": []
        }),
    );
    assert_eq!(error_code(&wrong_schema), "invalid_session");

    let unknown_session_field = replace(
        &mut controller,
        "unknown-session",
        json!({
            "schema_version": 1,
            "sample_rate": 48_000,
            "tracks": [],
            "future_magic": true
        }),
    );
    assert_eq!(error_code(&unknown_session_field), "invalid_params");

    let unknown_params_field = request(
        &mut controller,
        "unknown-params",
        "session.get",
        json!({ "ignored": true }),
    );
    assert_eq!(error_code(&unknown_params_field), "invalid_params");
}

#[test]
fn save_load_round_trip_restores_the_saved_session() {
    let mut controller = Controller::default();
    let original = session_with("original", 330.0, 0.4);
    assert_eq!(
        ok(&replace(&mut controller, "original", original.clone())),
        &original
    );

    let dir = tempdir().unwrap();
    let file = dir.path().join("session.json");
    let saved = request(
        &mut controller,
        "save",
        "session.save",
        json!({ "path": path_str(&file) }),
    );
    assert_eq!(ok(&saved)["path"], path_str(&file));
    assert!(file.is_file());

    let changed = session_with("changed", 660.0, 0.8);
    assert_eq!(
        ok(&replace(&mut controller, "changed", changed.clone())),
        &changed
    );

    let loaded = request(
        &mut controller,
        "load",
        "session.load",
        json!({ "path": path_str(&file) }),
    );
    assert_eq!(ok(&loaded), &original);
    assert_session(&mut controller, &original);
}

#[test]
fn save_and_render_refuse_to_overwrite_existing_files() {
    let mut controller = Controller::default();
    let dir = tempdir().unwrap();
    let sentinel = dir.path().join("sentinel.json");
    fs::write(&sentinel, b"keep this save sentinel").unwrap();
    let save = request(
        &mut controller,
        "save-existing",
        "session.save",
        json!({ "path": path_str(&sentinel) }),
    );
    assert_eq!(error_code(&save), "io_error");
    assert_eq!(fs::read(&sentinel).unwrap(), b"keep this save sentinel");

    let wav = dir.path().join("sentinel.wav");
    fs::write(&wav, b"keep this render sentinel").unwrap();
    let render = request(
        &mut controller,
        "render-existing",
        "render",
        json!({ "path": path_str(&wav), "seconds": 0.1 }),
    );
    assert_eq!(error_code(&render), "io_error");
    assert_eq!(fs::read(&wav).unwrap(), b"keep this render sentinel");
}

#[test]
fn invalid_render_duration_creates_no_output() {
    let mut controller = Controller::default();
    let dir = tempdir().unwrap();
    let output = dir.path().join("invalid.wav");
    let response = request(
        &mut controller,
        "invalid-duration",
        "render",
        json!({ "path": path_str(&output), "seconds": 0.0 }),
    );

    assert_eq!(error_code(&response), "invalid_params");
    assert_eq!(response["id"], "invalid-duration");
    assert!(!output.exists());
}

#[test]
fn render_reports_stereo_frames_and_bounded_nonzero_samples() {
    let mut controller = Controller::default();
    let session = session_with("tone", 440.0, 0.35);
    assert!(replace(&mut controller, "tone", session)["ok"] == true);

    let dir = tempdir().unwrap();
    let output = dir.path().join("tone.wav");
    let response = request(
        &mut controller,
        "render-tone",
        "render",
        json!({ "path": path_str(&output), "seconds": 0.1 }),
    );
    let report = ok(&response);
    assert_eq!(report["frames"], 4_800);
    assert_eq!(report["sample_rate"], 48_000);
    assert_eq!(report["channels"], 2);
    assert_eq!(report["clipped_frames"], 0);

    let mut reader = hound::WavReader::open(&output).unwrap();
    let spec = reader.spec();
    assert_eq!(spec.channels, 2);
    assert_eq!(spec.sample_rate, 48_000);
    assert_eq!(spec.bits_per_sample, 16);
    let samples: Vec<i16> = reader.samples::<i16>().map(Result::unwrap).collect();
    assert_eq!(samples.len(), 4_800 * 2);
    assert!(samples.iter().any(|&sample| sample != 0));
    let max_amplitude = samples
        .iter()
        .map(|&sample| sample.unsigned_abs())
        .max()
        .unwrap();
    assert!(max_amplitude as f64 / i16::MAX as f64 <= 0.35 + 1.0 / i16::MAX as f64);
    assert!(samples.chunks_exact(2).all(|frame| frame[0] == frame[1]));
}

#[test]
fn renderer_reports_clipping_and_empty_session_is_silent() {
    let mut controller = Controller::default();
    let clipping_session = json!({
        "schema_version": 1,
        "sample_rate": 48_000,
        "tracks": [
            { "id": "one", "device": { "kind": "sine", "frequency_hz": 440.0, "gain": 1.0 } },
            { "id": "two", "device": { "kind": "sine", "frequency_hz": 440.0, "gain": 1.0 } }
        ]
    });
    assert!(replace(&mut controller, "clip-session", clipping_session)["ok"] == true);

    let dir = tempdir().unwrap();
    let clipped_path = dir.path().join("clipped.wav");
    let clipped = request(
        &mut controller,
        "clipped",
        "render",
        json!({ "path": path_str(&clipped_path), "seconds": 0.1 }),
    );
    let report = ok(&clipped);
    assert_eq!(report["frames"], 4_800);
    assert!(report["clipped_frames"].as_u64().unwrap() > 0);
    let clipped_samples: Vec<i16> = hound::WavReader::open(&clipped_path)
        .unwrap()
        .samples::<i16>()
        .map(Result::unwrap)
        .collect();
    assert!(clipped_samples.contains(&i16::MAX));

    assert!(
        replace(
            &mut controller,
            "empty-session",
            json!({
                "schema_version": 1,
                "sample_rate": 48_000,
                "tracks": []
            })
        )["ok"]
            == true
    );
    let silence_path = dir.path().join("silence.wav");
    let silence = request(
        &mut controller,
        "silence",
        "render",
        json!({ "path": path_str(&silence_path), "seconds": 0.01 }),
    );
    assert_eq!(ok(&silence)["frames"], 480);
    let silence_samples: Vec<i16> = hound::WavReader::open(&silence_path)
        .unwrap()
        .samples::<i16>()
        .map(Result::unwrap)
        .collect();
    assert_eq!(silence_samples.len(), 480 * 2);
    assert!(silence_samples.iter().all(|&sample| sample == 0));
}

#[test]
fn command_errors_keep_stable_codes_and_echo_request_ids() {
    let mut controller = Controller::default();
    let first = request(&mut controller, "err-1", "not.real", json!({}));
    let second = request(&mut controller, "err-2", "not.real", json!({}));

    assert_eq!(error_code(&first), "unknown_method");
    assert_eq!(error_code(&second), "unknown_method");
    assert_eq!(first["error"]["message"], second["error"]["message"]);
    assert_eq!(first["id"], "err-1");
    assert_eq!(second["id"], "err-2");
}
