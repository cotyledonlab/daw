use daw::{
    control::{Controller, PROTOCOL_VERSION},
    session::{
        MAX_AUTOMATION_POINTS, MAX_VST3_PLUGINS, MAX_VST3_STATE_BYTES, SCHEMA_VERSION_4, Session,
    },
};
use serde_json::{Value, json};

fn plugin(id: &str) -> Value {
    json!({
        "kind":"vst3", "id":id, "bypass":false,
        "bundle_path":"/Library/Audio/Plug-Ins/VST3/Example.vst3",
        "class_id":"0123456789ABCDEF0123456789ABCDEF",
        "state_hex":"aB00", "controller_state_hex":"",
        "parameters":[{"id":7,"value":0.5,"points":[{"frame":1,"value":0.25}]}]
    })
}

fn session(effects: Value) -> Value {
    json!({
        "schema_version":4, "sample_rate":48000, "tempo_milli_bpm":120000,
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
fn schema4_plugin_round_trips_and_schema3_contract_stays_distinct() {
    let input = session(json!([plugin("synth")]));
    let parsed = validate(input.clone()).unwrap();
    assert_eq!(parsed.schema_version, SCHEMA_VERSION_4);
    assert_eq!(serde_json::to_value(parsed).unwrap(), input);

    let mut old = input;
    old["schema_version"] = json!(3);
    assert!(validate(old).is_err());
    for version in [1, 2] {
        let mut old = session(json!([plugin("synth")]));
        old["schema_version"] = json!(version);
        assert!(validate(old).is_err());
    }
}

#[test]
fn plugin_shape_identity_limits_and_normalized_values_are_strict() {
    let mut invalid_cases = Vec::new();
    let mut v = session(json!([plugin("x")]));
    v["tracks"][0]["effects"][0]["extra"] = json!(true);
    invalid_cases.push(v);
    let mut v = session(json!([plugin("x")]));
    v["tracks"][0]["effects"][0]
        .as_object_mut()
        .unwrap()
        .remove("bypass");
    invalid_cases.push(v);
    for (field, value) in [
        ("bundle_path", json!("relative/thing.vst3")),
        ("bundle_path", json!(format!("/{}.vst3", "x".repeat(4091)))),
        ("bundle_path", json!("/tmp/Example.VST3")),
        ("class_id", json!("0123456789abcdef0123456789abcdef")),
        ("class_id", json!("0123")),
        ("state_hex", json!("abc")),
        ("state_hex", json!("xz")),
        ("state_hex", json!("00".repeat(MAX_VST3_STATE_BYTES + 1))),
        ("value", json!(1.01)),
    ] {
        let mut v = session(json!([plugin("x")]));
        let target = if field == "value" {
            &mut v["tracks"][0]["effects"][0]["parameters"][0]["value"]
        } else {
            &mut v["tracks"][0]["effects"][0][field]
        };
        *target = value;
        invalid_cases.push(v);
    }
    let mut duplicate = session(json!([plugin("x")]));
    duplicate["tracks"][0]["effects"][0]["parameters"] = json!([
        {"id":7,"value":0.2,"points":[]}, {"id":7,"value":0.3,"points":[]}
    ]);
    invalid_cases.push(duplicate);
    for (index, invalid) in invalid_cases.into_iter().enumerate() {
        assert!(
            validate(invalid.clone()).is_err(),
            "invalid case {index} was accepted: {invalid}"
        );
    }

    let mut null = session(json!([plugin("x")]));
    null["tracks"][0]["effects"][0]["controller_state_hex"] = Value::Null;
    assert!(serde_json::from_value::<Session>(null).is_err());
}

#[test]
fn plugin_session_rate_state_plugin_and_combined_point_limits_apply_even_when_bypassed() {
    let mut wrong_rate = session(json!([plugin("x")]));
    wrong_rate["sample_rate"] = json!(44100);
    wrong_rate["tracks"][0]["effects"][0]["bypass"] = json!(true);
    assert!(validate(wrong_rate).is_err());

    let too_many_plugins = session(json!(
        (0..=MAX_VST3_PLUGINS)
            .map(|i| plugin(&format!("p{i}")))
            .collect::<Vec<_>>()
    ));
    assert!(validate(too_many_plugins).is_err());

    let mut too_much_state = session(json!([
        plugin("a"),
        plugin("b"),
        plugin("c"),
        plugin("d"),
        plugin("e")
    ]));
    for effect in too_much_state["tracks"][0]["effects"]
        .as_array_mut()
        .unwrap()
    {
        effect["state_hex"] = json!("00".repeat(MAX_VST3_STATE_BYTES));
        effect["controller_state_hex"] = json!("");
    }
    assert!(validate(too_much_state).is_err());

    let mut bad_points = session(json!([plugin("x")]));
    bad_points["tracks"][0]["effects"][0]["parameters"][0]["points"] = json!([
        {"frame":2,"value":0.5}, {"frame":2,"value":0.6}
    ]);
    assert!(validate(bad_points).is_err());
    let mut too_many_points = session(json!([plugin("x")]));
    too_many_points["tracks"][0]["effects"][0]["parameters"][0]["points"] = json!(
        (0..=MAX_AUTOMATION_POINTS)
            .map(|frame| json!({"frame":frame,"value":0.5}))
            .collect::<Vec<_>>()
    );
    assert!(validate(too_many_points).is_err());

    let mut combined = session(json!([
        plugin("instrument"),
        {"kind":"gain","id":"trim","gain":1.0,"bypass":false}
    ]));
    combined["tracks"][0]["effects"][0]["parameters"][0]["points"] = json!(
        (0..MAX_AUTOMATION_POINTS)
            .map(|frame| json!({"frame":frame,"value":0.5}))
            .collect::<Vec<_>>()
    );
    combined["tracks"][0]["automation"] = json!([{
        "effect_id":"trim", "parameter":"gain", "interpolation":"step",
        "points":[{"frame":MAX_AUTOMATION_POINTS,"value":0.75}]
    }]);
    assert!(validate(combined).is_err());
}

#[test]
fn gain_automation_cannot_target_a_vst3_id_and_rejected_replace_rolls_back() {
    let mut value = session(json!([plugin("same-id")]));
    value["tracks"][0]["automation"] = json!([{
        "effect_id":"same-id", "parameter":"gain", "interpolation":"step",
        "points":[{"frame":0,"value":0.5}]
    }]);
    assert!(validate(value.clone()).is_err());

    let mut controller = Controller::default();
    let request = |controller: &mut Controller, method: &str, params: Value| {
        let line = json!({"protocol_version":PROTOCOL_VERSION,"id":"vst3-test","method":method,"params":params}).to_string();
        serde_json::to_value(controller.handle_line(&line)).unwrap()
    };
    let initial = request(&mut controller, "session.inspect", json!({}))["result"].clone();
    let rejected = request(&mut controller, "session.replace", json!({"session":value}));
    assert_eq!(rejected["ok"], false);
    assert_eq!(rejected["error"]["code"], "invalid_session");
    assert_eq!(
        request(&mut controller, "session.inspect", json!({}))["result"],
        initial
    );
}
