//! Device-independent, bounded callback buffer adapter. No device access here.
use crate::{engine::Engine, render::validate_duration, session::Session};

pub struct PlaybackBuffer {
    engine: Option<Engine>,
    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    plugin: Option<crate::live_ring::Consumer>,
    timeline: u64,
    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    source_digest: u64,
    channels: usize,
    remaining: u64,
    volume: f64,
}

impl PlaybackBuffer {
    pub fn prepare(
        session: &Session,
        device_rate: u32,
        channels: usize,
        seconds: f64,
        volume: f64,
    ) -> Result<Self, String> {
        session.validate()?;
        validate_duration(seconds)?;
        if !(1..=32).contains(&channels) {
            return Err("device must have 1..32 channels".into());
        }
        if !volume.is_finite() || !(0.0..=1.0).contains(&volume) {
            return Err("volume must be finite and between 0 and 1".into());
        }
        if session.schema_version >= 2 && device_rate != session.sample_rate {
            return Err(
                "timeline sessions require the device rate to match the session sample rate".into(),
            );
        }
        let mut adjusted = session.clone();
        adjusted.sample_rate = device_rate;
        // Prepare at the actual device rate, preserving Hz. No sample resampling needed
        // for these procedural oscillators. Reject tones beyond device Nyquist.
        let engine = Engine::prepare(&adjusted)?;
        Ok(Self {
            engine: Some(engine),
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            plugin: None,
            timeline: 0,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            source_digest: crate::live_ring::DIGEST_START,
            channels,
            remaining: (seconds * f64::from(device_rate)).round() as u64,
            volume,
        })
    }

    #[cfg(all(feature = "vst3-live", target_os = "macos"))]
    pub(crate) fn prepare_native(
        session: &Session,
        device_rate: u32,
        channels: usize,
        seconds: f64,
        volume: f64,
    ) -> Result<(Self, Option<crate::live_plugins::WorkerGuard>), String> {
        if crate::hosting::has_au(session) {
            return Err("native AU playback is unavailable".into());
        }
        if !crate::hosting::has_plugins(session) {
            return Ok((
                Self::prepare(session, device_rate, channels, seconds, volume)?,
                None,
            ));
        }
        session.validate()?;
        validate_duration(seconds)?;
        if device_rate != 48000 || session.sample_rate != 48000 {
            return Err("live VST3 requires a 48000 Hz device and session".into());
        }
        if !(1..=32).contains(&channels) || !volume.is_finite() || !(0.0..=1.0).contains(&volume) {
            return Err("invalid native channel count or volume".into());
        }
        let remaining = (seconds * 48000.0).round() as u64;
        let (consumer, guard) = crate::live_plugins::start(session, remaining)?;
        Ok((
            Self {
                engine: None,
                plugin: Some(consumer),
                timeline: 0,
                source_digest: crate::live_ring::DIGEST_START,
                channels,
                remaining,
                volume,
            },
            Some(guard),
        ))
    }

    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    pub(crate) fn from_stream(
        consumer: crate::live_ring::Consumer,
        frames: u64,
        channels: usize,
        volume: f64,
    ) -> Result<Self, String> {
        if frames == 0
            || !(1..=32).contains(&channels)
            || !volume.is_finite()
            || !(0.0..=1.0).contains(&volume)
        {
            return Err("invalid live stream frames, channels or volume".into());
        }
        Ok(Self {
            engine: None,
            plugin: Some(consumer),
            timeline: 0,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            source_digest: crate::live_ring::DIGEST_START,
            channels,
            remaining: frames,
            volume,
        })
    }

    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    pub(crate) fn source_digest(&self) -> u64 {
        self.source_digest
    }

    pub fn remaining_frames(&self) -> u64 {
        self.remaining
    }

    pub fn frame_position(&self) -> u64 {
        self.engine
            .as_ref()
            .map_or(self.timeline, Engine::frame_position)
    }

    pub fn output_position(&self) -> u64 {
        self.engine
            .as_ref()
            .map_or(self.timeline, Engine::output_position)
    }

    pub fn seek(&mut self, frame: u64) -> Result<(), String> {
        self.engine
            .as_mut()
            .ok_or("plugin playback does not support seek")?
            .seek(frame)
    }

    pub fn set_loop(&mut self, region: Option<(u64, u64)>) -> Result<(), String> {
        self.engine
            .as_mut()
            .ok_or("plugin playback does not support loops")?
            .set_loop(region)
    }

