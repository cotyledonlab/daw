use daw::{control::Controller, engine::Engine, session::Session};
use serde_json::{Value, json};
fn call(c: &mut Controller, method: &str, params: Value) -> Value {
    serde_json::to_value(c.handle_line(
        &json!({"protocol_version":1,"id":"test","method":method,"params":params}).to_string(),
    ))
    .unwrap()
}
fn result(c: &mut Controller, method: &str, params: Value) -> Value {
    let response = call(c, method, params);
    assert_eq!(response["ok"], true, "{response}");
    response["result"].clone()
}
fn song() -> Value {
    json!({"schema_version":11,"sample_rate":48000,"tempo_milli_bpm":120000,"tracks":[{"id":"lead","mode":"sequenced","device":{"kind":"sine","frequency_hz":440,"gain":0.2},"effects":[],"mixer":{"gain":1.0,"pan":0.0,"mute":false,"solo":false},"clips":[{"id":"c","kind":"notes","start_frame":0,"length_frames":48000,"notes":[{"id":"n","start_frame":0,"duration_frames":24000,"frequency_hz":440,"velocity":0.8}]}]}]})
}
#[test]
fn streaming_matches_offline_dsp_and_keeps_the_saved_project_exact() {
    let mut c = Controller::default();
    let song = song();
    result(&mut c, "session.replace", json!({"session":song}));
    let before = result(&mut c, "session.inspect", json!({}));
    let id = result(
        &mut c,
        "stream.play",
        json!({"expected_revision":before["revision"]}),
    )["stream_id"]
        .clone();
    let session: Session = serde_json::from_value(song).unwrap();
    let mut engine = Engine::prepare(&session).unwrap();
    for _ in 0..4 {
        let block = result(&mut c, "stream.read", json!({"stream_id":id,"frames":4096}));
        let mut expected = vec![[0.0; 2]; 4096];
        engine.render_block(&mut expected);
        let actual: Vec<[f64; 2]> = serde_json::from_value(block["pcm"].clone()).unwrap();
        assert_eq!(actual, expected);
    }
    assert_eq!(result(&mut c, "session.inspect", json!({})), before);
}
#[test]
fn ownership_pause_seek_loop_and_rejected_updates_are_transactional() {
    let mut c = Controller::default();
    let mut song = song();
    result(&mut c, "session.replace", json!({"session":song}));
    let id = result(&mut c, "stream.play", json!({"expected_revision":"1"}))["stream_id"].clone();
    for p in [
        json!({"stream_id":"wrong","frames":4096}),
        json!({"stream_id":id,"frames":100000}),
    ] {
        assert_eq!(call(&mut c, "stream.read", p)["ok"], false);
    }
    result(&mut c, "stream.pause", json!({"stream_id":id}));
    assert_eq!(
        call(&mut c, "stream.read", json!({"stream_id":id,"frames":4096}))["ok"],
        false
    );
    result(&mut c, "stream.seek", json!({"stream_id":id,"frame":1024}));
    result(
        &mut c,
        "stream.loop",
        json!({"stream_id":id,"region":{"start_frame":1024,"end_frame":2048}}),
    );
    result(&mut c, "stream.resume", json!({"stream_id":id}));
    let block = result(&mut c, "stream.read", json!({"stream_id":id,"frames":4096}));
    assert_eq!(block["end_frame"], 2048);
    let before = result(&mut c, "session.inspect", json!({}));
    song["tracks"][0]["id"] = json!("changed");
    assert_eq!(
        call(
            &mut c,
            "session.update_live",
            json!({"expected_revision":"1","session":song})
        )["ok"],
        false
    );
    assert_eq!(result(&mut c, "session.inspect", json!({})), before);
    let stopped = result(&mut c, "stream.stop", json!({"stream_id":id}));
    assert_eq!(stopped["state"], "stopped");
    let new = result(&mut c, "stream.play", json!({"expected_revision":"1"}));
    assert_ne!(new["stream_id"], id);
    assert_eq!(
        call(&mut c, "stream.stop", json!({"stream_id":id}))["ok"],
        false
    );
}
#[test]
fn live_mix_update_reaches_next_block_and_saved_state_once() {
    let mut c = Controller::default();
    let mut song = song();
    result(&mut c, "session.replace", json!({"session":song}));
    let id = result(&mut c, "stream.play", json!({"expected_revision":"1"}))["stream_id"].clone();
    result(&mut c, "stream.read", json!({"stream_id":id,"frames":4096}));
    song["tracks"][0]["mixer"]["mute"] = json!(true);
    result(
        &mut c,
        "session.update_live",
        json!({"expected_revision":"1","session":song}),
    );
    let block = result(&mut c, "stream.read", json!({"stream_id":id,"frames":4096}));
    assert_eq!(block["revision"], "2");
    assert_eq!(block["start_frame"], 4096);
    assert!(
        block["pcm"].as_array().unwrap()[64..]
            .iter()
            .all(|f| f == &json!([0.0, 0.0]))
    );
    assert_eq!(
        call(
            &mut c,
            "session.update_live",
            json!({"expected_revision":"1","session":song})
        )["ok"],
        false
    );
}
