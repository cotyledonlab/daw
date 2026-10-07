use daw::{
    audio_buffer::PlaybackBuffer,
    session::{Device, Session, Track},
};
use std::f64::consts::TAU;

fn tone() -> Session {
    Session {
        tracks: vec![Track {
            mode: None,
            clips: None,
            effects: None,
            automation: None,
            mixer: None,
            id: "tone".into(),
            device: Device::Sine {
                frequency_hz: 440.0,
                gain: 0.5,
            },
        }],
        ..Session::default()
    }
}

#[test]
fn device_rate_preserves_tone_frequency_and_channel_mapping() {
    let session = tone();
    let mut playback = PlaybackBuffer::prepare(&session, 44_100, 4, 0.01, 0.8).unwrap();
    let mut output = [0.0; 4 * 100];
    assert_eq!(playback.fill(&mut output, |sample| sample).unwrap(), 100);

    for (frame_index, frame) in output.chunks_exact(4).enumerate() {
        let expected = (TAU * 440.0 * frame_index as f64 / 44_100.0).sin() * 0.4;
        assert!((frame[0] - expected).abs() < 1e-12, "frame {frame_index}");
        assert!((frame[1] - expected).abs() < 1e-12, "frame {frame_index}");
        assert_eq!(frame[2], 0.0);
        assert_eq!(frame[3], 0.0);
    }

    let mut mono = PlaybackBuffer::prepare(&session, 44_100, 1, 0.01, 1.0).unwrap();
    let mut mono_output = [0.0; 100];
    assert_eq!(mono.fill(&mut mono_output, |sample| sample).unwrap(), 100);
    for (index, sample) in mono_output.iter().enumerate() {
        let expected = (TAU * 440.0 * index as f64 / 44_100.0).sin() * 0.5;
        assert!((sample - expected).abs() < 1e-12, "frame {index}");
    }
}

#[test]
fn short_final_buffer_is_silent_after_duration() {
    let mut playback = PlaybackBuffer::prepare(&tone(), 8_000, 2, 0.001, 1.0).unwrap();
    assert_eq!(playback.remaining_frames(), 8);

    let mut output = [7.0; 20];
    assert_eq!(playback.fill(&mut output, |sample| sample).unwrap(), 8);
    assert!(output[..16].iter().any(|sample| *sample != 0.0));
    assert!(output[16..].iter().all(|sample| *sample == 0.0));
    assert_eq!(playback.remaining_frames(), 0);

    output.fill(7.0);
    assert_eq!(playback.fill(&mut output, |sample| sample).unwrap(), 0);
    assert!(output.iter().all(|sample| *sample == 0.0));
}

#[test]
fn malformed_partial_frame_is_silenced_without_advancing_state() {
    let session = tone();
    let mut playback = PlaybackBuffer::prepare(&session, 48_000, 2, 0.01, 1.0).unwrap();
    let mut fresh = PlaybackBuffer::prepare(&session, 48_000, 2, 0.01, 1.0).unwrap();
    let before = playback.remaining_frames();

    let mut malformed = [9.0; 3];
    assert_eq!(
        playback.fill(&mut malformed, |sample| sample),
        Err("partial device frame")
    );
    assert_eq!(malformed, [0.0; 3]);
    assert_eq!(playback.remaining_frames(), before);

    let mut actual = [0.0; 8];
    let mut expected = [0.0; 8];
    assert_eq!(playback.fill(&mut actual, |sample| sample).unwrap(), 4);
    assert_eq!(fresh.fill(&mut expected, |sample| sample).unwrap(), 4);
    assert_eq!(actual, expected);
}

#[test]
fn supplied_unsigned_conversion_maps_silence_to_midpoint() {
    let mut playback = PlaybackBuffer::prepare(&Session::default(), 48_000, 2, 0.01, 1.0).unwrap();
    let mut output = [0_u16; 12];
    let frames = playback
        .fill(&mut output, |sample| {
            ((sample * 32_767.0).round() as i32 + 32_768) as u16
        })
        .unwrap();
    assert_eq!(frames, 6);
    assert!(output.iter().all(|sample| *sample == 32_768));
}

#[test]
fn prepare_rejects_invalid_session_device_configuration_and_duration() {
    let mut invalid_session = tone();
    invalid_session.tracks[0].id.clear();
    assert!(PlaybackBuffer::prepare(&invalid_session, 48_000, 2, 1.0, 1.0).is_err());

    let session = tone();
    for rate in [0, 400, 193_000] {
        assert!(PlaybackBuffer::prepare(&session, rate, 2, 1.0, 1.0).is_err());
    }
    for channels in [0, 33] {
        assert!(PlaybackBuffer::prepare(&session, 48_000, channels, 1.0, 1.0).is_err());
    }
    for volume in [-0.01, 1.01, f64::NAN, f64::INFINITY] {
        assert!(PlaybackBuffer::prepare(&session, 48_000, 2, 1.0, volume).is_err());
    }
    for seconds in [0.0, 0.000_9, 60.001, f64::NAN, f64::INFINITY] {
        assert!(PlaybackBuffer::prepare(&session, 48_000, 2, seconds, 1.0).is_err());
    }
}

#[test]
fn output_is_independent_of_callback_buffer_sizes() {
    let session = tone();
    let mut whole = PlaybackBuffer::prepare(&session, 48_000, 2, 0.025, 0.7).unwrap();
    let mut reference = vec![0.0; 1_200];
    assert_eq!(whole.fill(&mut reference, |sample| sample).unwrap(), 600);

    let mut split = PlaybackBuffer::prepare(&session, 48_000, 2, 0.025, 0.7).unwrap();
    let mut actual = Vec::with_capacity(1_200);
    for frame_count in [1, 127, 256, 3, 200, 13] {
        let mut chunk = vec![0.0; frame_count * 2];
        assert_eq!(
            split.fill(&mut chunk, |sample| sample).unwrap(),
            frame_count as u64
        );
        actual.extend(chunk);
    }
    assert_eq!(actual, reference);
}
