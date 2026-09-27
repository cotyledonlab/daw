use daw::{
    engine::Engine,
    render,
    session::{Device, Session},
};
use std::f64::consts::TAU;

fn tone() -> Session {
    serde_json::from_value(serde_json::json!({
        "schema_version": 1,
        "sample_rate": 48000,
        "tracks": [{"id":"tone", "device":{"kind":"sine", "frequency_hz":440.0, "gain":0.5}}]
    }))
    .unwrap()
}

#[test]
fn block_boundaries_preserve_phase_and_cursor() {
    let session = tone();
    let mut one_block = Engine::prepare(&session).unwrap();
    let mut whole = vec![[0.0; 2]; 4_800];
    assert_eq!(one_block.render_block(&mut whole), 0);
    assert_eq!(one_block.frame_position(), 4_800);

    let mut split = Engine::prepare(&session).unwrap();
    let mut split_output = vec![[0.0; 2]; 4_800];
    let mut start = 0;
    for size in [1, 127, 256, 1_024, 3_392] {
        let end = (start + size).min(split_output.len());
        split.render_block(&mut split_output[start..end]);
        start = end;
    }
    assert_eq!(start, split_output.len());
    assert_eq!(split.frame_position(), 4_800);
    assert_eq!(whole, split_output);
    assert_eq!(whole[0], [0.0, 0.0]);
    assert!(whole[1][0] > 0.0);

    split.render_block(&mut []);
    assert_eq!(split.frame_position(), 4_800);
}

#[test]
fn wav_sine_matches_analytical_samples_within_one_pcm_step() {
    let session = tone();
    let mut cursor = std::io::Cursor::new(Vec::new());
    let report = render::render(&session, 0.1, &mut cursor).unwrap();
    assert_eq!(report.frames, 4_800);
    cursor.set_position(0);
    let mut reader = hound::WavReader::new(cursor).unwrap();
    let samples: Vec<i16> = reader.samples().map(Result::unwrap).collect();
    assert_eq!(samples.len(), 9_600);
    let step = 1.0 / i16::MAX as f64;
    for (frame, stereo) in samples.chunks_exact(2).enumerate() {
        let expected = (TAU * 440.0 * frame as f64 / 48_000.0).sin() * 0.5;
        let actual = f64::from(stereo[0]) / i16::MAX as f64;
        assert!(
            (actual - expected).abs() <= step,
            "frame {frame}: {actual} vs {expected}"
        );
        assert_eq!(stereo[0], stereo[1]);
    }
}

#[test]
fn silence_clipping_and_render_cursor_are_preserved() {
    let silence = Session::default();
    let report = render::render(&silence, 0.001, std::io::Cursor::new(Vec::new())).unwrap();
    assert_eq!(report.frames, 48);
    assert_eq!(report.clipped_frames, 0);
    let mut reader = hound::WavReader::new(report_output(&silence)).unwrap();
    let samples: Vec<i16> = reader.samples().map(Result::unwrap).collect();
    assert_eq!(samples.len(), 96);
    assert!(samples.iter().all(|sample| *sample == 0));

    let mut loud = tone();
    loud.tracks[0].id = "tone-1".into();
    let Device::Sine { gain, .. } = &mut loud.tracks[0].device;
    *gain = 1.0;
    let track = loud.tracks[0].clone();
    let mut track = track;
    track.id = "tone-2".into();
    loud.tracks.push(track);
    let mut output = std::io::Cursor::new(Vec::new());
    let report = render::render(&loud, 0.1, &mut output).unwrap();
    assert!(report.clipped_frames > 0);
    output.set_position(0);
    let mut reader = hound::WavReader::new(output).unwrap();
    let samples: Vec<i16> = reader.samples().map(Result::unwrap).collect();
    assert_eq!(samples.len(), report.frames as usize * 2);
    assert!(samples.chunks_exact(2).all(|frame| frame[0] == frame[1]));
}

fn report_output(session: &Session) -> std::io::Cursor<Vec<u8>> {
    let mut bytes = std::io::Cursor::new(Vec::new());
    render::render(session, 0.001, &mut bytes).unwrap();
    bytes.set_position(0);
    bytes
}

#[test]
fn prepare_rejects_invalid_sessions_before_rendering() {
    let mut invalid = tone();
    invalid.tracks[0].id.clear();
    assert!(Engine::prepare(&invalid).is_err());
}
