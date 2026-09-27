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

fn result(response: &Value) -> &Value {
    assert_eq!(response["ok"], true, "unexpected response: {response}");
    &response["result"]
}

fn error(response: &Value, expected: &str) {
    assert_eq!(response["ok"], false, "expected error: {response}");
    assert_eq!(response["error"]["code"], expected, "{response}");
}

fn inspect(controller: &mut Controller) -> Value {
    request(controller, "inspect", "session.inspect", json!({}))["result"].clone()
}

fn edit(controller: &mut Controller, revision: &str, operations: Value) -> Value {
    request(
        controller,
        "edit",
        "session.edit",
        json!({ "expected_revision": revision, "operations": operations }),
    )
}

fn sine(id: &str, frequency_hz: f64, gain: f64) -> Value {
    json!({ "id": id, "device": { "kind": "sine", "frequency_hz": frequency_hz, "gain": gain } })
}

fn session(tracks: Vec<Value>) -> Value {
    json!({ "schema_version": 1, "sample_rate": 48_000, "tracks": tracks })
}

fn add(track: Value) -> Value {
    json!({ "op": "add_track", "track": track })
}

fn path_str(path: &Path) -> &str {
    path.to_str().expect("temporary paths are UTF-8")
}

#[test]
fn inspection_and_competing_edits_use_monotonic_revisions() {
    let mut controller = Controller::default();
    let first = inspect(&mut controller);
    assert_eq!(first["revision"], "0");
    assert_eq!(first["session"], session(vec![]));

    let accepted = edit(&mut controller, "0", json!([add(sine("one", 440.0, 0.2))]));
    assert_eq!(result(&accepted)["revision"], "1");
    assert_eq!(result(&accepted)["session"]["tracks"][0]["id"], "one");

    let stale = edit(&mut controller, "0", json!([add(sine("two", 880.0, 0.3))]));
    error(&stale, "revision_conflict");
    assert_eq!(inspect(&mut controller)["revision"], "1");
    assert_eq!(
        inspect(&mut controller)["session"]["tracks"]
            .as_array()
            .unwrap()
            .len(),
        1
    );

    let same_value = edit(
        &mut controller,
        "1",
        json!([{ "op": "set_parameter", "track_id": "one", "parameter": "gain", "value": 0.2 }]),
    );
    assert_eq!(result(&same_value)["revision"], "2");

    let missing_file = tempdir().unwrap().path().join("missing.json");
    let stale_replace = request(
        &mut controller,
        "stale-replace",
        "session.replace",
        json!({ "expected_revision": "1", "session": session(vec![]) }),
    );
    error(&stale_replace, "revision_conflict");
    let stale_load = request(
        &mut controller,
        "stale-load",
        "session.load",
        json!({ "expected_revision": "1", "path": path_str(&missing_file) }),
    );
    error(&stale_load, "revision_conflict");
    assert_eq!(inspect(&mut controller)["revision"], "2");
}

#[test]
fn edits_apply_sequentially_and_failed_middle_operation_rolls_back_all() {
    let mut controller = Controller::default();
    let initial = edit(&mut controller, "0", json!([add(sine("old", 440.0, 0.2))]));
    assert_eq!(result(&initial)["revision"], "1");

    let failed = edit(
        &mut controller,
        "1",
        json!([
            { "op": "set_parameter", "track_id": "old", "parameter": "gain", "value": 0.8 },
            { "op": "set_parameter", "track_id": "old", "parameter": "frequency_hz", "value": 24_000.0 },
            { "op": "set_parameter", "track_id": "old", "parameter": "frequency_hz", "value": 440.0 },
            add(sine("later", 880.0, 0.4))
        ]),
    );
    error(&failed, "invalid_session");
    let after_failure = inspect(&mut controller);
    assert_eq!(after_failure["revision"], "1");
    assert_eq!(after_failure["session"], result(&initial)["session"]);

    let sequential = edit(
        &mut controller,
        "1",
        json!([
            add(sine("temporary", 220.0, 0.1)),
            { "op": "set_parameter", "track_id": "temporary", "parameter": "frequency_hz", "value": 330.0 },
            { "op": "remove_track", "track_id": "old" }
        ]),
    );
    assert_eq!(result(&sequential)["revision"], "2");
    assert_eq!(
        result(&sequential)["session"]["tracks"],
        json!([sine("temporary", 330.0, 0.1)])
    );

    let mut boundary = Vec::with_capacity(128);
    boundary.push(json!({ "op": "remove_track", "track_id": "temporary" }));
    for index in 0..63 {
        let id = format!("boundary-{index}");
        boundary.push(add(sine(&id, 440.0, 0.2)));
        boundary.push(json!({ "op": "remove_track", "track_id": id }));
    }
    boundary.push(add(sine("last", 660.0, 0.3)));
    assert_eq!(boundary.len(), 128);
    let accepted_boundary = edit(&mut controller, "2", json!(boundary));
    assert_eq!(result(&accepted_boundary)["revision"], "3");
    assert_eq!(
        result(&accepted_boundary)["session"]["tracks"],
        json!([sine("last", 660.0, 0.3)])
    );
}

