use daw::{
    control::{Controller, PROTOCOL_VERSION},
    session::{MAX_AUTOMATION_LANES_PER_TRACK, MAX_AUTOMATION_POINTS, MAX_FRAME, Session},
};
use serde_json::{Value, json};

fn session_with(automation: Value) -> Value {
    json!({
        "schema_version":3, "sample_rate":8000, "tempo_milli_bpm":120000,
        "tracks":[{
            "id":"tone", "mode":"continuous", "clips":[],
            "device":{"kind":"sine", "frequency_hz":1000.0, "gain":0.5},
            "effects":[{"kind":"gain", "id":"trim", "gain":1.0, "bypass":false}],
            "automation":automation
        }]
    })
}

fn lane(points: Value) -> Value {
    json!({"effect_id":"trim", "parameter":"gain", "interpolation":"step", "points":points})
}

fn valid() -> Value {
    session_with(json!([lane(json!([
        {"frame":0,"value":0.5}, {"frame":MAX_FRAME,"value":4.0}
    ]))]))
}

fn validate(value: Value) -> Result<Session, String> {
    let parsed: Session = serde_json::from_value(value).map_err(|error| error.to_string())?;
    parsed.validate()?;
    Ok(parsed)
}

#[test]
fn automation_shape_and_boundaries_round_trip() {
    let value = valid();
    let parsed = validate(value.clone()).unwrap();
    assert_eq!(serde_json::to_value(parsed).unwrap(), value);
    assert!(validate(session_with(json!([]))).is_ok());
}

#[test]
fn automation_rejects_null_malformed_fields_and_bad_values() {
    let mut null = valid();
    null["tracks"][0]["automation"] = Value::Null;
    assert!(validate(null).is_err());

    type Mutation = Box<dyn Fn(&mut Value)>;
    let mutations: Vec<Mutation> = vec![
        Box::new(|v| v["tracks"][0]["automation"][0]["surprise"] = json!(true)),
        Box::new(|v| v["tracks"][0]["automation"][0]["effect_id"] = json!("missing")),
        Box::new(|v| v["tracks"][0]["automation"][0]["parameter"] = json!("frequency_hz")),
        Box::new(|v| v["tracks"][0]["automation"][0]["interpolation"] = json!("linear")),
        Box::new(|v| v["tracks"][0]["automation"][0]["points"] = json!([])),
        Box::new(|v| v["tracks"][0]["automation"][0]["points"][1]["frame"] = json!(0)),
        Box::new(|v| v["tracks"][0]["automation"][0]["points"][0]["frame"] = json!(MAX_FRAME + 1)),
        Box::new(|v| v["tracks"][0]["automation"][0]["points"][0]["value"] = json!(4.01)),
        Box::new(|v| v["tracks"][0]["automation"][0]["points"][0]["value"] = json!(-0.01)),
    ];
    for mutate in mutations {
        let mut invalid = valid();
        mutate(&mut invalid);
        assert!(validate(invalid).is_err());
    }

    let mut effects_missing = valid();
    effects_missing["tracks"][0]["effects"] = json!([]);
    assert!(validate(effects_missing).is_err());
    let mut duplicate_lane = valid();
    duplicate_lane["tracks"][0]["automation"] = json!([
        lane(json!([{"frame":0,"value":1.0}])),
        lane(json!([{"frame":1,"value":2.0}]))
    ]);
    assert!(validate(duplicate_lane).is_err());
}

#[test]
fn schema_one_and_two_reject_automation_even_when_empty() {
    for version in [1, 2] {
        let mut value = valid();
        value["schema_version"] = json!(version);
        if version == 1 {
            value.as_object_mut().unwrap().remove("tempo_milli_bpm");
            value["tracks"][0].as_object_mut().unwrap().remove("mode");
            value["tracks"][0].as_object_mut().unwrap().remove("clips");
            value["tracks"][0]
                .as_object_mut()
                .unwrap()
                .remove("effects");
        }
        value["tracks"][0]["automation"] = json!([]);
        assert!(
            validate(value).is_err(),
            "schema {version} accepted automation"
        );
    }
}

#[test]
fn automation_lane_and_session_point_limits_are_enforced() {
    let lanes = (0..=MAX_AUTOMATION_LANES_PER_TRACK)
        .map(|_| lane(json!([{"frame":0,"value":1.0}])))
        .collect::<Vec<_>>();
    let mut too_many_lanes = valid();
    too_many_lanes["tracks"][0]["automation"] = json!(lanes);
    assert!(validate(too_many_lanes).is_err());

    let points = (0..=MAX_AUTOMATION_POINTS)
        .map(|frame| json!({"frame":frame,"value":1.0}))
        .collect::<Vec<_>>();
    assert!(validate(session_with(json!([lane(json!(points))]))).is_err());
}

fn request(controller: &mut Controller, method: &str, params: Value) -> Value {
    let line = json!({"protocol_version":PROTOCOL_VERSION, "id":"automation-test", "method":method, "params":params}).to_string();
    serde_json::to_value(controller.handle_line(&line)).unwrap()
}

#[test]
fn replacement_failure_preserves_revision_and_saved_automation_round_trips() {
    let mut controller = Controller::default();
    let initial = request(&mut controller, "session.inspect", json!({}))["result"].clone();
    let mut invalid = valid();
    invalid["tracks"][0]["automation"][0]["points"] = json!([]);
    let rejected = request(
        &mut controller,
        "session.replace",
        json!({"session":invalid}),
    );
    assert_eq!(rejected["ok"], false);
    assert_eq!(rejected["error"]["code"], "invalid_session");
    let after = request(&mut controller, "session.inspect", json!({}))["result"].clone();
    assert_eq!(after, initial);

    let value = valid();
    assert_eq!(
        request(&mut controller, "session.replace", json!({"session":value}))["ok"],
        true
    );
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("automation.json")
        .to_string_lossy()
        .into_owned();
    assert_eq!(
        request(&mut controller, "session.save", json!({"path":path}))["ok"],
        true
    );
    let mut restored = Controller::default();
    let loaded = request(&mut restored, "session.load", json!({"path":path}));
    assert_eq!(loaded["ok"], true);
    assert_eq!(loaded["result"], value);
}
