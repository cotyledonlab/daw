use daw::session::Session;
use serde_json::{Value, json};

const RATE: u32 = 48_000;
const SCHEMA_7: u32 = 7;
const MAX_CSD_BYTES: usize = 60 * 1024;

fn csound(program: &str, duration_frames: u64) -> Value {
    json!({
        "kind":"csound",
        "program":program,
        "duration_frames":duration_frames,
        "gain":0.5,
        "controls":[]
    })
}

fn session(version: u32, devices: Vec<Value>) -> Value {
    json!({
        "schema_version":version,
        "sample_rate":RATE,
        "tempo_milli_bpm":120000,
        "tracks":devices.into_iter().enumerate().map(|(i, device)| json!({
            "id":format!("track-{i}"), "device":device,
            "mode":"continuous", "clips":[], "effects":[]
        })).collect::<Vec<_>>()
    })
}

fn validate(value: Value) -> Result<Session, String> {
    let parsed: Session = serde_json::from_value(value).map_err(|error| error.to_string())?;
    parsed.validate()?;
    Ok(parsed)
}

#[test]
fn csound_device_is_schema7_only_and_round_trips() {
    let input = session(
        SCHEMA_7,
        vec![csound("<CsoundSynthesizer>\n</CsoundSynthesizer>", 48)],
    );
    let parsed = validate(input.clone()).unwrap();
    assert_eq!(parsed.schema_version, SCHEMA_7);
    assert_eq!(serde_json::to_value(parsed).unwrap(), input);

    assert!(validate(session(6, vec![csound("<CsoundSynthesizer/>", 48)])).is_err());
    assert!(validate(session(1, vec![csound("<CsoundSynthesizer/>", 48)])).is_err());
    assert!(validate(session(6, vec![])).is_ok());
}

#[test]
fn csound_program_duration_gain_and_track_shape_are_bounded() {
    let valid_program = "x".repeat(MAX_CSD_BYTES);
    assert!(validate(session(SCHEMA_7, vec![csound(&valid_program, 48)])).is_ok());
    for (program, frames, gain) in [
        (String::new(), 48, 0.5),
        ("x".repeat(MAX_CSD_BYTES + 1), 48, 0.5),
        ("x\0y".into(), 48, 0.5),
        ("x".into(), 47, 0.5),
        ("x".into(), RATE as u64 * 10 + 1, 0.5),
        ("x".into(), 48, -0.001),
        ("x".into(), 48, 1.001),
    ] {
        assert!(
            validate(session(
                SCHEMA_7,
                vec![{
                    let mut value = csound(&program, frames);
                    value["gain"] = json!(gain);
                    value
                }]
            ))
            .is_err(),
            "accepted invalid program/duration/gain: {program:?}, {frames}, {gain}"
        );
    }
    let mut nonfinite_gain = session(SCHEMA_7, vec![csound("x", 48)]);
    nonfinite_gain["tracks"][0]["device"]["gain"] = json!(null);
    assert!(validate(nonfinite_gain).is_err());
    assert!(validate(session(SCHEMA_7, vec![csound(&"é".repeat(30_720), 48)])).is_ok());
    assert!(validate(session(SCHEMA_7, vec![csound(&"é".repeat(30_721), 48)])).is_err());

    let mut too_many = session(SCHEMA_7, vec![csound("x", 48)]);
    too_many["tracks"][0]["device"]["controls"] = json!(
        (0..=64)
            .map(|i| json!({"name":format!("c{i}"),"value":0.0,"points":[]}))
            .collect::<Vec<_>>()
    );
    assert!(validate(too_many).is_err());
}

