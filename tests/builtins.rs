use daw::{
    engine::Engine,
    session::{Clip, Device, Session},
};
use serde_json::{Value, json};

fn fixture(device: Value, hz: f64) -> Session {
    serde_json::from_value(json!({
        "schema_version":9,"sample_rate":8000,"tempo_milli_bpm":120000,
        "tracks":[{"id":"instrument","device":device,"mode":"sequenced","effects":[],
        "clips":[{"kind":"notes","id":"pattern","start_frame":0,"length_frames":5000,
        "notes":[{"id":"n","start_frame":0,"duration_frames":80,"frequency_hz":hz,"velocity":0.7}]}]}]
    })).unwrap()
}
fn drum() -> Value {
    json!({"kind":"drumkit","kit_id":"factory-v1","gain":0.5})
}
fn synth() -> Value {
    json!({"kind":"synth","waveform":"saw","gain":0.2,"attack_ms":5,"release_ms":100,"cutoff_hz":2000})
}
fn midi(m: u8) -> f64 {
    440.0 * 2f64.powf((f64::from(m) - 69.0) / 12.0)
}
fn notes(session: &mut Session) -> &mut daw::session::NoteClip {
    let Clip::Notes(clip) = &mut session.tracks[0].clips.as_mut().unwrap()[0] else {
        panic!()
    };
    clip
}
fn render(session: &Session, count: usize) -> Vec<[f64; 2]> {
    let mut result = vec![[0.0; 2]; count];
    Engine::prepare(session).unwrap().render_block(&mut result);
    result
}

#[test]
fn builtins_require_new_schema_and_strict_valid_parameters() {
    for device in [drum(), synth()] {
        let mut session = fixture(device, midi(36));
        session.validate().unwrap();
        session.tracks[0].effects = None;
        assert!(
            session
                .validate()
                .unwrap_err()
                .contains("requires track effects")
        );
        session.tracks[0].effects = Some(vec![]);
        session.schema_version = 8;
        assert!(session.validate().unwrap_err().contains("schema_version 9"));
        session.schema_version = 9;
        session.tracks[0].mode = Some(daw::session::TrackMode::Continuous);
        assert!(session.validate().is_err());
    }
    for device in [
        json!({"kind":"drumkit","kit_id":"unknown","gain":0.5}),
        json!({"kind":"drumkit","kit_id":"factory-v1","gain":1.1}),
        json!({"kind":"synth","waveform":"saw","gain":0.2,"attack_ms":0,"release_ms":100,"cutoff_hz":2000}),
        json!({"kind":"synth","waveform":"square","gain":0.2,"attack_ms":1,"release_ms":2001,"cutoff_hz":2000}),
        json!({"kind":"synth","waveform":"square","gain":0.2,"attack_ms":1,"release_ms":100,"cutoff_hz":4000}),
    ] {
        assert!(fixture(device, midi(36)).validate().is_err());
    }
    let mut unknown = synth();
    unknown["surprise"] = json!(true);
    assert!(serde_json::from_value::<Device>(unknown).is_err());
    let mut unknown = synth();
    unknown["waveform"] = json!("triangle");
    assert!(serde_json::from_value::<Device>(unknown).is_err());
    assert!(fixture(drum(), midi(37)).validate().is_err());
    for m in [36, 38, 42] {
        fixture(drum(), midi(m)).validate().unwrap();
    }
}

#[test]
fn drum_gate_does_not_truncate_tail_but_clip_boundary_does() {
    let mut session = fixture(drum(), midi(36));
    notes(&mut session).notes[0].duration_frames = 1;
    let output = render(&session, 5000);
    assert!(output[100..3900].iter().any(|frame| frame[0] != 0.0));
    assert!(output[4000..].iter().all(|frame| frame[0] == 0.0));
    notes(&mut session).length_frames = 100;
    let truncated = render(&session, 200);
    assert!(truncated[..100].iter().any(|frame| frame[0] != 0.0));
    assert!(truncated[100..].iter().all(|frame| frame[0] == 0.0));
}

#[test]
fn drum_polyphony_counts_sample_tails_instead_of_note_gates() {
    let mut session = fixture(drum(), midi(36));
    let template = notes(&mut session).notes[0].clone();
    notes(&mut session).notes = (0..65)
        .map(|i| {
            let mut note = template.clone();
            note.id = format!("n{i}");
            note.start_frame = i * 2;
            note.duration_frames = 1;
            note
        })
        .collect();
    assert!(
        session
            .validate()
            .unwrap_err()
            .contains("polyphony exceeds 64")
    );
    notes(&mut session).notes.truncate(64);
    session.validate().unwrap();
    render(&session, 5000);
}

#[test]
fn synth_release_polyphony_is_bounded() {
    let mut session = fixture(synth(), 440.0);
    let template = notes(&mut session).notes[0].clone();
    notes(&mut session).notes = (0..65)
        .map(|i| {
            let mut note = template.clone();
            note.id = format!("n{i}");
            note.start_frame = i * 2;
            note.duration_frames = 1;
            note
        })
        .collect();
    assert!(
        session
            .validate()
            .unwrap_err()
            .contains("polyphony exceeds 64")
    );
}

