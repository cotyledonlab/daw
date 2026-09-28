use daw::session::{
    Clip, ClipKind, DEFAULT_TEMPO_MILLI_BPM, Device, MAX_FRAME, MAX_VOICES, Note, NoteClip,
    Session, Track, TrackMode, envelope_frames, tick_to_frame,
};
use serde_json::{Value, json};

fn note(id: &str, start_frame: u64, duration_frames: u64) -> Note {
    Note {
        id: id.into(),
        start_frame,
        duration_frames,
        frequency_hz: 440.0,
        velocity: 0.5,
    }
}

fn track(mode: TrackMode, clips: Vec<NoteClip>) -> Track {
    Track {
        id: "t".into(),
        device: Device::Sine {
            frequency_hz: 220.0,
            gain: 0.5,
        },
        mode: Some(mode),
        clips: Some(clips.into_iter().map(Clip::Notes).collect()),
        effects: None,
    }
}

fn clip(id: &str, start_frame: u64, length_frames: u64, notes: Vec<Note>) -> NoteClip {
    NoteClip {
        kind: ClipKind::Notes,
        id: id.into(),
        start_frame,
        length_frames,
        notes,
    }
}

fn v2(tracks: Vec<Track>) -> Session {
    Session {
        schema_version: 2,
        sample_rate: 8_000,
        tempo_milli_bpm: Some(DEFAULT_TEMPO_MILLI_BPM),
        tracks,
        asset_root: None,
    }
}

fn v2_json() -> Value {
    json!({
        "schema_version": 2,
        "sample_rate": 48000,
        "tempo_milli_bpm": 120000,
        "tracks": [{
            "id": "lead", "device": {"kind":"sine", "frequency_hz":440.0,"gain":0.15},
            "mode":"sequenced", "clips":[{"kind":"notes", "id":"c", "start_frame":0,
                "length_frames":100, "notes":[{"id":"n", "start_frame":0, "duration_frames":10,
                    "frequency_hz":660.0,"velocity":0.8}]}]
        }]
    })
}

fn audio_track(mode: TrackMode, clips: Vec<daw::session::AudioClip>) -> Track {
    Track {
        id: "audio".into(),
        device: Device::Audio { gain: 0.5 },
        mode: Some(mode),
        clips: Some(clips.into_iter().map(Clip::Audio).collect()),
        effects: None,
    }
}

fn audio_clip() -> daw::session::AudioClip {
    daw::session::AudioClip {
        kind: daw::session::AudioClipKind::Audio,
        id: "a".into(),
        start_frame: 0,
        length_frames: 100,
        source_path: "assets/voice.wav".into(),
        source_offset_frames: 0,
        gain: 1.0,
    }
}

#[test]
fn audio_schema_is_strict_and_counts_audio_lifetimes() {
    v2(vec![audio_track(TrackMode::Sequenced, vec![audio_clip()])])
        .validate()
        .unwrap();
    assert!(
        v2(vec![audio_track(TrackMode::Continuous, vec![])])
            .validate()
            .is_err()
    );
    assert!(
        v2(vec![track(
            TrackMode::Sequenced,
            vec![clip("n", 0, 2, vec![])]
        )])
        .validate()
        .is_ok()
    );
    let mut wrong_device = audio_track(TrackMode::Sequenced, vec![audio_clip()]);
    wrong_device.device = Device::Sine {
        frequency_hz: 220.0,
        gain: 0.5,
    };
    assert!(v2(vec![wrong_device]).validate().is_err());

    let mut bad = audio_clip();
    bad.source_path = "../secret.wav".into();
    assert!(
        v2(vec![audio_track(TrackMode::Sequenced, vec![bad])])
            .validate()
            .is_err()
    );
    let mut bad = audio_clip();
    bad.gain = f64::NAN;
    assert!(
        v2(vec![audio_track(TrackMode::Sequenced, vec![bad])])
            .validate()
            .is_err()
    );
    let mut bad = audio_clip();
    bad.source_offset_frames = MAX_FRAME;
    assert!(
        v2(vec![audio_track(TrackMode::Sequenced, vec![bad])])
            .validate()
            .is_err()
    );

    let mut legacy_audio = v2(vec![audio_track(TrackMode::Sequenced, vec![])]);
    legacy_audio.schema_version = 1;
    legacy_audio.tempo_milli_bpm = None;
    legacy_audio.tracks[0].mode = None;
    legacy_audio.tracks[0].clips = None;
    assert!(legacy_audio.validate().is_err());
    assert!(
        serde_json::from_value::<Session>(json!({
            "schema_version":2,"sample_rate":48000,"tempo_milli_bpm":120000,
            "asset_root":"/tmp","tracks":[]
        }))
        .is_err()
    );
}

#[test]
fn v1_migration_shape_remains_valid_but_timeline_fields_are_rejected() {
    let legacy = json!({"schema_version":1,"sample_rate":48000,"tracks":[{
        "id":"tone","device":{"kind":"sine","frequency_hz":440.0,"gain":0.15}
    }]});
    let session: Session = serde_json::from_value(legacy.clone()).unwrap();
    session.validate().unwrap();
    let mut upgraded = legacy;
    upgraded["schema_version"] = json!(2);
    upgraded["tempo_milli_bpm"] = json!(120000);
    upgraded["tracks"][0]["mode"] = json!("continuous");
    upgraded["tracks"][0]["clips"] = json!([]);
    serde_json::from_value::<Session>(upgraded.clone())
        .unwrap()
        .validate()
        .unwrap();
    upgraded["schema_version"] = json!(1);
    assert!(
        serde_json::from_value::<Session>(upgraded)
            .unwrap()
            .validate()
            .is_err()
    );
}

