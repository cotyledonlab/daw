use daw::session::{
    MAX_VST3_PLUGINS, MAX_VST3_SESSION_STATE_BYTES, MAX_VST3_STATE_BYTES, SCHEMA_VERSION_5, Session,
};
use serde_json::{Value, json};

fn au(id: &str) -> Value {
    json!({
        "kind":"au", "id":id, "bypass":false,
        "component_type":"aufx", "component_subtype":"lpas",
        "component_manufacturer":"appl", "state_hex":"aB00",
        "parameters":[{"id":0,"value":0.5}]
    })
}

fn vst3(id: &str) -> Value {
    json!({
        "kind":"vst3", "id":id, "bypass":false,
        "bundle_path":"/Library/Audio/Plug-Ins/VST3/Example.vst3",
        "class_id":"0123456789ABCDEF0123456789ABCDEF",
        "state_hex":"", "controller_state_hex":"",
        "parameters":[]
    })
}

fn session(version: u32, effects: Value) -> Value {
    json!({
        "schema_version":version, "sample_rate":48000, "tempo_milli_bpm":120000,
        "tracks":[{
            "id":"tone", "device":{"kind":"sine","frequency_hz":440.0,"gain":0.5},
            "mode":"continuous", "clips":[], "effects":effects
        }]
    })
}

fn validate(value: Value) -> Result<Session, String> {
    let parsed: Session = serde_json::from_value(value).map_err(|error| error.to_string())?;
    parsed.validate()?;
    Ok(parsed)
}

#[test]
fn schema5_au_round_trips_and_prior_schemas_reject_it() {
    let input = session(5, json!([au("filter")]));
    let parsed = validate(input.clone()).unwrap();
    assert_eq!(parsed.schema_version, SCHEMA_VERSION_5);
    assert_eq!(serde_json::to_value(parsed).unwrap(), input);
    for version in 1..=4 {
        assert!(validate(session(version, json!([au("filter")]))).is_err());
    }
}

#[test]
fn au_requires_v5_effects_and_48khz() {
    assert!(validate(session(5, Value::Null)).is_err());
    assert!(validate(session(5, json!([]))).is_ok());
    let mut wrong_rate = session(5, json!([au("filter")]));
    wrong_rate["sample_rate"] = json!(44100);
    assert!(validate(wrong_rate).is_err());
}

#[test]
fn au_shape_identity_and_parameter_contract_are_strict() {
    let mut cases = Vec::new();
    let mut unknown = session(5, json!([au("x")]));
    unknown["tracks"][0]["effects"][0]["extra"] = json!(true);
    cases.push(unknown);
    for (field, value) in [
        ("component_type", json!("abc")),
        ("component_subtype", json!("éé")),
        ("component_manufacturer", json!("ab\n1")),
        ("state_hex", json!("abc")),
        ("state_hex", json!("xy")),
        ("state_hex", json!("00".repeat(MAX_VST3_STATE_BYTES + 1))),
    ] {
        let mut invalid = session(5, json!([au("x")]));
        invalid["tracks"][0]["effects"][0][field] = value;
        cases.push(invalid);
    }
    let mut duplicate = session(5, json!([au("x")]));
    duplicate["tracks"][0]["effects"][0]["parameters"] = json!([
        {"id":7,"value":0.2}, {"id":7,"value":0.3}
    ]);
    cases.push(duplicate);
    let mut nonfinite = session(5, json!([au("x")]));
    nonfinite["tracks"][0]["effects"][0]["parameters"][0]["value"] = json!(1e100);
    cases.push(nonfinite);
    let mut null = session(5, json!([au("x")]));
    null["tracks"][0]["effects"][0]["state_hex"] = Value::Null;
    cases.push(null);
    for (index, invalid) in cases.into_iter().enumerate() {
        assert!(
            validate(invalid.clone()).is_err(),
            "invalid AU case {index}: {invalid}"
        );
    }
    let mut points = session(5, json!([au("x")]));
    points["tracks"][0]["effects"][0]["parameters"][0]["points"] = json!([]);
    assert!(validate(points).is_err());
}

#[test]
fn au_foreign_effect_count_and_aggregate_state_limits_include_vst3() {
    let eight = (0..MAX_VST3_PLUGINS)
        .map(|i| {
            if i == 0 {
                au("foreign0")
            } else {
                vst3(&format!("foreign{i}"))
            }
        })
        .collect::<Vec<_>>();
    assert!(validate(session(5, json!(eight))).is_ok());
    let nine = (0..=MAX_VST3_PLUGINS)
        .map(|i| {
            if i == 0 {
                au("foreign0")
            } else {
                vst3(&format!("foreign{i}"))
            }
        })
        .collect::<Vec<_>>();
    assert!(validate(session(5, json!(nine))).is_err());

    let mut over_state = session(
        5,
        json!([
            {"kind":"au","id":"a","bypass":false,"component_type":"aufx","component_subtype":"lpas","component_manufacturer":"appl","state_hex":"00".repeat(MAX_VST3_STATE_BYTES),"parameters":[]},
            {"kind":"vst3","id":"b","bypass":false,"bundle_path":"/Library/Example.vst3","class_id":"0123456789ABCDEF0123456789ABCDEF","state_hex":"00".repeat(MAX_VST3_STATE_BYTES),"controller_state_hex":"","parameters":[]},
            {"kind":"au","id":"c","bypass":false,"component_type":"aufx","component_subtype":"lpas","component_manufacturer":"appl","state_hex":"00".repeat(MAX_VST3_STATE_BYTES),"parameters":[]},
            {"kind":"au","id":"d","bypass":false,"component_type":"aufx","component_subtype":"lpas","component_manufacturer":"appl","state_hex":"00".repeat(MAX_VST3_STATE_BYTES),"parameters":[]}
        ]),
    );
    assert_eq!(MAX_VST3_SESSION_STATE_BYTES, 4 * MAX_VST3_STATE_BYTES);
    assert!(validate(over_state.clone()).is_ok());
    over_state["tracks"][0]["effects"][1]["controller_state_hex"] = json!("00");
    assert!(validate(over_state).is_err());
}

#[cfg(not(all(feature = "au-offline", target_os = "macos")))]
#[test]
fn unavailable_hosting_preserves_active_session_and_revision() {
    use daw::control::Controller;
    let mut controller = Controller::default();
    let call = |controller: &mut Controller, method: &str, params: Value| {
        serde_json::to_value(
            controller.handle_line(
                &json!({
                    "protocol_version": 1, "id": "au", "method": method, "params": params
                })
                .to_string(),
            ),
        )
        .unwrap()
    };
    let before = call(&mut controller, "session.inspect", json!({}));
    let mut candidate = session(5, json!([au("filter")]));
    candidate["tracks"][0]["effects"][0]["state_hex"] = json!("");
    let rejected = call(
        &mut controller,
        "session.replace",
        json!({"session": candidate}),
    );
    assert_eq!(rejected["error"]["code"], "plugin_error");
    assert_eq!(call(&mut controller, "session.inspect", json!({})), before);
}