#[test]
fn instruments_are_block_deterministic_and_reset_on_seek_and_loop() {
    let mut square = synth();
    square["waveform"] = json!("square");
    for (device, hz) in [(drum(), midi(38)), (synth(), 440.0), (square, 440.0)] {
        let session = fixture(device, hz);
        let expected = render(&session, 6000);
        assert!(expected.iter().all(|f| f.iter().all(|v| v.is_finite())));
        assert!(expected.iter().any(|f| f[0] != 0.0));
        let mut engine = Engine::prepare(&session).unwrap();
        let mut actual = vec![[0.0; 2]; 6000];
        for block in actual.chunks_mut(37) {
            engine.render_block(block);
        }
        assert_eq!(actual, expected);
        engine.seek(0).unwrap();
        engine.render_block(&mut actual);
        assert_eq!(actual, expected);
        engine.seek(20).unwrap();
        let mut empty = [[1.0; 2]; 10];
        engine.render_block(&mut empty);
        assert_eq!(empty, [[0.0; 2]; 10]);
        engine.seek(0).unwrap();
        engine.set_loop(Some((0, 100))).unwrap();
        let mut loops = [[0.0; 2]; 300];
        engine.render_block(&mut loops);
        assert_eq!(&loops[..100], &loops[100..200]);
        assert_eq!(&loops[..100], &loops[200..]);
    }
}

#[test]
fn synth_release_and_velocity_scaling_are_audible() {
    let mut session = fixture(synth(), 440.0);
    let output = render(&session, 1000);
    assert!(output[100..800].iter().any(|f| f[0] != 0.0));
    assert!(output[880..].iter().all(|f| f[0] == 0.0));
    notes(&mut session).notes[0].velocity = 0.35;
    let quieter = render(&session, 1000);
    for (loud, quiet) in output.iter().zip(quieter) {
        assert_eq!(loud[0] * 0.5, quiet[0]);
    }
}

// FNV-1a over signed PCM16 little-endian, rounding sample * 32767. This is
// intentionally versioned: changing factory sound content requires a new kit ID.
fn pcm16_hash(samples: &[f64]) -> u64 {
    samples.iter().fold(0xcbf29ce484222325, |mut hash, sample| {
        for byte in ((sample.clamp(-1.0, 1.0) * 32767.0).round() as i16).to_le_bytes() {
            hash ^= u64::from(byte);
            hash = hash.wrapping_mul(0x100000001b3);
        }
        hash
    })
}

#[test]
fn factory_v1_pcm16_content_is_fixed_at_common_sample_rates() {
    assert_eq!(daw::builtins::FACTORY_KIT, "factory-v1");
    for (rate, expected) in [
        (
            44100,
            [0xcb9a4db8691d2e3b, 0x4596fd0bbf6b1d2f, 0x6d71edcb2c36e367],
        ),
        (
            48000,
            [0x845608c08e3a456d, 0x3558d7167a5a85c7, 0xbab33ea4e5453a89],
        ),
    ] {
        let bank = daw::builtins::DrumBank::prepare(rate);
        let hashes = bank.samples.each_ref().map(|samples| pcm16_hash(samples));
        assert_eq!(
            hashes, expected,
            "factory-v1 sound changed; preserve it and add a new kit version"
        );
    }
}

#[test]
fn polyblep_reduces_high_pitch_alias_energy() {
    use daw::builtins::{Waveform, oscillator};
    // 9kHz at 48kHz, exactly periodic across 256 frames. Only the 9kHz
    // fundamental and (saw only) 18kHz second harmonic lie below Nyquist.
    let count = 256;
    let step = 48.0 / count as f64;
    for waveform in [Waveform::Saw, Waveform::Square] {
        let raw: Vec<_> = (0..count)
            .map(|i| {
                let phase = (i as f64 * step) % 1.0;
                match waveform {
                    Waveform::Saw => 2.0 * phase - 1.0,
                    Waveform::Square => {
                        if phase < 0.5 {
                            1.0
                        } else {
                            -1.0
                        }
                    }
                }
            })
            .collect();
        let filtered: Vec<_> = (0..count)
            .map(|i| oscillator(waveform, (i as f64 * step) % 1.0, step))
            .collect();
        let alias_energy = |samples: &[f64]| {
            (1..count / 2)
                .filter(|&bin| bin != 48 && !(waveform == Waveform::Saw && bin == 96))
                .map(|bin| {
                    let (mut re, mut im) = (0.0, 0.0);
                    for (i, &sample) in samples.iter().enumerate() {
                        let phase = std::f64::consts::TAU * bin as f64 * i as f64 / count as f64;
                        re += sample * phase.cos();
                        im += sample * phase.sin();
                    }
                    re * re + im * im
                })
                .sum::<f64>()
        };
        assert!(alias_energy(&filtered) < alias_energy(&raw) * 0.1);
    }
}

#[test]
fn synth_onset_has_at_least_one_millisecond_attack_and_zero_release_is_bounded() {
    let mut device = synth();
    device["attack_ms"] = json!(1);
    device["release_ms"] = json!(0);
    let session = fixture(device, 440.0);
    let result = render(&session, 100);
    assert_eq!(result[0], [0.0; 2]);
    assert!(result[1][0].abs() < 0.05);
    assert!(result[1..80].iter().any(|f| f[0] != 0.0));
    assert!(result[81..].iter().all(|f| f[0] == 0.0));
    assert_eq!(daw::builtins::envelope_frames(0.0, 8000), 1);
}