    /// Fill all samples, including silence after the duration and on malformed buffers.
    /// The conversion function must be allocation-free and map zero to sample equilibrium.
    pub fn fill<T: Copy>(
        &mut self,
        output: &mut [T],
        convert: impl Fn(f64) -> T,
    ) -> Result<u64, &'static str> {
        output.fill(convert(0.0));
        if output.len() % self.channels != 0 {
            return Err("partial device frame");
        }
        let frames = (output.len() / self.channels).min(self.remaining as usize);
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        if let Some(consumer) = &mut self.plugin {
            if consumer.failed() {
                return Err("live audio worker failed");
            }
            let mut rendered = 0;
            for destination in output.chunks_exact_mut(self.channels).take(frames) {
                let Some(frame) = consumer.pop() else {
                    if !consumer.done() {
                        consumer.note_underrun(self.timeline);
                    }
                    break;
                };
                self.source_digest = crate::live_ring::digest_frame(self.source_digest, frame);
                destination[0] = convert(if self.channels == 1 {
                    (frame.audio[0] + frame.audio[1]) * 0.5 * self.volume
                } else {
                    frame.audio[0] * self.volume
                });
                if self.channels >= 2 {
                    destination[1] = convert(frame.audio[1] * self.volume);
                }
                self.timeline = frame.timeline;
                rendered += 1;
            }
            self.remaining -= rendered;
            return Ok(rendered);
        }
        let mut block = [[0.0; 2]; 256];
        for offset in (0..frames).step_by(256) {
            let count = (frames - offset).min(256);
            self.engine
                .as_mut()
                .expect("built-in source")
                .render_block(&mut block[..count]);
            for (index, frame) in block[..count].iter().enumerate() {
                let destination = &mut output[(offset + index) * self.channels..][..self.channels];
                destination[0] = convert(if self.channels == 1 {
                    (frame[0] + frame[1]) * 0.5 * self.volume
                } else {
                    frame[0] * self.volume
                });
                if self.channels >= 2 {
                    destination[1] = convert(frame[1] * self.volume);
                }
                // Channels beyond stereo intentionally remain silent.
            }
        }
        self.remaining -= frames as u64;
        Ok(frames as u64)
    }
}

