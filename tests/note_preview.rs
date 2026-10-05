use daw::control::Controller;
use serde_json::{Value, json};
use tempfile::tempdir;

fn call(c: &mut Controller, method: &str, params: Value) -> Value {
    serde_json::to_value(c.handle_line(
        &json!({"protocol_version":1,"id":"test","method":method,"params":params}).to_string(),
    ))
    .unwrap()
}
fn session(device: Value) -> Value {
    json!({"schema_version":11,"sample_rate":48000,"tempo_milli_bpm":120000,"tracks":[{
        "id":"lead","mode":"sequenced","device":device,"clips":[],
        "effects":[{"kind":"gain","id":"quiet","gain":0.5,"bypass":false}],"automation":[],
        "mixer":{"gain":0.6,"pan":1.0,"mute":true,"solo":true}}]})
}
fn setup(c: &mut Controller, device: Value) -> Value {
    assert_eq!(
        call(c, "session.replace", json!({"session":session(device)}))["ok"],
        true
    );
    call(c, "session.inspect", json!({}))["result"].clone()
}
fn preview(c: &mut Controller, path: &std::path::Path, frequency: f64, velocity: f64) -> Value {
    call(
        c,
        "note.preview",
        json!({"expected_revision":"1","track_id":"lead","frequency_hz":frequency,"velocity":velocity,"path":path}),
    )
}
#[test]
fn actual_preview_is_bounded_deterministic_and_preserves_saved_state_and_render() {
    let mut c = Controller::default();
    let before = setup(&mut c, json!({"kind":"sine","frequency_hz":220,"gain":0.4}));
    let dir = tempdir().unwrap();
    let render_before = dir.path().join("before.wav");
    assert_eq!(
        call(
            &mut c,
            "render",
            json!({"path":render_before,"seconds":0.5})
        )["ok"],
        true
    );
    let a = dir.path().join("a.wav");
    let b = dir.path().join("b.wav");
    assert_eq!(preview(&mut c, &a, 440.0, 0.5)["ok"], true);
    assert_eq!(preview(&mut c, &b, 440.0, 0.5)["ok"], true);
    assert_eq!(std::fs::read(&a).unwrap(), std::fs::read(&b).unwrap());
    let mut reader = hound::WavReader::open(&a).unwrap();
    assert_eq!(reader.spec().sample_rate, 48000);
    assert_eq!(reader.spec().channels, 2);
    assert_eq!(reader.duration(), 24000);
    let samples = reader
        .samples::<i16>()
        .map(Result::unwrap)
        .collect::<Vec<_>>();
    assert!(samples.chunks_exact(2).all(|frame| frame[0] == 0));
    assert!(samples.chunks_exact(2).any(|frame| frame[1].abs() > 100));
    assert!(samples[26000..].iter().all(|sample| *sample == 0));
    assert_eq!(call(&mut c, "session.inspect", json!({}))["result"], before);
    let after = dir.path().join("after.wav");
    assert_eq!(
        call(&mut c, "render", json!({"path":after,"seconds":0.5}))["ok"],
        true
    );
    assert_eq!(
        std::fs::read(render_before).unwrap(),
        std::fs::read(after).unwrap()
    );
    let silent = dir.path().join("silent.wav");
    assert_eq!(preview(&mut c, &silent, 440.0, 0.0)["ok"], true);
    assert!(
        hound::WavReader::open(silent)
            .unwrap()
            .samples::<i16>()
            .all(|s| s.unwrap() == 0)
    );
}
#[test]
fn preview_rejections_do_not_publish_or_change_revision() {
    let mut c = Controller::default();
    let before = setup(&mut c, json!({"kind":"sine","frequency_hz":220,"gain":0.4}));
    let dir = tempdir().unwrap();
    let path = dir.path().join("preview.wav");
    let base = json!({"expected_revision":"1","track_id":"lead","frequency_hz":440,"velocity":0.8,"path":path});
    for (field, value) in [
        ("expected_revision", json!("0")),
        ("expected_revision", json!("01")),
        ("track_id", json!("missing")),
        ("frequency_hz", json!(24000)),
        ("frequency_hz", json!(0)),
        ("velocity", json!(-1)),
        ("velocity", json!(true)),
        ("seconds", json!(100)),
    ] {
        let mut p = base.clone();
        p[field] = value;
        assert_eq!(call(&mut c, "note.preview", p)["ok"], false, "{field}");
        assert!(!path.exists());
        assert_eq!(call(&mut c, "session.inspect", json!({}))["result"], before);
    }
    std::fs::write(&path, b"keep").unwrap();
    assert_eq!(
        preview(&mut c, &path, 440.0, 0.8)["error"]["code"],
        "io_error"
    );
    assert_eq!(std::fs::read(path).unwrap(), b"keep");
}
#[test]
fn synth_and_drumkit_use_actual_saved_devices() {
    for (device, pitch) in [
        (
            json!({"kind":"synth","waveform":"saw","gain":0.2,"attack_ms":5,"release_ms":50,"cutoff_hz":4000}),
            440.0,
        ),
        (
            json!({"kind":"drumkit","kit_id":"factory-v1","gain":0.2}),
            65.40639132514966,
        ),
    ] {
        let mut c = Controller::default();
        let drum = device["kind"] == "drumkit";
        setup(&mut c, device);
        let dir = tempdir().unwrap();
        let path = dir.path().join("preview.wav");
        let result = preview(&mut c, &path, pitch, 0.8);
        assert_eq!(result["ok"], true, "{result}");
        let samples = hound::WavReader::open(path)
            .unwrap()
            .samples::<i16>()
            .map(Result::unwrap)
            .collect::<Vec<_>>();
        assert!(samples[..24000].iter().any(|s| *s != 0));
        // The clip includes the source's release; its note gate still ends at .25s.
        assert!(samples[24000..26400].iter().any(|s| *s != 0));
        if drum {
            assert!(
                samples[43200..47520].iter().any(|s| *s != 0),
                "kick must retain its .5s one-shot tail"
            );
            assert!(samples[samples.len() - 2..].iter().all(|s| *s == 0));
        } else {
            assert!(
                samples[30000..].iter().all(|s| *s == 0),
                "50ms synth release must finish after gate-off"
            );
        }
    }
}

