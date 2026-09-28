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

const MAX_FRAME: u64 = 9_007_199_254_740_991;

fn inspected(controller: &mut Controller, id: &str) -> Value {
    ok(&request(controller, id, "session.inspect", json!({}))).clone()
}

#[test]
fn seek_and_loop_are_discoverable_in_every_build() {
    let mut controller = Controller::default();
    let capabilities = request(&mut controller, "capabilities", "capabilities", json!({}));
    let methods = ok(&capabilities)["methods"].as_array().unwrap();
    for method in ["transport.seek", "transport.loop"] {
        assert!(methods.iter().any(|value| value == method), "{method}");
    }
}

#[test]
fn seek_and_loop_params_are_strict_and_invalid_values_preserve_session_and_revision() {
    let mut controller = Controller::default();
    ok(&request(
        &mut controller,
        "set",
        "session.replace",
        json!({"session":session("original")}),
    ));
    let before = inspected(&mut controller, "before");

    let invalid = [
        ("seek-missing", "transport.seek", json!({})),
        ("seek-negative", "transport.seek", json!({"frame":-1})),
        ("seek-float", "transport.seek", json!({"frame":1.5})),
        (
            "seek-too-large",
            "transport.seek",
            json!({"frame":MAX_FRAME+1}),
        ),
        (
            "seek-extra",
            "transport.seek",
            json!({"frame":0,"extra":true}),
        ),
        ("loop-missing", "transport.loop", json!({})),
        ("loop-null-param", "transport.loop", json!(null)),
        (
            "loop-region-missing",
            "transport.loop",
            json!({"region":{}}),
        ),
        (
            "loop-start-missing",
            "transport.loop",
            json!({"region":{"end_frame":10}}),
        ),
        (
            "loop-end-missing",
            "transport.loop",
            json!({"region":{"start_frame":0}}),
        ),
        (
            "loop-negative",
            "transport.loop",
            json!({"region":{"start_frame":-1,"end_frame":10}}),
        ),
        (
            "loop-float",
            "transport.loop",
            json!({"region":{"start_frame":0,"end_frame":1.5}}),
        ),
        (
            "loop-empty",
            "transport.loop",
            json!({"region":{"start_frame":4,"end_frame":4}}),
        ),
        (
            "loop-reversed",
            "transport.loop",
            json!({"region":{"start_frame":5,"end_frame":4}}),
        ),
        (
            "loop-too-large",
            "transport.loop",
            json!({"region":{"start_frame":0,"end_frame":MAX_FRAME+1}}),
        ),
        (
            "loop-extra-inner",
            "transport.loop",
            json!({"region":{"start_frame":0,"end_frame":10,"extra":true}}),
        ),
        (
            "loop-extra-outer",
            "transport.loop",
            json!({"region":null,"extra":true}),
        ),
    ];
    for (id, method, params) in invalid {
        let response = request(&mut controller, id, method, params);
        assert_eq!(error_code(&response), "invalid_params", "{id}: {response}");
        assert_eq!(response["id"], id);
    }

    let after = inspected(&mut controller, "after");
    assert_eq!(after, before);
}

#[test]
fn valid_seek_and_loop_controls_are_recognized_while_stopped() {
    let mut controller = Controller::default();
    for (id, method, params) in [
        ("seek-zero", "transport.seek", json!({"frame":0})),
        ("seek-max", "transport.seek", json!({"frame":MAX_FRAME})),
        ("loop-disable", "transport.loop", json!({"region":null})),
        (
            "loop-enable",
            "transport.loop",
            json!({"region":{"start_frame":0,"end_frame":MAX_FRAME}}),
        ),
    ] {
        let response = request(&mut controller, id, method, params);
        let expected = if cfg!(all(feature = "native-audio", target_os = "macos")) {
            "audio_error"
        } else {
            "audio_unavailable"
        };
        assert_eq!(error_code(&response), expected, "{id}: {response}");
    }
    let status = request(&mut controller, "status", "transport.status", json!({}));
    assert_eq!(ok(&status)["state"], "stopped");
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
