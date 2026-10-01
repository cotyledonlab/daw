use daw::session::Session;
use serde_json::{Value, json};

const RATE: u32 = 48_000;
const SCHEMA_8: u32 = 8;
const MAX_PROGRAM_BYTES: usize = 60 * 1024;

fn puredata(program: &str, duration_frames: u64) -> Value {
    json!({
        "kind":"puredata",
        "program":program,
        "abstractions":[],
        "duration_frames":duration_frames,
        "gain":0.5,
        "controls":[]
    })
}

fn csound(program: &str, duration_frames: u64) -> Value {
    json!({
        "kind":"csound", "program":program, "duration_frames":duration_frames,
        "gain":0.5, "controls":[]
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
fn puredata_device_is_schema8_only_and_round_trips_opaque_content() {
    let mut input = session(
        SCHEMA_8,
        vec![puredata("#N canvas 0 0 100 100 10;\n#X obj 0 0 osc~;", 48)],
    );
    input["tracks"][0]["device"]["abstractions"] = json!([
        {"name":"daw-offset", "program":"#N canvas;\n#X obj 0 0 +~;"}
    ]);
    let parsed = validate(input.clone()).unwrap();
    assert_eq!(parsed.schema_version, SCHEMA_8);
    assert_eq!(serde_json::to_value(parsed).unwrap(), input);

    for version in 1..=7 {
        assert!(
            validate(session(version, vec![puredata("#N canvas;", 48)])).is_err(),
            "accepted Pure Data in schema {version}"
        );
    }
    assert!(validate(session(7, vec![])).is_ok());
    assert!(validate(session(7, vec![csound("<CsoundSynthesizer/>", 48)])).is_ok());
    assert!(validate(session(SCHEMA_8, vec![csound("<CsoundSynthesizer/>", 48)])).is_ok());
}

#[test]
fn puredata_program_and_abstraction_snapshots_share_byte_budget() {
    let mut exact = puredata("p".repeat(40_000).as_str(), 48);
    exact["abstractions"] = json!([
        {"name":"a", "program":"q".repeat(MAX_PROGRAM_BYTES - 40_000)}
    ]);
    assert!(validate(session(SCHEMA_8, vec![exact])).is_ok());

    let mut oversized = puredata("p".repeat(40_000).as_str(), 48);
    oversized["abstractions"] = json!([
        {"name":"a", "program":"q".repeat(MAX_PROGRAM_BYTES - 40_000 + 1)}
    ]);
    assert!(validate(session(SCHEMA_8, vec![oversized])).is_err());

    assert!(validate(session(SCHEMA_8, vec![puredata(&"é".repeat(30_720), 48)])).is_ok());
    assert!(validate(session(SCHEMA_8, vec![puredata(&"é".repeat(30_721), 48)])).is_err());
    for program in ["", "x\0y"] {
        assert!(validate(session(SCHEMA_8, vec![puredata(program, 48)])).is_err());
    }
    let mut empty_abstraction = puredata("x", 48);
    empty_abstraction["abstractions"] = json!([{"name":"empty", "program":""}]);
    assert!(validate(session(SCHEMA_8, vec![empty_abstraction])).is_err());
    let mut nul_abstraction = puredata("x", 48);
    nul_abstraction["abstractions"] = json!([{"name":"nul", "program":"x\0y"}]);
    assert!(validate(session(SCHEMA_8, vec![nul_abstraction])).is_err());
}

#[test]
fn abstraction_names_are_bounded_ascii_unique_and_not_paths() {
    let mut okay = puredata("x", 48);
    okay["abstractions"] = json!([
        {"name":"_tone-2", "program":"a"},
        {"name":"tone", "program":"b"}
    ]);
    assert!(validate(session(SCHEMA_8, vec![okay])).is_ok());

    let long_name = "a".repeat(65);
    for name in ["", "2tone", "../tone", "a/b", "a\\b", ".", "é", &long_name] {
        let mut invalid = puredata("x", 48);
        invalid["abstractions"] = json!([{"name":name, "program":"a"}]);
        assert!(
            validate(session(SCHEMA_8, vec![invalid])).is_err(),
            "accepted name {name:?}"
        );
    }
    let mut duplicate = puredata("x", 48);
    duplicate["abstractions"] = json!([
        {"name":"same", "program":"a"}, {"name":"same", "program":"b"}
    ]);
    assert!(validate(session(SCHEMA_8, vec![duplicate])).is_err());
    let mut too_many = puredata("x", 48);
    too_many["abstractions"] = json!(
        (0..17)
            .map(|index| json!({"name":format!("abs{index}"), "program":"x"}))
            .collect::<Vec<_>>()
    );
    assert!(validate(session(SCHEMA_8, vec![too_many])).is_err());
    let mut extra = puredata("x", 48);
    extra["abstractions"] = json!([{"name":"ok", "program":"x", "path":"../x"}]);
    assert!(validate(session(SCHEMA_8, vec![extra])).is_err());
}

#[test]
fn puredata_duration_and_track_shape_obey_continuous_bounds() {
    for (rate, minimum) in [(48_000, 48), (44_100, 45), (8_000, 8), (192_000, 192)] {
        let mut valid = session(SCHEMA_8, vec![puredata("x", minimum)]);
        valid["sample_rate"] = json!(rate);
        assert!(validate(valid).is_ok());
        let mut too_short = session(SCHEMA_8, vec![puredata("x", minimum - 1)]);
        too_short["sample_rate"] = json!(rate);
        assert!(validate(too_short).is_err());
    }
    assert!(validate(session(SCHEMA_8, vec![puredata("x", RATE as u64 * 10)])).is_ok());
    assert!(validate(session(SCHEMA_8, vec![puredata("x", RATE as u64 * 10 + 1)])).is_err());

    let mut bad_gain = puredata("x", 48);
    for gain in [-0.001, 1.001] {
        bad_gain["gain"] = json!(gain);
        assert!(validate(session(SCHEMA_8, vec![bad_gain.clone()])).is_err());
    }
    bad_gain["gain"] = Value::Null;
    assert!(validate(session(SCHEMA_8, vec![bad_gain])).is_err());

    let mut sequenced = session(SCHEMA_8, vec![puredata("x", 48)]);
    sequenced["tracks"][0]["mode"] = json!("sequenced");
    assert!(validate(sequenced).is_err());
    let mut clips = session(SCHEMA_8, vec![puredata("x", 48)]);
    clips["tracks"][0]["clips"] = json!([{"kind":"audio"}]);
    assert!(validate(clips).is_err());
    let mut no_effects = session(SCHEMA_8, vec![puredata("x", 48)]);
    no_effects["tracks"][0]
        .as_object_mut()
        .unwrap()
        .remove("effects");
    assert!(validate(no_effects).is_err());
}

#[test]
fn puredata_control_names_values_and_points_are_strict() {
    let mut valid = session(SCHEMA_8, vec![puredata("x", 256)]);
    valid["tracks"][0]["device"]["controls"] = json!([{
        "name":"frequency", "value":440.0,
        "points":[{"frame":0,"value":440.0},{"frame":64,"value":660.0}]
    }]);
    assert!(validate(valid).is_ok());

    let mut f32_max = session(SCHEMA_8, vec![puredata("x", 64)]);
    f32_max["tracks"][0]["device"]["controls"] = json!([{"name":"x", "value":3.4e38, "points":[]}]);
    assert!(validate(f32_max).is_ok());
    for control in [
        json!({"name":"", "value":0.0, "points":[]}),
        json!({"name":"x\0y", "value":0.0, "points":[]}),
        json!({"name":"é".repeat(65), "value":0.0, "points":[]}),
        json!({"name":"x", "value":3.5e38, "points":[]}),
        json!({"name":"x", "value":"bad", "points":[]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":1,"value":1.0}]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":0,"value":1.0},{"frame":0,"value":2.0}]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":64,"value":1.0},{"frame":0,"value":2.0}]}),
        json!({"name":"x", "value":0.0, "points":[{"frame":64,"value":1.0,"extra":1}]}),
        json!({"name":"x", "value":0.0, "points":[], "extra":1}),
    ] {
        let mut candidate = session(SCHEMA_8, vec![puredata("x", 128)]);
        candidate["tracks"][0]["device"]["controls"] = json!([control]);
        assert!(
            validate(candidate).is_err(),
            "accepted invalid control {control}"
        );
    }

    let mut nul_controls = session(SCHEMA_8, vec![puredata("x", 64)]);
    nul_controls["tracks"][0]["device"]["controls"] = Value::Null;
    assert!(validate(nul_controls).is_err());
    let mut duplicate = session(SCHEMA_8, vec![puredata("x", 64)]);
    duplicate["tracks"][0]["device"]["controls"] = json!([
        {"name":"x", "value":0.0, "points":[]},
        {"name":"x", "value":1.0, "points":[]}
    ]);
    assert!(validate(duplicate).is_err());
    let mut too_many = session(SCHEMA_8, vec![puredata("x", 64)]);
    too_many["tracks"][0]["device"]["controls"] = json!(
        (0..=64)
            .map(|index| json!({"name":format!("c{index}"),"value":0.0,"points":[]}))
            .collect::<Vec<_>>()
    );
    assert!(validate(too_many).is_err());
    let mut too_many_points = session(SCHEMA_8, vec![puredata("x", 33_000)]);
    too_many_points["tracks"][0]["device"]["controls"] = json!([{
        "name":"x", "value":0.0,
        "points":(0..=512).map(|index| json!({"frame":index*64,"value":0.0})).collect::<Vec<_>>()
    }]);
    assert!(validate(too_many_points).is_err());
}

#[test]
fn puredata_sources_share_runtime_count_duration_and_point_budgets() {
    let one_second = puredata("x", RATE as u64);
    assert!(validate(session(SCHEMA_8, vec![one_second.clone(); 4])).is_ok());
    assert!(validate(session(SCHEMA_8, vec![one_second.clone(); 5])).is_err());
    assert!(
        validate(session(
            SCHEMA_8,
            vec![
                puredata("x", RATE as u64 * 6),
                puredata("y", RATE as u64 * 5)
            ],
        ))
        .is_err()
    );

    let mixed_exact = session(
        SCHEMA_8,
        vec![
            puredata("pd1", RATE as u64 * 2),
            puredata("pd2", RATE as u64 * 2),
            csound("cs1", RATE as u64 * 3),
            csound("cs2", RATE as u64 * 3),
        ],
    );
    assert!(validate(mixed_exact).is_ok());
    let mixed_over = session(
        SCHEMA_8,
        vec![
            puredata("pd1", RATE as u64 * 2),
            puredata("pd2", RATE as u64 * 2),
            csound("cs1", RATE as u64 * 3),
            csound("cs2", RATE as u64 * 3 + 1),
        ],
    );
    assert!(validate(mixed_over).is_err());

    let mut cross_source_points = session(
        SCHEMA_8,
        vec![puredata("pd", RATE as u64), csound("cs", RATE as u64)],
    );
    for index in 0..2 {
        cross_source_points["tracks"][index]["device"]["controls"] = json!([{
            "name":"control", "value":0.0,
            "points":(0..256).map(|point| json!({"frame":point*64,"value":0.0})).collect::<Vec<_>>()
        }]);
    }
    assert!(validate(cross_source_points.clone()).is_ok());
    cross_source_points["tracks"][1]["device"]["controls"][0]["points"]
        .as_array_mut()
        .unwrap()
        .push(json!({"frame":256*64,"value":0.0}));
    assert!(validate(cross_source_points).is_err());
}

#[test]
fn strict_device_and_nested_shapes_reject_unknown_fields() {
    let mut unknown_device_field = session(SCHEMA_8, vec![puredata("x", 48)]);
    unknown_device_field["tracks"][0]["device"]["path"] = json!("../patch.pd");
    assert!(validate(unknown_device_field).is_err());
    let mut unknown_abstraction_field = session(SCHEMA_8, vec![puredata("x", 48)]);
    unknown_abstraction_field["tracks"][0]["device"]["abstractions"] =
        json!([{"name":"a", "program":"x", "path":"../a.pd"}]);
    assert!(validate(unknown_abstraction_field).is_err());
}

#[test]
fn failed_puredata_runtime_preparation_preserves_session_and_revision() {
    use daw::control::Controller;

    let mut controller = Controller::default();
    let request = |controller: &mut Controller, method: &str, params: Value| {
        serde_json::to_value(
            controller.handle_line(
                &json!({"protocol_version":1,"id":"puredata","method":method,"params":params})
                    .to_string(),
            ),
        )
        .unwrap()
    };
    let before = request(&mut controller, "session.inspect", json!({}));
    let broken_patch = "#N canvas 0 0 100 100 10;\n#X obj 0 0 daw_definitely_missing_object;";
    let candidate = session(SCHEMA_8, vec![puredata(broken_patch, 48)]);
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
