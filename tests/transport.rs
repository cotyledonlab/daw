use daw::control::{Controller, PROTOCOL_VERSION};
use serde_json::{Value, json};

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

fn session(track_id: &str) -> Value {
    json!({
        "schema_version": 1,
        "sample_rate": 48_000,
        "tracks": [{"id":track_id,"device":{"kind":"sine","frequency_hz":440.0,"gain":0.4}}]
    })
}

#[test]
fn stopped_status_and_stop_are_idempotent() {
    let mut controller = Controller::default();
    for (id, method) in [
        ("status", "transport.status"),
        ("stop-1", "transport.stop"),
        ("stop-2", "transport.stop"),
    ] {
        let response = request(&mut controller, id, method, json!({}));
        assert_eq!(ok(&response)["state"], "stopped");
        assert_eq!(ok(&response)["level"], 0);
        assert_eq!(response["id"], id);
    }
}

#[test]
fn transport_params_are_strict_and_invalid_values_do_not_change_session() {
    let mut controller = Controller::default();
    let original = session("original");
    let replaced = request(
        &mut controller,
        "set",
        "session.replace",
        json!({"session":original}),
    );
    assert_eq!(ok(&replaced), &session("original"));

    for (id, method, params) in [
        (
            "duration-zero",
            "transport.play",
            json!({"seconds":0.0,"volume":0.25}),
        ),
        (
            "duration-long",
            "transport.play",
            json!({"seconds":60.001,"volume":0.25}),
        ),
        (
            "volume-low",
            "transport.play",
            json!({"seconds":1.0,"volume":-0.01}),
        ),
        ("volume-high", "transport.volume", json!({"volume":1.01})),
        ("missing-duration", "transport.play", json!({"volume":0.25})),
        ("extra-status", "transport.status", json!({"extra":true})),
        (
            "extra-volume",
            "transport.volume",
            json!({"volume":0.5,"extra":true}),
        ),
    ] {
        let response = request(&mut controller, id, method, params);
        assert_eq!(error_code(&response), "invalid_params", "{id}: {response}");
        assert_eq!(response["id"], id);
    }

    let after = request(&mut controller, "get", "session.get", json!({}));
    assert_eq!(ok(&after), &session("original"));
}

#[cfg(not(all(feature = "native-audio", target_os = "macos")))]
#[test]
fn playback_controls_report_audio_unavailable_without_native_backend() {
    let mut controller = Controller::default();
    for (id, method, params) in [
        (
            "play",
            "transport.play",
            json!({"seconds":1.0,"volume":0.25}),
        ),
        ("pause", "transport.pause", json!({})),
        ("resume", "transport.resume", json!({})),
        ("volume", "transport.volume", json!({"volume":0.5})),
    ] {
        let response = request(&mut controller, id, method, params);
        assert_eq!(
            error_code(&response),
            "audio_unavailable",
            "{id}: {response}"
        );
    }
}

#[cfg(all(feature = "native-audio", target_os = "macos"))]
#[test]
fn pause_and_resume_while_stopped_return_audio_error_without_opening_a_device() {
    let mut controller = Controller::default();
    for (id, method) in [("pause", "transport.pause"), ("resume", "transport.resume")] {
        let response = request(&mut controller, id, method, json!({}));
        assert_eq!(error_code(&response), "audio_error", "{id}: {response}");
        assert!(
            response["error"]["message"]
                .as_str()
                .unwrap()
                .contains("stopped")
        );
    }
    let status = request(&mut controller, "status", "transport.status", json!({}));
    assert_eq!(ok(&status)["state"], "stopped");
}

#[test]
fn successful_replacement_updates_session_and_failed_replacement_preserves_it() {
    let mut controller = Controller::default();
    let first = session("first");
    assert_eq!(
        ok(&request(
            &mut controller,
            "first",
            "session.replace",
            json!({"session":first})
        ),),
        &session("first")
    );

    let second = session("second");
    assert_eq!(
        ok(&request(
            &mut controller,
            "second",
            "session.replace",
            json!({"session":second})
        ),),
        &session("second")
    );

    let failed = request(
        &mut controller,
        "failed",
        "session.replace",
        json!({
            "session":{"schema_version":9,"sample_rate":48_000,"tracks":[]}
        }),
    );
    assert_eq!(error_code(&failed), "invalid_session");
    let current = request(&mut controller, "current", "session.get", json!({}));
    assert_eq!(ok(&current), &session("second"));
    let stopped = request(&mut controller, "status", "transport.status", json!({}));
    assert_eq!(ok(&stopped)["state"], "stopped");
}