#[test]
fn csound_control_contract_rejects_bad_names_values_points_and_fields() {
    let valid = json!({"name":"frequency", "value":440.0, "points":[
        {"frame":0,"value":440.0}, {"frame":64,"value":660.0}
    ]});
    let mut okay = session(SCHEMA_7, vec![csound("x", 128)]);
    okay["tracks"][0]["device"]["controls"] = json!([valid]);
    assert!(validate(okay).is_ok());
    let mut large_native = session(SCHEMA_7, vec![csound("x", 128)]);
    large_native["tracks"][0]["device"]["controls"] =
        json!([{"name":"x", "value":1.0e100, "points":[]}]);
    assert!(validate(large_native).is_ok());

    let mut invalid = Vec::new();
    for control in [
        json!({"name":"", "value":0.0, "points":[]}),
        json!({"name":"x\0y", "value":0.0, "points":[]}),
        json!({"name":"é".repeat(65), "value":0.0, "points":[]}),
        json!({"name":"x", "value":"bad", "points":[]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":1,"value":1.0}]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":0,"value":1.0},{"frame":0,"value":2.0}]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":128,"value":1.0}]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":64,"value":1.0},{"frame":0,"value":2.0}]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":64,"value":1.0,"extra":1}]}),
        json!({"name":"x", "value":0.0, "points":[], "extra":1}),
    ] {
        let mut value = session(SCHEMA_7, vec![csound("x", 128)]);
        value["tracks"][0]["device"]["controls"] = json!([control]);
        invalid.push(value);
    }
    let mut null_controls = session(SCHEMA_7, vec![csound("x", 128)]);
    null_controls["tracks"][0]["device"]["controls"] = Value::Null;
    invalid.push(null_controls);
    for (index, value) in invalid.into_iter().enumerate() {
        assert!(
            validate(value.clone()).is_err(),
            "invalid control case {index}: {value}"
        );
    }
}

#[test]
fn csound_sources_share_source_count_duration_and_point_budgets() {
    let one_second = csound("x", RATE as u64);
    assert!(validate(session(SCHEMA_7, vec![one_second.clone(); 4])).is_ok());
    assert!(validate(session(SCHEMA_7, vec![one_second.clone(); 5])).is_err());
    assert!(
        validate(session(
            SCHEMA_7,
            vec![csound("x", RATE as u64 * 6), csound("y", RATE as u64 * 5)]
        ))
        .is_err()
    );

    let mut over_points = csound("x", RATE as u64);
    over_points["controls"] = json!([{"name":"x", "value":0.0,
        "points":(0..=512).map(|i| json!({"frame":i*64,"value":i as f64})).collect::<Vec<_>>()
    }]);
    assert!(validate(session(SCHEMA_7, vec![over_points])).is_err());
}

#[test]
fn csound_track_requires_continuous_mode_empty_clips_and_effects() {
    let mut sequenced = session(SCHEMA_7, vec![csound("x", 48)]);
    sequenced["tracks"][0]["mode"] = json!("sequenced");
    assert!(validate(sequenced).is_err());
    let mut clips = session(SCHEMA_7, vec![csound("x", 48)]);
    clips["tracks"][0]["clips"] =
        json!([{"kind":"notes","id":"c","start_frame":0,"length_frames":1,"notes":[]}]);
    assert!(validate(clips).is_err());
    let mut absent_effects = session(SCHEMA_7, vec![csound("x", 48)]);
    absent_effects["tracks"][0]
        .as_object_mut()
        .unwrap()
        .remove("effects");
    assert!(validate(absent_effects).is_err());
}

#[test]
fn rejected_runtime_preparation_preserves_session_and_revision() {
    use daw::control::Controller;
    use serde_json::json;

    let mut controller = Controller::default();
    let request = |controller: &mut Controller, method: &str, params: Value| {
        serde_json::to_value(
            controller.handle_line(
                &json!({"protocol_version":1,"id":"csound","method":method,"params":params})
                    .to_string(),
            ),
        )
        .unwrap()
    };
    let before = request(&mut controller, "session.inspect", json!({}));
    let candidate = session(SCHEMA_7, vec![csound("<CsoundSynthesizer/>\n", 48)]);
    let rejected = request(
        &mut controller,
        "session.replace",
        json!({"session":candidate}),
    );
    assert_eq!(rejected["ok"], false);
    assert_eq!(rejected["error"]["code"], "runtime_error");
    assert_eq!(
        request(&mut controller, "session.inspect", json!({})),
        before
    );
}
