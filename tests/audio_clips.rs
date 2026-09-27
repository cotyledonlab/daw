use daw::{
    control::{Controller, PROTOCOL_VERSION},
    engine::Engine,
    session::Session,
};
use serde_json::{Value, json};
use std::{fs, path::Path};
use tempfile::tempdir;

fn write_wav(path: &Path, rate: u32, channels: u16, frames: &[[i16; 2]]) {
    let spec = hound::WavSpec {
        channels,
        sample_rate: rate,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut writer = hound::WavWriter::create(path, spec).unwrap();
    for frame in frames {
        for channel in 0..channels as usize {
            writer
                .write_sample(frame.get(channel).copied().unwrap_or(0))
                .unwrap();
        }
    }
    writer.finalize().unwrap();
}

fn asset_session(root: &Path, clips: Value, track_gain: f64) -> Session {
    let mut session: Session = serde_json::from_value(json!({
        "schema_version":2,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"audio","device":{"kind":"audio","gain":track_gain},"mode":"sequenced","clips":clips}]
    })).unwrap();
    session.asset_root = Some(root.to_path_buf());
    session
}

fn clip(id: &str, start: u64, len: u64, offset: u64, gain: f64) -> Value {
    json!({"kind":"audio","id":id,"start_frame":start,"length_frames":len,"source_path":"source.wav","source_offset_frames":offset,"gain":gain})
}

fn impulse_asset(dir: &Path) {
    write_wav(
        &dir.join("source.wav"),
        8000,
        1,
        &[[8192, 0], [16384, 0], [-8192, 0], [24576, 0]],
    );
}

fn render(session: &Session, count: usize) -> Vec<[f64; 2]> {
    let mut engine = Engine::prepare(session).unwrap();
    let mut out = vec![[0.0; 2]; count];
    engine.render_block(&mut out);
    out
}

#[test]
fn mono_source_obeys_clip_and_track_gain_offsets_and_source_range() {
    let dir = tempdir().unwrap();
    impulse_asset(dir.path());
    let session = asset_session(dir.path(), json!([clip("c", 2, 2, 1, 0.5)]), 0.5);
    let out = render(&session, 6);
    assert_eq!(
        out,
        vec![
            [0.0; 2],
            [0.0; 2],
            [0.125; 2],
            [-0.0625; 2],
            [0.0; 2],
            [0.0; 2]
        ]
    );
}

#[test]
fn stereo_source_channels_are_preserved_and_adjacent_ranges_are_half_open() {
    let dir = tempdir().unwrap();
    write_wav(
        &dir.path().join("source.wav"),
        8000,
        2,
        &[[8192, -8192], [16384, -16384], [24576, -24576]],
    );
    let session = asset_session(
        dir.path(),
        json!([clip("a", 0, 1, 0, 1.0), clip("b", 1, 2, 1, 1.0)]),
        1.0,
    );
    assert_eq!(
        render(&session, 4),
        vec![[0.25, -0.25], [0.5, -0.5], [0.75, -0.75], [0.0, 0.0]]
    );
}

#[test]
fn overlaps_mix_and_clip_independently_on_both_channels() {
    let dir = tempdir().unwrap();
    write_wav(&dir.path().join("source.wav"), 8000, 2, &[[24576, -24576]]);
    let session = asset_session(
        dir.path(),
        json!([clip("a", 0, 1, 0, 1.0), clip("b", 0, 1, 0, 1.0)]),
        1.0,
    );
    assert_eq!(render(&session, 1), vec![[1.0, -1.0]]);
}

#[test]
fn uneven_blocks_match_single_block_exactly_and_assets_are_preloaded() {
    let dir = tempdir().unwrap();
    impulse_asset(dir.path());
    let session = asset_session(dir.path(), json!([clip("c", 1, 3, 0, 0.75)]), 0.8);
    let expected = render(&session, 7);
    let mut engine = Engine::prepare(&session).unwrap();
    fs::remove_file(dir.path().join("source.wav")).unwrap();
    let mut actual = vec![[0.0; 2]; 7];
    let mut pos = 0;
    for size in [1, 3, 1, 2] {
        engine.render_block(&mut actual[pos..pos + size]);
        pos += size;
    }
    assert_eq!(pos, 7);
    assert_eq!(actual, expected);
}

