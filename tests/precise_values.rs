//! Musical edits must preserve untouched IEEE-754 values across the JSON wire
//! and disk; approximate equality would hide detuning and revision drift.
use daw::control::{Controller, PROTOCOL_VERSION};
use serde_json::{Value, json};

fn request(controller: &mut Controller, method: &str, params: Value) -> Value {
    let line =
        json!({"protocol_version":PROTOCOL_VERSION,"id":"precise","method":method,"params":params})
            .to_string();
    let response = controller.handle_line(&line);
    // Exercise the response wire parser too, rather than comparing Rust Values.
    let response: Value = serde_json::from_str(&serde_json::to_string(&response).unwrap()).unwrap();
    assert_eq!(response["ok"], true, "{response}");
    response["result"].clone()
}

fn assert_notes(session: &Value, frequencies: &[f64; 3], velocity: f64) {
    let notes = session["tracks"][0]["clips"][0]["notes"]
        .as_array()
        .unwrap();
    for (index, (note, frequency)) in notes.iter().zip(frequencies).enumerate() {
        assert_eq!(
            note["frequency_hz"].as_f64().unwrap().to_bits(),
            frequency.to_bits()
        );
        assert_eq!(
            note["start_frame"].as_u64().unwrap(),
            137 + index as u64 * 1237
        );
        assert_eq!(
            note["duration_frames"].as_u64().unwrap(),
            997 + index as u64 * 31
        );
        let expected_velocity = if index == 0 {
            velocity
        } else {
            0.731_246_819_731_24_f64
        };
        assert_eq!(
            note["velocity"].as_f64().unwrap().to_bits(),
            expected_velocity.to_bits()
        );
    }
}

#[test]
fn checked_replacement_velocity_edit_get_save_and_load_preserve_exact_notes() {
    let frequencies = [
        246.94165062806206_f64,
        97.99885899543733_f64,
        329.7129387412567_f64,
    ];
    let original_velocity = 0.731_246_819_731_24_f64;
    let mut session = json!({
        "schema_version":9,"sample_rate":48000,"tempo_milli_bpm":123456,
        "tracks":[{"id":"precise","mode":"sequenced","effects":[],
            "device":{"kind":"synth","waveform":"saw","gain":0.2,"attack_ms":8,"release_ms":90,"cutoff_hz":3000},
            "clips":[{"kind":"notes","id":"offgrid","start_frame":71,"length_frames":12000,
                "notes":frequencies.iter().enumerate().map(|(index,frequency)|json!({
                    "id":format!("n{index}"),"start_frame":137+index as u64*1237,
                    "duration_frames":997+index as u64*31,"frequency_hz":frequency,"velocity":original_velocity
                })).collect::<Vec<_>>()}]}]
    });
    let mut controller = Controller::default();
    let replaced = request(
        &mut controller,
        "session.replace",
        json!({"expected_revision":"0","session":session}),
    );
    assert_eq!(
        request(&mut controller, "session.inspect", json!({}))["revision"],
        "1"
    );
    assert_notes(&replaced, &frequencies, original_velocity);
    assert_notes(
        &request(&mut controller, "session.get", json!({})),
        &frequencies,
        original_velocity,
    );

    // The UI submits checked replacement for note edits. Change velocity alone:
    // frequency and off-grid timing must survive both parser passes untouched.
    let edited_velocity = 0.482_739_123_819_23_f64;
    session["tracks"][0]["clips"][0]["notes"][0]["velocity"] = json!(edited_velocity);
    let replaced = request(
        &mut controller,
        "session.replace",
        json!({"expected_revision":"1","session":session}),
    );
    assert_eq!(
        request(&mut controller, "session.inspect", json!({}))["revision"],
        "2"
    );
    assert_notes(&replaced, &frequencies, edited_velocity);
    assert_notes(
        &request(&mut controller, "session.get", json!({})),
        &frequencies,
        edited_velocity,
    );

    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("precise.json");
    request(
        &mut controller,
        "session.save",
        json!({"path":path.to_str().unwrap()}),
    );
    let disk: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    assert_notes(&disk, &frequencies, edited_velocity);
    request(
        &mut controller,
        "session.replace",
        json!({"expected_revision":"2","session":{"schema_version":1,"sample_rate":48000,"tracks":[]}}),
    );
    let loaded = request(
        &mut controller,
        "session.load",
        json!({"expected_revision":"3","path":path.to_str().unwrap()}),
    );
    assert_eq!(
        request(&mut controller, "session.inspect", json!({}))["revision"],
        "4"
    );
    assert_notes(&loaded, &frequencies, edited_velocity);
    assert_notes(
        &request(&mut controller, "session.get", json!({})),
        &frequencies,
        edited_velocity,
    );
}