#[cfg(all(test, feature = "native-audio", target_os = "macos"))]
mod live_tests {
    use super::*;
    use crate::live_ring::{self, Frame};
    use std::{
        alloc::{GlobalAlloc, Layout, System},
        cell::Cell,
    };
    #[cfg(feature = "vst3-live")]
    static NATIVE_FIXTURE_TEST: std::sync::Mutex<()> = std::sync::Mutex::new(());
    struct Counted;
    thread_local! { static WATCH: Cell<bool> = const { Cell::new(false) }; static OPERATIONS: Cell<usize> = const { Cell::new(0) }; }
    fn count() {
        if WATCH.try_with(Cell::get).unwrap_or(false) {
            OPERATIONS.with(|n| n.set(n.get() + 1));
        }
    }
    // SAFETY: the original pointer/layout are forwarded unchanged to System.
    unsafe impl GlobalAlloc for Counted {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            count();
            unsafe { System.alloc(layout) }
        }
        unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
            count();
            unsafe { System.dealloc(ptr, layout) }
        }
        unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, size: usize) -> *mut u8 {
            count();
            unsafe { System.realloc(ptr, layout, size) }
        }
    }
    #[global_allocator]
    static ALLOCATOR: Counted = Counted;
    #[test]
    fn stream_consumer_is_bounded_allocation_free_and_preserves_underrun_position() {
        let (mut producer, consumer, control) = live_ring::pair();
        for i in 0..4 {
            producer
                .push(Frame {
                    audio: [0.5, -0.25],
                    timeline: i + 1,
                })
                .unwrap();
        }
        let mut playback = PlaybackBuffer {
            engine: None,
            plugin: Some(consumer),
            timeline: 0,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            source_digest: crate::live_ring::DIGEST_START,
            channels: 4,
            remaining: 8,
            volume: 0.5,
        };
        let mut output = [1.0; 32];
        OPERATIONS.set(0);
        WATCH.set(true);
        let first = playback.fill(&mut output, |s| s);
        let position = playback.frame_position();
        let second = playback.fill(&mut output, |s| s);
        WATCH.set(false);
        assert_eq!(OPERATIONS.get(), 0);
        assert_eq!(first, Ok(4));
        assert_eq!(second, Ok(0));
        assert_eq!(position, 4);
        let expected_digest = (0..4).fold(crate::live_ring::DIGEST_START, |digest, index| {
            crate::live_ring::digest_frame(
                digest,
                Frame {
                    audio: [0.5, -0.25],
                    timeline: index + 1,
                },
            )
        });
        assert_eq!(playback.source_digest(), expected_digest);
        assert_eq!(playback.remaining_frames(), 4);
        assert_eq!(control.underruns(), 2);
        assert!(output.iter().all(|s| *s == 0.0));
        producer.set_failed();
        assert!(playback.fill(&mut output, |s| s).is_err());
    }
    #[cfg(feature = "vst3-live")]
    #[test]
    #[ignore = "requires built native fixture and DAW_VST3_FIXTURE_NO_EVENTS/REALTIME=1"]
    fn actual_live_worker_restores_state_automates_and_releases() {
        let _exclusive = NATIVE_FIXTURE_TEST.lock().unwrap();
        let session: Session = serde_json::from_value(serde_json::json!({
            "schema_version":4,"sample_rate":48000,"tempo_milli_bpm":120000,
            "tracks":[{"id":"t","mode":"continuous","clips":[],"device":{"kind":"sine","frequency_hz":440,"gain":0.1},
            "effects":[{"kind":"vst3","id":"fx","bypass":false,
            "bundle_path":format!("{}/output/vst3-spike/DawTestGain.vst3",env!("CARGO_MANIFEST_DIR")),
            "class_id":"DA01234567894ABCBDEF0123456789AB","state_hex":"000000000000e03f","controller_state_hex":"",
            "parameters":[{"id":0,"value":0.2,"points":[{"frame":32,"value":0.8}]}]}]}]
        })).unwrap();
        for _ in 0..10 {
            let (mut playback, mut worker) =
                PlaybackBuffer::prepare_native(&session, 48000, 2, 0.001, 1.0).unwrap();
            let mut output = [0.0; 96];
            OPERATIONS.set(0);
            WATCH.set(true);
            let result = playback.fill(&mut output, |s| s);
            WATCH.set(false);
            assert_eq!(result, Ok(48));
            assert_eq!(OPERATIONS.get(), 0);
            for i in 0..48 {
                let gain = if i < 32 { 0.2 } else { 0.8 };
                let expected =
                    (std::f64::consts::TAU * i as f64 * 440.0 / 48000.0).sin() * 0.1 * gain;
                assert!((output[i * 2] - expected).abs() < 1e-7);
            }
            drop(playback);
            worker.as_mut().unwrap().finish().unwrap();
        }
        // Stop while the producer is blocked by the bounded full ring.
        let (playback, mut worker) =
            PlaybackBuffer::prepare_native(&session, 48000, 2, 60.0, 1.0).unwrap();
        drop(playback);
        worker.as_mut().unwrap().finish().unwrap();
    }
    #[cfg(feature = "vst3-live")]
    #[test]
    #[ignore = "requires built native fixture and DAW_VST3_FIXTURE_NO_EVENTS/REALTIME=1"]
    fn actual_live_parameter_update_preserves_phase_and_queue_bounds() {
        let _exclusive = NATIVE_FIXTURE_TEST.lock().unwrap();
        let session: Session = serde_json::from_value(serde_json::json!({
            "schema_version":4,"sample_rate":48000,"tempo_milli_bpm":120000,
            "tracks":[{"id":"t","mode":"continuous","clips":[],"device":{"kind":"sine","frequency_hz":440,"gain":0.1},
            "effects":[{"kind":"gain","id":"g","gain":0.5,"bypass":false},
            {"kind":"vst3","id":"fx","bypass":false,
            "bundle_path":format!("{}/output/vst3-spike/DawTestGain.vst3",env!("CARGO_MANIFEST_DIR")),
            "class_id":"DA01234567894ABCBDEF0123456789AB","state_hex":"000000000000e03f","controller_state_hex":"",
            "parameters":[{"id":0,"value":0.2,"points":[]}]}]}]
        })).unwrap();
        let (mut consumer, mut worker) = crate::live_plugins::start(&session, 8192).unwrap();
        assert!(crate::live_plugins::start(&session, 8192).is_err());
        worker
            .queue_parameter(crate::live_plugins::ParameterChange {
                track: 0,
                effect: 1,
                id: 0,
                value: 0.7,
                revision: 42,
            })
            .unwrap();
        let mut frames = Vec::new();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
        while frames.len() < 4096 && std::time::Instant::now() < deadline {
            if let Some(frame) = consumer.pop() {
                frames.push(frame)
            } else {
                std::thread::sleep(std::time::Duration::from_millis(1));
            }
        }
        assert_eq!(frames.len(), 4096);
        let status = worker.parameter_status();
        assert_eq!(status["applied_revision"], "42");
        assert_eq!(status["pending"], 0);
        let changed_at = status["applied_frame"].as_u64().unwrap();
        assert!(changed_at >= 1024 && changed_at % 256 == 0);
        for frame in frames {
            let position = frame.timeline - 1;
            let gain = if position < changed_at { 0.2 } else { 0.7 };
            let expected =
                (std::f64::consts::TAU * position as f64 * 440.0 / 48000.0).sin() * 0.05 * gain;
            assert!((frame.audio[0] - expected).abs() < 1e-7, "frame {position}");
        }
        worker.finish().unwrap();
        let (_consumer, mut worker) = crate::live_plugins::start(&session, 8192).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(50));
        for revision in 1..=8 {
            worker
                .queue_parameter(crate::live_plugins::ParameterChange {
                    track: 0,
                    effect: 1,
                    id: 0,
                    value: 0.6,
                    revision,
                })
                .unwrap();
        }
        assert!(
            worker
                .queue_parameter(crate::live_plugins::ParameterChange {
                    track: 0,
                    effect: 1,
                    id: 0,
                    value: 0.1,
                    revision: 9
                })
                .is_err()
        );
        assert_eq!(worker.parameter_status()["pending"], 8);
        worker.finish().unwrap();
        // Reused effects share a module until every instance has released it.
        let mut repeated = session.clone();
        let effects = repeated.tracks[0].effects.as_mut().unwrap();
        let mut second = effects[1].clone();
        if let crate::session::Effect::Vst3 { id, .. } = &mut second {
            *id = "fx2".into();
        }
        effects.push(second);
        let (_consumer, mut worker) = crate::live_plugins::start(&repeated, 512).unwrap();
        worker.finish().unwrap();
    }
}