#[test]
fn preparation_rejects_missing_bad_rate_float_and_unsupported_channel_assets() {
    let dir = tempdir().unwrap();
    let session = asset_session(dir.path(), json!([clip("c", 0, 1, 0, 1.0)]), 1.0);
    assert!(Engine::prepare(&session).is_err());
    write_wav(&dir.path().join("source.wav"), 16000, 1, &[[1, 0]]);
    assert!(Engine::prepare(&session).is_err());
    write_wav(&dir.path().join("source.wav"), 8000, 1, &[[1, 0]]);
    let out_of_range = asset_session(dir.path(), json!([clip("c", 0, 2, 1, 1.0)]), 1.0);
    assert!(Engine::prepare(&out_of_range).is_err());
    write_wav(&dir.path().join("source.wav"), 8000, 3, &[[1, 2]]);
    assert!(Engine::prepare(&session).is_err());
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: 8000,
        bits_per_sample: 32,
        sample_format: hound::SampleFormat::Float,
    };
    let mut writer = hound::WavWriter::create(dir.path().join("source.wav"), spec).unwrap();
    writer.write_sample(0.5f32).unwrap();
    writer.finalize().unwrap();
    assert!(Engine::prepare(&session).is_err());
}

fn request(controller: &mut Controller, id: &str, method: &str, params: Value) -> Value {
    serde_json::to_value(
        controller.handle_line(
            &json!({"protocol_version":PROTOCOL_VERSION,"id":id,"method":method,"params":params})
                .to_string(),
        ),
    )
    .unwrap()
}
fn code(response: &Value) -> &str {
    response["error"]["code"].as_str().unwrap()
}
fn path(path: &Path) -> &str {
    path.to_str().unwrap()
}
fn session_json(source_path: &str) -> Value {
    json!({"schema_version":2,"sample_rate":8000,"tempo_milli_bpm":120000,"tracks":[{"id":"audio","device":{"kind":"audio","gain":1.0},"mode":"sequenced","clips":[{"kind":"audio","id":"c","start_frame":0,"length_frames":1,"source_path":source_path,"source_offset_frames":0,"gain":1.0}]}]})
}

#[test]
fn controller_load_resolves_assets_relative_to_session_and_save_reload_preserves_project_root() {
    let dir = tempdir().unwrap();
    let project = dir.path().join("project");
    fs::create_dir(&project).unwrap();
    impulse_asset(&project);
    let file = project.join("song.json");
    fs::write(
        &file,
        serde_json::to_vec(&session_json("source.wav")).unwrap(),
    )
    .unwrap();
    let mut c = Controller::default();
    let loaded = request(&mut c, "load", "session.load", json!({"path":path(&file)}));
    assert_eq!(loaded["ok"], true, "{loaded}");
    let inspected = request(&mut c, "inspect", "session.inspect", json!({}));
    assert_eq!(inspected["result"]["revision"], "1");
    let save = project.join("copy.json");
    let saved = request(&mut c, "save", "session.save", json!({"path":path(&save)}));
    assert_eq!(saved["ok"], true, "{saved}");
    let reload = request(
        &mut c,
        "reload",
        "session.load",
        json!({"path":path(&save)}),
    );
    assert_eq!(reload["ok"], true, "{reload}");
    let output = project.join("render.wav");
    let rendered = request(
        &mut c,
        "render",
        "render",
        json!({"path":path(&output),"seconds":0.001}),
    );
    assert_eq!(rendered["ok"], true, "{rendered}");
    assert!(output.is_file());
}