#[test]
fn audition_does_not_touch_other_tracks_or_project_relative_assets() {
    let dir = tempdir().unwrap();
    let audio = dir.path().join("asset.wav");
    let mut wav = hound::WavWriter::create(
        audio,
        hound::WavSpec {
            channels: 1,
            sample_rate: 48000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        },
    )
    .unwrap();
    for frame in 0..24000 {
        wav.write_sample::<i16>(if frame % 100 < 50 { 500 } else { -500 })
            .unwrap();
    }
    wav.finalize().unwrap();
    let mut saved = session(json!({"kind":"sine","frequency_hz":220,"gain":0.4}));
    saved["tracks"].as_array_mut().unwrap().push(json!({
        "id":"audio","mode":"sequenced","device":{"kind":"audio","gain":0.2},
        "clips":[{"kind":"audio","id":"pcm","start_frame":0,"length_frames":24000,
        "source_path":"asset.wav","source_offset_frames":0,"gain":1.0}],
        "effects":[],"automation":[],"mixer":{"gain":1.0,"pan":0,"mute":false,"solo":false}}));
    let project = dir.path().join("session.json");
    std::fs::write(&project, serde_json::to_vec(&saved).unwrap()).unwrap();
    let mut c = Controller::default();
    let loaded = call(&mut c, "session.load", json!({"path":project}));
    assert_eq!(loaded["ok"], true, "{loaded}");
    let before = call(&mut c, "session.inspect", json!({}))["result"].clone();
    let a = dir.path().join("before.wav");
    let b = dir.path().join("after.wav");
    assert_eq!(
        call(&mut c, "render", json!({"path":a,"seconds":0.5}))["ok"],
        true
    );
    assert_eq!(
        preview(&mut c, &dir.path().join("note.wav"), 440.0, 0.8)["ok"],
        true
    );
    assert_eq!(call(&mut c, "session.inspect", json!({}))["result"], before);
    assert_eq!(
        call(&mut c, "render", json!({"path":b,"seconds":0.5}))["ok"],
        true
    );
    assert_eq!(std::fs::read(a).unwrap(), std::fs::read(b).unwrap());
}
