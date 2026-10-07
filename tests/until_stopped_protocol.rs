use daw::control::{Controller, PROTOCOL_VERSION};
use serde_json::{Value, json};

fn call(controller: &mut Controller, method: &str, params: Value) -> Value {
    serde_json::to_value(controller.handle_line(&json!({
        "protocol_version":PROTOCOL_VERSION,"id":"until-stopped-test","method":method,"params":params
    }).to_string())).unwrap()
}
fn result(reply: Value) -> Value {
    assert_eq!(reply["ok"], true, "{reply}");
    reply["result"].clone()
}
fn fixture(controller: &mut Controller) -> (Value, Value) {
    let session = json!({"schema_version":1,"sample_rate":48000,"tracks":[
        {"id":"tone","device":{"kind":"sine","frequency_hz":440,"gain":0.1}}
    ]});
    let before = result(call(controller, "session.inspect", json!({})));
    result(call(
        controller,
        "session.replace",
        json!({"session":session,"expected_revision":before["revision"]}),
    ));
    let inspect = result(call(controller, "session.inspect", json!({})));
    let canonical_session = result(call(controller, "session.get", json!({})));
    (canonical_session, inspect["revision"].clone())
}
fn assert_preserved(controller: &mut Controller, session: &Value, revision: &Value) {
    assert_eq!(result(call(controller, "session.get", json!({}))), *session);
    assert_eq!(
        result(call(controller, "session.inspect", json!({})))["revision"],
        *revision
    );
}

#[test]
fn invalid_play_params_preserve_session_and_revision_without_starting_audio() {
    let mut controller = Controller::default();
    let (session, revision) = fixture(&mut controller);
    for params in [
        json!({"volume":0}),
        json!({"until_stopped":false,"volume":0}),
        json!({"seconds":null,"volume":0}),
        json!({"until_stopped":null,"volume":0}),
        json!({"until_stopped":"true","volume":0}),
        json!({"until_stopped":true,"seconds":1,"volume":0}),
        json!({"until_stopped":true,"seconds":null,"volume":0}),
        json!({"until_stopped":true,"source_mode":"live","volume":0}),
        json!({"until_stopped":true,"volume":-0.1}),
        json!({"until_stopped":true,"volume":1.1}),
        json!({"until_stopped":true,"volume":null}),
        json!({"until_stopped":true,"volume":"0"}),
        json!({"until_stopped":true}),
        json!({"seconds":1,"volume":-0.1}),
        json!({"seconds":61,"volume":0}),
    ] {
        let reply = call(&mut controller, "transport.play", params.clone());
        assert_eq!(reply["ok"], false, "{params}: {reply}");
        assert_eq!(
            reply["error"]["code"], "invalid_params",
            "{params}: {reply}"
        );
        assert_preserved(&mut controller, &session, &revision);
    }
}

#[test]
fn discovery_reports_the_build_specific_indefinite_contract() {
    let mut controller = Controller::default();
    let capabilities = result(call(&mut controller, "capabilities", json!({})));
    assert_eq!(
        capabilities["timeline_transport"]["until_stopped"],
        cfg!(all(feature = "native-audio", target_os = "macos"))
    );
    assert_eq!(
        capabilities["timeline_transport"]["until_stopped_devices"],
        json!(["sine", "synth", "drumkit", "audio", "pd_instrument"])
    );
    assert_eq!(capabilities["timeline_transport"]["native_only"], true);
    for device in ["sine", "synth", "drumkit", "audio", "pd_instrument"] {
        assert!(
            capabilities["devices"]
                .as_array()
                .unwrap()
                .iter()
                .any(|value| value == device)
        );
    }
    assert_eq!(capabilities["render"]["max_seconds"], 180);
    assert_eq!(capabilities["render"]["builtin_max_seconds"], 180);
    assert_eq!(capabilities["render"]["native_max_seconds"], 60);
    assert_eq!(capabilities["render"]["plugin_max_seconds"], 10);
    assert_eq!(capabilities["render"]["runtime_max_seconds"], 60);
    assert_eq!(capabilities["pd_instrument"]["schema_version"], 12);
    assert_eq!(capabilities["pd_instrument"]["sample_rate"], 48000);
    assert_eq!(
        capabilities["pd_instrument"]["native_dsp_owner"],
        "bounded_worker"
    );
    assert_eq!(capabilities["pd_instrument"]["custom_programs"], false);
    assert_eq!(
        capabilities["render"]["song_devices"],
        json!(["sine", "synth", "drumkit", "audio", "pd_instrument"])
    );
    assert_eq!(
        capabilities["render"]["song_effects"],
        json!(["gain", "lowpass", "delay"])
    );
}

#[cfg(not(all(feature = "native-audio", target_os = "macos")))]
#[test]
fn valid_indefinite_request_reports_audio_unavailable_in_portable_build() {
    let mut controller = Controller::default();
    let (session, revision) = fixture(&mut controller);
    for params in [
        json!({"until_stopped":true,"volume":0}),
        json!({"until_stopped":true,"source_mode":"prepared","volume":0}),
    ] {
        let reply = call(&mut controller, "transport.play", params);
        assert_eq!(reply["ok"], false, "{reply}");
        assert_eq!(reply["error"]["code"], "audio_unavailable");
        assert_preserved(&mut controller, &session, &revision);
    }
}

#[test]
fn missing_or_corrupt_audio_asset_load_preserves_session_and_revision() {
    let mut controller = Controller::default();
    let (session, revision) = fixture(&mut controller);
    let directory = tempfile::tempdir().unwrap();
    let project = directory.path().join("audio.json");
    std::fs::write(&project,json!({
        "schema_version":9,"sample_rate":48000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"audio","mode":"sequenced","device":{"kind":"audio","gain":0.5},"effects":[],
            "clips":[{"kind":"audio","id":"c","start_frame":0,"length_frames":10,
                "source_path":"missing.wav","source_offset_frames":0,"gain":1}]}]
    }).to_string()).unwrap();
    for corrupt in [false, true] {
        if corrupt {
            std::fs::write(directory.path().join("missing.wav"), b"invalid WAV bytes").unwrap();
        }
        let reply = call(
            &mut controller,
            "session.load",
            json!({"path":project.to_str().unwrap(),"expected_revision":revision}),
        );
        assert_eq!(reply["ok"], false, "{reply}");
        assert_eq!(reply["error"]["code"], "asset_error", "{reply}");
        assert_preserved(&mut controller, &session, &revision);
    }
}
