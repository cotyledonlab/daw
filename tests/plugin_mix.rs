use daw::{plugin_render, session::Session};
use serde_json::{Value, json};
use std::io::Cursor;

fn bypassed_plugin() -> Value {
    json!({
        "kind":"vst3", "id":"silent-plugin", "bypass":true,
        "bundle_path":"/tmp/no-host-needed.vst3",
        "class_id":"0123456789ABCDEF0123456789ABCDEF",
        "state_hex":"", "controller_state_hex":"", "parameters":[]
    })
}

fn gain(id: &str, amount: f64) -> Value {
    json!({"kind":"gain", "id":id, "gain":amount, "bypass":false})
}

fn session(tracks: Vec<Value>) -> Session {
    serde_json::from_value(json!({
        "schema_version":4, "sample_rate":48000, "tempo_milli_bpm":120000,
        "tracks":tracks
    }))
    .unwrap()
}

fn tone(id: &str, source_gain: f64, trim: f64) -> Value {
    json!({
        "id":id, "mode":"continuous", "clips":[],
        "device":{"kind":"sine", "frequency_hz":12000.0, "gain":source_gain},
        "effects":[gain("boost", 4.0), bypassed_plugin(), gain("trim", trim)]
    })
}

fn encoded(session: &Session, seconds: f64) -> (Vec<i16>, u64) {
    let prepared = plugin_render::prepare(session, seconds).unwrap();
    let mut cursor = Cursor::new(Vec::new());
    let report = prepared.encode(&mut cursor).unwrap();
    cursor.set_position(0);
    let samples = hound::WavReader::new(cursor)
        .unwrap()
        .samples::<i16>()
        .map(Result::unwrap)
        .collect();
    (samples, report.clipped_frames)
}

#[test]
fn serial_chain_preserves_headroom_and_master_clips_the_summed_mix_once() {
    // The first chain rises to 3.0 between effects, then returns to 0.75.
    // That intermediate value must survive without clipping before its trim.
    let first_only = session(vec![tone("first", 0.75, 0.25)]);
    let (first_samples, first_clipped) = encoded(&first_only, 0.001);
    assert_eq!(first_clipped, 0);
    assert_eq!(
        &first_samples[..8],
        &[0, 0, 24575, 24575, 0, 0, -24575, -24575]
    );

    // A second track is summed after its own chain. Its trim changes at frame
    // two, and the master clips only the two over-full frames in each cycle.
    let mut second = tone("second", 0.5, 0.5);
    second["automation"] = json!([{
        "effect_id":"trim", "parameter":"gain", "interpolation":"step",
        "points":[{"frame":2,"value":0.25}]
    }]);
    let mix = session(vec![tone("first", 0.75, 0.25), second]);
    let (samples, clipped) = encoded(&mix, 0.001);
    assert_eq!(clipped, 24);
    assert_eq!(&samples[..8], &[0, 0, 32767, 32767, 0, 0, -32767, -32767]);
}

#[test]
fn plugin_render_accepts_exact_ten_second_bound_and_rejects_longer_duration() {
    let s = session(vec![tone("bounded", 0.1, 0.25)]);
    let prepared = plugin_render::prepare(&s, 10.0).unwrap();
    let report = prepared.encode(Cursor::new(Vec::new())).unwrap();
    assert_eq!(report.frames, 480_000);
    assert!(plugin_render::prepare(&s, 10.001).is_err());

    let mut wrong_rate = s;
    wrong_rate.sample_rate = 44_100;
    assert!(plugin_render::prepare(&wrong_rate, 1.0).is_err());
}
