use daw::{
    audio_buffer::PlaybackBuffer, control::Controller, engine::Engine, render, session::Session,
};
use serde_json::{Value, json};
use std::io::{Cursor, Seek};

fn musical_session() -> Session {
    serde_json::from_value(json!({"schema_version":10,"sample_rate":8000,"tempo_milli_bpm":120000,"tracks":[
        {"id":"synth","mode":"sequenced","effects":[{"kind":"gain","id":"trim","gain":0.75,"bypass":false}],"mixer":{"gain":0.8,"pan":-0.25,"mute":false,"solo":false},
        "device":{"kind":"synth","waveform":"saw","gain":0.2,"attack_ms":8,"release_ms":90,"cutoff_hz":2000},
        "clips":[{"kind":"notes","id":"song","start_frame":0,"length_frames":1440000,"notes":[
            {"id":"begin","start_frame":0,"duration_frames":1000,"frequency_hz":440,"velocity":0.8},
            {"id":"end","start_frame":1438000,"duration_frames":1000,"frequency_hz":660,"velocity":0.8}]}]}
    ]})).unwrap()
}

#[test]
fn three_minute_musical_export_streams_exact_length_and_preserves_end_events() {
    let session = musical_session();
    let mut output = tempfile::tempfile().unwrap();
    let started = std::time::Instant::now();
    let report = render::render(&session, 180.0, &mut output).unwrap();
    eprintln!(
        "180-second built-in musical render: {:?}",
        started.elapsed()
    );
    assert_eq!(report.frames, 1_440_000);
    assert_eq!(output.metadata().unwrap().len(), 44 + report.frames * 4);
    output.rewind().unwrap();
    let mut reader = hound::WavReader::new(output).unwrap();
    assert_eq!(reader.duration(), 1_440_000);
    assert!(
        reader
            .samples::<i16>()
            .take(4000)
            .any(|sample| sample.unwrap() != 0)
    );
    reader.seek(1_438_000).unwrap();
    assert!(
        reader
            .samples::<i16>()
            .take(3000)
            .any(|sample| sample.unwrap() != 0)
    );
}

#[test]
fn offline_boundary_and_native_duration_are_independent() {
    let session = musical_session();
    assert!(render::validate_render_duration(&session, 180.0).is_ok());
    for invalid in [0.0009, 180.001, f64::NAN, f64::INFINITY] {
        assert!(render::validate_render_duration(&session, invalid).is_err());
        let mut output = Cursor::new(Vec::new());
        assert!(render::render(&session, invalid, &mut output).is_err());
        assert!(output.into_inner().is_empty());
    }
    assert!(PlaybackBuffer::prepare(&session, 8000, 2, 60.0, 1.0).is_ok());
    for seconds in [60.001, 180.0] {
        assert!(PlaybackBuffer::prepare(&session, 8000, 2, seconds, 1.0).is_err());
    }
    let mut engine = Engine::prepare(&session).unwrap();
    let mut output = Cursor::new(Vec::new());
    assert!(render::render_prepared(&mut engine, 8000, 180.001, &mut output).is_err());
    assert!(output.into_inner().is_empty());
}

#[test]
fn composition_preserves_foreign_and_prepared_runtime_caps_before_worker_start() {
    for schema in 1..=10 {
        let mut value = json!({"schema_version":schema,"sample_rate":8000,"tracks":[{"id":"tone","device":{"kind":"sine","frequency_hz":440,"gain":0.1}}]});
        if schema >= 2 {
            value["tempo_milli_bpm"] = json!(120000);
            value["tracks"][0]["mode"] = json!("continuous");
            value["tracks"][0]["clips"] = json!([]);
        }
        if schema >= 3 {
            value["tracks"][0]["effects"] = json!([]);
        }
        let session: Session = serde_json::from_value(value).unwrap();
        session.validate().unwrap();
        assert_eq!(render::max_render_seconds(&session), 180.0);
    }
    let runtime: Session = serde_json::from_value(json!({"schema_version":7,"sample_rate":8000,"tempo_milli_bpm":120000,"tracks":[{"id":"cs","mode":"continuous","clips":[],"effects":[],"device":{"kind":"csound","program":"invalid program which must never reach runtime","duration_frames":8000,"gain":0.5,"controls":[]}}]})).unwrap();
    assert_eq!(render::max_render_seconds(&runtime), 60.0);
    let mut output = Cursor::new(Vec::new());
    assert!(
        render::render(&runtime, 60.001, &mut output)
            .unwrap_err()
            .contains("between 0.001 and 60")
    );
    assert!(output.into_inner().is_empty());
    let plugin: Session = serde_json::from_value(json!({"schema_version":4,"sample_rate":48000,"tempo_milli_bpm":120000,"tracks":[{"id":"plugin","mode":"continuous","clips":[],"device":{"kind":"sine","frequency_hz":440,"gain":0.1},"effects":[{"kind":"vst3","id":"foreign","bundle_path":"/missing.vst3","class_id":"00000000000000000000000000000000","bypass":false,"parameters":[],"state_hex":"","controller_state_hex":""}]}]})).unwrap();
    assert_eq!(render::max_render_seconds(&plugin), 10.0);
    assert!(render::validate_render_duration(&plugin, 10.0).is_ok());
    assert!(render::validate_render_duration(&plugin, 10.001).is_err());
}

fn request(controller: &mut Controller, method: &str, params: Value) -> Value {
    serde_json::to_value(controller.handle_line(
        &json!({"protocol_version":1,"id":"song","method":method,"params":params}).to_string(),
    ))
    .unwrap()
}

#[test]
fn control_render_accepts_three_minutes_and_rejects_excess_before_creating_file() {
    let directory = tempfile::tempdir().unwrap();
    let mut controller = Controller::default();
    let session: Session =
        serde_json::from_value(json!({"schema_version":1,"sample_rate":8000,"tracks":[]})).unwrap();
    assert_eq!(
        request(
            &mut controller,
            "session.replace",
            json!({"session":session})
        )["ok"],
        true
    );
    let path = directory.path().join("song.wav");
    let response = request(
        &mut controller,
        "render",
        json!({"path":path,"seconds":180}),
    );
    assert_eq!(response["ok"], true, "{response}");
    assert_eq!(response["result"]["frames"], 1_440_000);
    let rejected = directory.path().join("too-long.wav");
    assert_eq!(
        request(
            &mut controller,
            "render",
            json!({"path":rejected,"seconds":180.001})
        )["error"]["code"],
        "invalid_params"
    );
    assert!(!rejected.exists());
}