#[test]
fn asset_failures_preserve_active_session_revision_and_replace_inherits_project_root() {
    let dir = tempdir().unwrap();
    let project = dir.path().join("project");
    fs::create_dir(&project).unwrap();
    impulse_asset(&project);
    let good = project.join("good.json");
    fs::write(
        &good,
        serde_json::to_vec(&session_json("source.wav")).unwrap(),
    )
    .unwrap();
    let missing = project.join("missing.json");
    fs::write(
        &missing,
        serde_json::to_vec(&session_json("absent.wav")).unwrap(),
    )
    .unwrap();
    let mut c = Controller::default();
    assert_eq!(
        request(&mut c, "load", "session.load", json!({"path":path(&good)}))["ok"],
        true
    );
    let failure = request(
        &mut c,
        "bad-load",
        "session.load",
        json!({"path":path(&missing)}),
    );
    assert_eq!(code(&failure), "asset_error");
    assert_eq!(
        request(&mut c, "inspect", "session.inspect", json!({}))["result"]["revision"],
        "1"
    );
    fs::remove_file(project.join("source.wav")).unwrap();
    let render_path = project.join("failed-render.wav");
    let render_failure = request(
        &mut c,
        "bad-render",
        "render",
        json!({"path":path(&render_path),"seconds":0.001}),
    );
    assert_eq!(code(&render_failure), "asset_error");
    assert!(!render_path.exists());
    impulse_asset(&project);
    let replacement = request(
        &mut c,
        "replace",
        "session.replace",
        json!({"session":session_json("source.wav")}),
    );
    assert_eq!(replacement["ok"], true, "{replacement}");
    let output = project.join("replacement.wav");
    let rendered = request(
        &mut c,
        "render",
        "render",
        json!({"path":path(&output),"seconds":0.001}),
    );
    assert_eq!(rendered["ok"], true, "{rendered}");
    let bad_replace = request(
        &mut c,
        "bad-replace",
        "session.replace",
        json!({"session":session_json("absent.wav")}),
    );
    assert_eq!(code(&bad_replace), "asset_error");
    assert_eq!(
        request(&mut c, "inspect", "session.inspect", json!({}))["result"]["revision"],
        "2"
    );
    let outside = dir.path().join("outside.json");
    let failed_save = request(
        &mut c,
        "outside-save",
        "session.save",
        json!({"path":path(&outside)}),
    );
    assert_eq!(code(&failed_save), "invalid_params");
}

#[test]
fn integer_pcm_depths_are_normalized_and_truncated_data_fails() {
    let dir = tempdir().unwrap();
    for bits in [16, 24, 32] {
        let file = dir.path().join("source.wav");
        let mut writer = hound::WavWriter::create(
            &file,
            hound::WavSpec {
                channels: 2,
                sample_rate: 8000,
                bits_per_sample: bits,
                sample_format: hound::SampleFormat::Int,
            },
        )
        .unwrap();
        let half = 1_i32 << (bits - 2);
        writer.write_sample(half).unwrap();
        writer.write_sample(-half).unwrap();
        writer.finalize().unwrap();
        let session = asset_session(dir.path(), json!([clip("c", 0, 1, 0, 1.0)]), 1.0);
        assert_eq!(render(&session, 1), vec![[0.5, -0.5]]);
        let mut buffer =
            daw::audio_buffer::PlaybackBuffer::prepare(&session, 8000, 1, 0.001, 1.0).unwrap();
        let mut mono = [1.0; 1];
        buffer.fill(&mut mono, |sample| sample).unwrap();
        assert_eq!(mono, [0.0]);
        let mut bytes = fs::read(&file).unwrap();
        bytes.pop();
        fs::write(&file, bytes).unwrap();
        assert!(Engine::prepare(&session).is_err());
    }
}

#[test]
fn duplicate_sources_share_buffers_and_oversized_files_are_rejected() {
    let dir = tempdir().unwrap();
    impulse_asset(dir.path());
    let session = asset_session(
        dir.path(),
        json!([clip("a", 0, 1, 0, 1.0), clip("b", 1, 1, 1, 1.0)]),
        1.0,
    );
    let prepared = daw::assets::prepare(&session).unwrap();
    assert!(std::sync::Arc::ptr_eq(
        &prepared[0].frames,
        &prepared[1].frames
    ));
    fs::OpenOptions::new()
        .write(true)
        .open(dir.path().join("source.wav"))
        .unwrap()
        .set_len(daw::assets::MAX_FILE_BYTES + 1)
        .unwrap();
    assert!(Engine::prepare(&session).is_err());
}

#[cfg(unix)]
#[test]
fn symlink_to_asset_outside_project_is_rejected() {
    let project = tempdir().unwrap();
    let outside = tempdir().unwrap();
    impulse_asset(outside.path());
    std::os::unix::fs::symlink(
        outside.path().join("source.wav"),
        project.path().join("source.wav"),
    )
    .unwrap();
    let session = asset_session(project.path(), json!([clip("c", 0, 1, 0, 1.0)]), 1.0);
    assert!(Engine::prepare(&session).is_err());
}