#[test]
fn edit_shape_and_revision_tokens_are_strictly_validated() {
    let mut controller = Controller::default();
    let oversized: Vec<_> = (0..129)
        .map(|index| add(sine(&format!("track-{index}"), 440.0, 0.2)))
        .collect();
    for operations in [json!([]), json!(oversized)] {
        let response = edit(&mut controller, "0", operations);
        error(&response, "invalid_params");
        assert_eq!(inspect(&mut controller)["revision"], "0");
    }

    let invalid_operations = [
        json!([{ "op": "unknown" }]),
        json!([{ "op": "add_track", "track": sine("a", 440.0, 0.2), "extra": true }]),
        json!([{ "op": "set_parameter", "track_id": "x", "parameter": "pan", "value": 0.0 }]),
        json!([{ "op": "set_parameter", "track_id": "x", "parameter": "gain", "value": null }]),
        json!([{ "op": "remove_track", "track_id": "x", "extra": true }]),
    ];
    for operations in invalid_operations {
        error(&edit(&mut controller, "0", operations), "invalid_params");
        assert_eq!(inspect(&mut controller)["revision"], "0");
    }

    for expected_revision in [json!(null), json!(0), json!("00"), json!("+0"), json!("")] {
        let response = request(
            &mut controller,
            "bad-revision",
            "session.edit",
            json!({ "expected_revision": expected_revision, "operations": [add(sine("a", 440.0, 0.2))] }),
        );
        error(&response, "invalid_params");
        assert_eq!(inspect(&mut controller)["revision"], "0");
    }
}

#[test]
fn replacement_and_load_preserve_legacy_shapes_and_advance_current_revision() {
    let mut controller = Controller::default();
    let legacy = request(
        &mut controller,
        "legacy-replace",
        "session.replace",
        json!({ "session": session(vec![sine("saved", 440.0, 0.2)]) }),
    );
    assert_eq!(result(&legacy), &session(vec![sine("saved", 440.0, 0.2)]));
    assert_eq!(inspect(&mut controller)["revision"], "1");

    let dir = tempdir().unwrap();
    let file = dir.path().join("session.json");
    let saved = request(
        &mut controller,
        "save",
        "session.save",
        json!({ "path": path_str(&file) }),
    );
    assert!(saved["ok"] == true);
    let persisted: Value = serde_json::from_slice(&fs::read(&file).unwrap()).unwrap();
    assert!(persisted.get("revision").is_none());
    assert_eq!(inspect(&mut controller)["revision"], "1");

    let guarded = request(
        &mut controller,
        "guarded-replace",
        "session.replace",
        json!({ "expected_revision": "1", "session": session(vec![sine("new", 880.0, 0.4)]) }),
    );
    assert_eq!(result(&guarded), &session(vec![sine("new", 880.0, 0.4)]));
    assert_eq!(inspect(&mut controller)["revision"], "2");

    let loaded = request(
        &mut controller,
        "load",
        "session.load",
        json!({ "expected_revision": "2", "path": path_str(&file) }),
    );
    assert_eq!(result(&loaded), &session(vec![sine("saved", 440.0, 0.2)]));
    assert_eq!(inspect(&mut controller)["revision"], "3");

    for (method, params) in [
        (
            "session.replace",
            json!({ "expected_revision": null, "session": session(vec![]) }),
        ),
        (
            "session.load",
            json!({ "expected_revision": null, "path": path_str(&file) }),
        ),
    ] {
        error(
            &request(&mut controller, "null-guard", method, params),
            "invalid_params",
        );
        assert_eq!(inspect(&mut controller)["revision"], "3");
    }
}

#[test]
fn failed_replacement_and_load_and_non_edit_commands_do_not_advance_revision() {
    let mut controller = Controller::default();
    let dir = tempdir().unwrap();
    let bad_file = dir.path().join("bad.json");
    fs::write(&bad_file, b"not json").unwrap();

    error(
        &request(
            &mut controller,
            "bad-replace",
            "session.replace",
            json!({ "expected_revision": "0", "session": { "schema_version": 77, "sample_rate": 48_000, "tracks": [] } }),
        ),
        "invalid_session",
    );
    error(
        &request(
            &mut controller,
            "bad-load",
            "session.load",
            json!({ "expected_revision": "0", "path": path_str(&bad_file) }),
        ),
        "invalid_session",
    );
    assert_eq!(inspect(&mut controller)["revision"], "0");

    let saved = dir.path().join("saved.json");
    let wav = dir.path().join("silent.wav");
    assert!(
        request(
            &mut controller,
            "save",
            "session.save",
            json!({ "path": path_str(&saved) })
        )["ok"]
            == true
    );
    assert!(
        request(
            &mut controller,
            "render",
            "render",
            json!({ "path": path_str(&wav), "seconds": 0.01 })
        )["ok"]
            == true
    );
    assert_eq!(inspect(&mut controller)["revision"], "0");
}