#[test]
fn schema_v2_requires_fields_rejects_null_and_unknown_fields() {
    let valid = v2_json();
    serde_json::from_value::<Session>(valid.clone())
        .unwrap()
        .validate()
        .unwrap();
    let mut absent = valid.clone();
    absent.as_object_mut().unwrap().remove("tempo_milli_bpm");
    assert!(
        serde_json::from_value::<Session>(absent)
            .unwrap()
            .validate()
            .is_err()
    );
    let mut null = valid.clone();
    null["tempo_milli_bpm"] = Value::Null;
    assert!(serde_json::from_value::<Session>(null).is_err());
    let mut null_track = valid.clone();
    null_track["tracks"][0]["mode"] = Value::Null;
    assert!(serde_json::from_value::<Session>(null_track).is_err());
    let mut unknown = valid;
    unknown["tracks"][0]["clips"][0]["surprise"] = json!(true);
    assert!(serde_json::from_value::<Session>(unknown).is_err());
}

#[test]
fn tempo_fixtures_and_envelope_rounding_are_exact() {
    for (rate, tempo, ticks, frames) in [
        (48_000, 120_000, 0, 0),
        (48_000, 120_000, 960, 24_000),
        (48_000, 120_000, 1920, 48_000),
        (44_100, 120_000, 1, 23),
        (44_100, 120_000, 2, 46),
        (44_100, 120_000, 960, 22_050),
        (44_100, 120_000, 100, 2297),
        (48_000, 96_000, 2, 63),
    ] {
        assert_eq!(tick_to_frame(ticks, rate, tempo).unwrap(), frames);
    }
    assert_eq!(envelope_frames(8_000), 40);
    assert_eq!(envelope_frames(44_100), 221);
    assert_eq!(tick_to_frame(2, 48_000, 96_000).unwrap(), 63); // exact half-frame rounds up.
    assert!(tick_to_frame(u64::MAX, u32::MAX, u32::MAX).is_err());
    assert!(tick_to_frame(1, 48000, 0).is_err());
    assert!(tick_to_frame(1, 0, 120000).is_err());
    assert!(tick_to_frame(MAX_FRAME, 192000, 20000).is_err());
}

#[test]
fn frame_and_item_boundaries_are_enforced() {
    let mut edge = v2(vec![track(
        TrackMode::Sequenced,
        vec![clip("c", MAX_FRAME - 1, 1, vec![])],
    )]);
    edge.validate().unwrap();
    if let Some(clips) = edge.tracks[0].clips.as_mut() {
        let Clip::Notes(clip) = &mut clips[0] else {
            panic!("expected notes")
        };
        clip.length_frames = 2;
    }
    assert!(edge.validate().is_err());
    let mut bad = v2(vec![track(
        TrackMode::Sequenced,
        vec![clip("c", 0, 10, vec![note("n", 0, 0)])],
    )]);
    assert!(bad.validate().is_err());
    if let Clip::Notes(clip) = &mut bad.tracks[0].clips.as_mut().unwrap()[0] {
        clip.notes[0] = note("n", 9, 2);
    }
    assert!(bad.validate().is_err());
    if let Clip::Notes(clip) = &mut bad.tracks[0].clips.as_mut().unwrap()[0] {
        clip.notes[0].velocity = f64::NAN;
    }
    assert!(bad.validate().is_err());
    let max_clips = (0..1024)
        .map(|i| clip(&format!("c{i}"), 0, 1, vec![]))
        .collect();
    v2(vec![track(TrackMode::Sequenced, max_clips)])
        .validate()
        .unwrap();
    let too_many_clips = (0..1025)
        .map(|i| clip(&format!("c{i}"), 0, 1, vec![]))
        .collect();
    assert!(
        v2(vec![track(TrackMode::Sequenced, too_many_clips)])
            .validate()
            .is_err()
    );
    let many = (0..16_385).map(|i| note(&format!("n{i}"), 0, 1)).collect();
    assert!(
        v2(vec![track(
            TrackMode::Sequenced,
            vec![clip("c", 0, 2, many)]
        )])
        .validate()
        .is_err()
    );
}

#[test]
fn polyphony_counts_release_tails_and_observes_clip_cuts() {
    let overlapping = (0..=MAX_VOICES)
        .map(|i| note(&format!("n{i}"), 0, 1))
        .collect();
    assert!(
        v2(vec![track(
            TrackMode::Sequenced,
            vec![clip("c", 0, 100, overlapping)]
        )])
        .validate()
        .is_err()
    );

    let mut tail_notes: Vec<_> = (0..MAX_VOICES)
        .map(|i| note(&format!("n{i}"), 0, 1))
        .collect();
    tail_notes.push(note("later", 2, 1));
    assert!(
        v2(vec![track(
            TrackMode::Sequenced,
            vec![clip("c", 0, 100, tail_notes)]
        )])
        .validate()
        .is_err()
    );

    let cut = v2(vec![track(
        TrackMode::Sequenced,
        vec![
            clip("a", 0, 2, vec![note("n1", 0, 1)]),
            clip("b", 2, 2, vec![note("n2", 0, 1)]),
        ],
    )]);
    cut.validate().unwrap();
}
