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

fn success(response: &Value) -> bool {
    response["ok"] == true
}

fn replace(controller: &mut Controller, session: Value) -> Value {
    request(
        controller,
        "replace",
        "session.replace",
        json!({ "session": session }),
    )
}

fn capabilities(controller: &mut Controller) -> Value {
    request(controller, "caps", "capabilities", json!({}))["result"].clone()
}

fn session(sample_rate: u32, tracks: Vec<Value>) -> Value {
    json!({ "schema_version": 1, "sample_rate": sample_rate, "tracks": tracks })
}

fn sine(id: impl Into<String>, frequency_hz: f64, gain: f64) -> Value {
    json!({ "id": id.into(), "device": { "kind": "sine", "frequency_hz": frequency_hz, "gain": gain } })
}

#[test]
fn metadata_is_sufficient_to_construct_a_valid_sine_session() {
    let mut controller = Controller::default();
    let caps = capabilities(&mut controller);
    let kind = caps["devices"][0].as_str().unwrap();
    let parameters = caps["device_metadata"][kind]["parameters"]
        .as_object()
        .unwrap();
    let mut device = serde_json::Map::new();
    device.insert("kind".into(), json!(kind));
    for (name, metadata) in parameters {
        device.insert(name.clone(), metadata["default"].clone());
    }
    let response = replace(
        &mut controller,
        json!({
            "schema_version": caps["session_schema_version"],
            "sample_rate": caps["session"]["sample_rate"]["default"],
            "tracks": [{"id": "metadata-built", "device": device}]
        }),
    );
    assert!(
        success(&response),
        "metadata-built session rejected: {response}"
    );
}

#[test]
fn sample_rate_metadata_matches_validation_boundaries() {
    let mut controller = Controller::default();
    let caps = capabilities(&mut controller);
    let rate = &caps["session"]["sample_rate"];
    let minimum = rate["minimum"].as_u64().unwrap() as u32;
    let maximum = rate["maximum"].as_u64().unwrap() as u32;
    assert_eq!(minimum, 8_000);
    assert_eq!(maximum, 192_000);

    assert!(success(&replace(&mut controller, session(minimum, vec![]))));
    assert!(success(&replace(&mut controller, session(maximum, vec![]))));
    assert!(!success(&replace(
        &mut controller,
        session(minimum - 1, vec![])
    )));
    assert!(!success(&replace(
        &mut controller,
        session(maximum + 1, vec![])
    )));
}

#[test]
fn frequency_zero_and_nyquist_are_rejected_for_multiple_sample_rates() {
    let mut controller = Controller::default();
    let caps = capabilities(&mut controller);
    let frequency = &caps["device_metadata"]["sine"]["parameters"]["frequency_hz"];
    assert_eq!(frequency["maximum_from"]["field"], "session.sample_rate");
    assert_eq!(frequency["maximum_from"]["exclusive"], true);
    let factor = frequency["maximum_from"]["factor"].as_f64().unwrap();
    let minimum = frequency["exclusive_minimum"].as_f64().unwrap();
    for sample_rate in [8_000, 44_100, 192_000] {
        assert!(success(&replace(
            &mut controller,
            session(
                sample_rate,
                vec![sine("inside", sample_rate as f64 / 4.0, 0.2)]
            )
        )));
        for frequency in [minimum, sample_rate as f64 * factor] {
            assert!(
                !success(&replace(
                    &mut controller,
                    session(sample_rate, vec![sine("outside", frequency, 0.2)])
                )),
                "accepted {frequency} Hz at {sample_rate} Hz"
            );
        }
    }
}

#[test]
fn gain_metadata_boundaries_match_validation() {
    let mut controller = Controller::default();
    let caps = capabilities(&mut controller);
    let gain = &caps["device_metadata"]["sine"]["parameters"]["gain"];
    assert_eq!(gain["minimum"], 0.0);
    assert_eq!(gain["maximum"], 1.0);
    for endpoint in [0.0, 1.0] {
        assert!(success(&replace(
            &mut controller,
            session(48_000, vec![sine("gain", 440.0, endpoint)])
        )));
    }
    for outside in [-0.001, 1.001] {
        assert!(!success(&replace(
            &mut controller,
            session(48_000, vec![sine("gain", 440.0, outside)])
        )));
    }
}

#[test]
fn track_count_and_utf8_id_limits_match_metadata() {
    let mut controller = Controller::default();
    let caps = capabilities(&mut controller);
    let tracks = &caps["session"]["tracks"];
    assert_eq!(tracks["min_items"], 0);
    assert_eq!(tracks["max_items"], 64);
    let sixty_four: Vec<_> = (0..64)
        .map(|index| sine(format!("track-{index}"), 440.0, 0.2))
        .collect();
    assert!(success(&replace(
        &mut controller,
        session(48_000, sixty_four)
    )));
    let sixty_five: Vec<_> = (0..65)
        .map(|index| sine(format!("track-{index}"), 440.0, 0.2))
        .collect();
    assert!(!success(&replace(
        &mut controller,
        session(48_000, sixty_five)
    )));

    let id = &caps["session"]["track_id"];
    assert_eq!(id["max_utf8_bytes"], 128);
    assert!(success(&replace(
        &mut controller,
        session(48_000, vec![sine("é".repeat(64), 440.0, 0.2)])
    )));
    assert!(!success(&replace(
        &mut controller,
        session(48_000, vec![sine("é".repeat(65), 440.0, 0.2)])
    )));
}

#[test]
fn duplicate_track_ids_are_rejected() {
    let mut controller = Controller::default();
    assert!(!success(&replace(
        &mut controller,
        session(
            48_000,
            vec![sine("same", 440.0, 0.2), sine("same", 880.0, 0.2)]
        )
    )));
}

#[test]
fn discovered_render_duration_bounds_match_validation() {
    let mut controller = Controller::default();
    let caps = capabilities(&mut controller);
    let min = caps["render"]["min_seconds"].as_f64().unwrap();
    let max = caps["render"]["max_seconds"].as_f64().unwrap();
    assert!(daw::render::validate_duration(min).is_ok());
    assert!(daw::render::validate_duration(max).is_ok());
    assert!(daw::render::validate_duration(min / 2.0).is_err());
    assert!(daw::render::validate_duration(max + 0.001).is_err());
}
