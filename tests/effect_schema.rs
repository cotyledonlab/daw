use daw::{
    control::{Controller, PROTOCOL_VERSION},
    session::{Effect, MAX_EFFECT_GAIN, MAX_EFFECTS_PER_TRACK, SCHEMA_VERSION_3, Session},
};
use serde_json::{Value, json};

fn effect(id: &str, gain: f64, bypass: bool) -> Value {
    json!({"kind":"gain", "id":id, "gain":gain, "bypass":bypass})
}

fn schema3(effects: Value) -> Value {
    json!({
        "schema_version":3,
        "sample_rate":48000,
        "tempo_milli_bpm":120000,
        "tracks":[{
            "id":"tone",
            "device":{"kind":"sine", "frequency_hz":440.0, "gain":0.5},
            "mode":"continuous",
            "clips":[],
            "effects":effects
        }]
    })
}

fn validate(value: Value) -> Result<Session, serde_json::Error> {
    let session: Session = serde_json::from_value(value)?;
    session.validate().map_err(serde::de::Error::custom)?;
    Ok(session)
}

#[test]
fn schema3_effects_round_trip_and_allow_boundary_gain() {
    let input = schema3(json!([effect("boost", MAX_EFFECT_GAIN, true)]));
    let session = validate(input.clone()).unwrap();
    assert_eq!(session.schema_version, SCHEMA_VERSION_3);
    assert_eq!(session.tracks[0].effects.as_ref().unwrap().len(), 1);
    let encoded = serde_json::to_value(&session).unwrap();
    let decoded: Session = serde_json::from_value(encoded).unwrap();
    assert_eq!(decoded, session);
    assert_eq!(
        session.tracks[0].effects.as_ref().unwrap()[0],
        Effect::Gain {
            id: "boost".into(),
            gain: MAX_EFFECT_GAIN,
            bypass: true
        }
    );
}

#[test]
fn schemas_one_and_two_reject_effects_even_when_empty() {
    for version in [1, 2] {
        let mut value = schema3(json!([]));
        value["schema_version"] = json!(version);
        if version == 1 {
            value.as_object_mut().unwrap().remove("tempo_milli_bpm");
            value["tracks"][0].as_object_mut().unwrap().remove("mode");
            value["tracks"][0].as_object_mut().unwrap().remove("clips");
        }
        assert!(
            validate(value).is_err(),
            "schema {version} accepted effects"
        );
    }
}

#[test]
fn schema3_requires_timeline_and_effect_fields_and_rejects_null() {
    let mut missing = schema3(json!([]));
    missing["tracks"][0]
        .as_object_mut()
        .unwrap()
        .remove("effects");
    assert!(
        validate(missing)
            .unwrap_err()
            .to_string()
            .contains("requires track effects")
    );

    let mut missing_clips = schema3(json!([]));
    missing_clips["tracks"][0]
        .as_object_mut()
        .unwrap()
        .remove("clips");
    assert!(
        validate(missing_clips)
            .unwrap_err()
            .to_string()
            .contains("requires track clips")
    );

    for value in [
        schema3(Value::Null),
        schema3(json!([{"kind":"gain", "id":"g", "gain":1.0}])),
    ] {
        assert!(serde_json::from_value::<Session>(value).is_err());
    }
}

#[test]
fn effect_shape_ids_count_and_gain_are_strict() {
    let cases = [
        schema3(json!([effect("", 1.0, false)])),
        schema3(json!([effect(&"x".repeat(129), 1.0, false)])),
        schema3(json!([
            effect("same", 1.0, false),
            effect("same", 2.0, false)
        ])),
        schema3(json!([effect("g", -0.01, false)])),
        schema3(json!([effect("g", MAX_EFFECT_GAIN + 0.01, false)])),
        schema3(json!([effect("g", f64::NAN, false)])),
        schema3(json!([{"kind":"gain", "id":"g", "gain":1.0, "bypass":false, "extra":true}])),
        schema3(json!([{"kind":"gain", "id":"g", "gain":1.0}])),
        schema3(json!([{"kind":"future", "id":"g", "gain":1.0, "bypass":false}])),
    ];
    for value in cases {
        assert!(validate(value).is_err());
    }

    for count in [MAX_EFFECTS_PER_TRACK, MAX_EFFECTS_PER_TRACK + 1] {
        let effects: Vec<_> = (0..count)
            .map(|i| effect(&format!("g{i}"), 1.0, false))
            .collect();
        assert_eq!(
            validate(schema3(json!(effects))).is_ok(),
            count == MAX_EFFECTS_PER_TRACK
        );
    }
}

fn request(controller: &mut Controller, method: &str, params: Value) -> Value {
    let line = json!({
        "protocol_version":PROTOCOL_VERSION,
        "id":"effect-test",
        "method":method,
        "params":params
    })
    .to_string();
    serde_json::to_value(controller.handle_line(&line)).unwrap()
}

#[test]
fn invalid_effect_replacement_preserves_controller_session_and_revision() {
    let mut controller = Controller::default();
    let initial = request(&mut controller, "session.inspect", json!({}))["result"].clone();
    let invalid = request(
        &mut controller,
        "session.replace",
        json!({"session":schema3(json!([effect("bad", 5.0, false)]))}),
    );
    assert_eq!(invalid["ok"], false);
    assert_eq!(invalid["error"]["code"], "invalid_session");
    let after = request(&mut controller, "session.inspect", json!({}))["result"].clone();
    assert_eq!(after["revision"], initial["revision"]);
    assert_eq!(after["session"], initial["session"]);
}

#[test]
fn gain_chain_state_survives_controller_save_load_and_requires_matched_native_rate() {
    let value = schema3(json!([
        effect("boost", 2.0, false),
        effect("skip", 0.0, true)
    ]));
    let mut controller = Controller::default();
    assert_eq!(
        request(&mut controller, "session.replace", json!({"session":value}))["ok"],
        true
    );
    let directory = tempfile::tempdir().unwrap();
    let path = directory
        .path()
        .join("effects.json")
        .to_string_lossy()
        .into_owned();
    assert_eq!(
        request(&mut controller, "session.save", json!({"path":path}))["ok"],
        true
    );
    let mut restored = Controller::default();
    let response = request(&mut restored, "session.load", json!({"path":path}));
    assert_eq!(response["ok"], true);
    assert_eq!(response["result"], value);
    let session: Session = serde_json::from_value(value).unwrap();
    assert!(
        daw::audio_buffer::PlaybackBuffer::prepare(&session, session.sample_rate + 1, 2, 1.0, 0.1)
            .is_err()
    );
}
