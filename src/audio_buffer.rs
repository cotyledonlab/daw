//! Device-independent, bounded callback buffer adapter. No device access here.
use crate::{
    engine::Engine,
    render::validate_duration,
    session::{Device, Session},
};

pub struct PlaybackBuffer {
    engine: Option<Engine>,
    metronome: Option<crate::metronome::Metronome>,
    loop_region: Option<(u64, u64)>,
    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    pd: Option<crate::pd_playback::Reader>,
    mixer_peaks: crate::engine::MixerPeaks,
    mixer_available: bool,
    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    plugin: Option<crate::live_ring::Consumer>,
    timeline: u64,
    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    source_digest: u64,
    channels: usize,
    device_rate: u32,
    remaining: u64,
    until_stopped: bool,
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
        if session
            .tracks
            .iter()
            .any(|track| matches!(track.device, Device::PdInstrument(_)))
        {
            return Err("Pd instrument playback requires its owned DSP worker".into());
        }
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
            metronome: None,
            loop_region: None,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            pd: None,
            mixer_peaks: Default::default(),
            mixer_available: session.schema_version >= 10,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            plugin: None,
            timeline: 0,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            source_digest: crate::live_ring::DIGEST_START,
            channels,
            device_rate,
            remaining: (seconds * f64::from(device_rate)).round() as u64,
            until_stopped: false,
            volume,
        })
    }

    /// Indefinite playback accepts bounded built-in devices and preloaded PCM audio.
    /// Validate before any source/plugin preparation can initialize foreign code.
    pub fn validate_until_stopped(session: &Session) -> Result<(), String> {
        if session.tracks.iter().any(|track| {
            !matches!(
                track.device,
                Device::Sine { .. }
                    | Device::Synth { .. }
                    | Device::Drumkit { .. }
                    | Device::Audio { .. }
                    | Device::PdInstrument(_)
            ) || track
                .effects
                .iter()
                .flatten()
                .any(|effect| !effect.is_builtin())
        }) {
            return Err("until-stopped playback supports built-in sine, synth, drumkit, preloaded audio and Pd instrument devices with gain, lowpass and delay effects".into());
        }
        session.validate()
    }

    pub fn prepare_until_stopped(
        session: &Session,
        device_rate: u32,
        channels: usize,
        volume: f64,
    ) -> Result<Self, String> {
        Self::validate_until_stopped(session)?;
        let mut playback = Self::prepare(session, device_rate, channels, 1.0, volume)?;
        // This sentinel is never decremented. Work remains bounded by each callback.
        playback.remaining = u64::MAX;
        playback.until_stopped = true;
        Ok(playback)
    }

    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    pub(crate) fn prepare_pd(
        session: &Session,
        device_rate: u32,
        channels: usize,
        seconds: Option<f64>,
        volume: f64,
    ) -> Result<(Self, crate::pd_playback::Worker), String> {
        session.validate()?;
        if device_rate != session.sample_rate
            || !(1..=32).contains(&channels)
            || !volume.is_finite()
            || !(0.0..=1.0).contains(&volume)
        {
            return Err(
                "Pd playback requires matching device rate, 1..32 channels and volume 0..1".into(),
            );
        }
        if let Some(seconds) = seconds {
            validate_duration(seconds)?;
        } else {
            Self::validate_until_stopped(session)?;
        }
        let (reader, worker) = crate::pd_playback::start(session)?;
        Ok((
            Self {
                engine: None,
                metronome: None,
                loop_region: None,
                pd: Some(reader),
                mixer_peaks: Default::default(),
                mixer_available: true,
                plugin: None,
                timeline: 0,
                source_digest: crate::live_ring::DIGEST_START,
                channels,
                device_rate,
                remaining: seconds
                    .map_or(u64::MAX, |s| (s * f64::from(device_rate)).round() as u64),
                until_stopped: seconds.is_none(),
                volume,
            },
            worker,
        ))
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
                metronome: None,
                loop_region: None,
                pd: None,
                mixer_peaks: Default::default(),
                mixer_available: false,
                plugin: Some(consumer),
                timeline: 0,
                source_digest: crate::live_ring::DIGEST_START,
                channels,
                device_rate,
                remaining,
                until_stopped: false,
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
            metronome: None,
            loop_region: None,
            pd: None,
            mixer_peaks: Default::default(),
            mixer_available: false,
            plugin: Some(consumer),
            timeline: 0,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            source_digest: crate::live_ring::DIGEST_START,
            channels,
            device_rate: 0,
            remaining: frames,
            until_stopped: false,
            volume,
        })
    }

    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    pub(crate) fn source_digest(&self) -> u64 {
        self.source_digest
    }

    /// Finite frames remaining, or `u64::MAX` for until-stopped playback.
    /// The indefinite sentinel stays constant across callbacks.
    pub fn mixer_peaks(&self) -> Option<&crate::engine::MixerPeaks> {
        self.mixer_available.then_some(&self.mixer_peaks)
    }

    /// Configure before starting callbacks; no click state enters Session or exports.
    pub fn configure_metronome(
        &mut self,
        session: &Session,
        enabled: bool,
        bars: u8,
    ) -> Result<(), String> {
        if !enabled && bars == 0 {
            self.metronome = None;
            return Ok(());
        }
        if !crate::metronome::supports_session(session) {
            return Err("metronome and count-in require built-in prepared playback".into());
        }
        if self.device_rate != session.sample_rate {
            return Err("metronome requires matching device and session sample rates".into());
        }
        self.metronome = Some(crate::metronome::Metronome::new(
            session.sample_rate,
            session.tempo_milli_bpm.unwrap_or(120000),
            enabled,
            bars,
        )?);
        Ok(())
    }
    pub fn metronome_enabled(&self) -> bool {
        self.metronome
            .as_ref()
            .is_some_and(crate::metronome::Metronome::enabled)
    }
    pub fn count_in_remaining_frames(&self) -> u64 {
        self.metronome
            .as_ref()
            .map_or(0, crate::metronome::Metronome::count_in_remaining_frames)
    }

    pub fn remaining_frames(&self) -> u64 {
        self.remaining
    }

    pub fn frame_position(&self) -> u64 {
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        if let Some(pd) = &self.pd {
            return pd.frame_position();
        }
        self.engine
            .as_ref()
            .map_or(self.timeline, Engine::frame_position)
    }

    pub fn output_position(&self) -> u64 {
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        if let Some(pd) = &self.pd {
            return pd.output_position();
        }
        self.engine
            .as_ref()
            .map_or(self.timeline, Engine::output_position)
    }

    pub fn seek(&mut self, frame: u64) -> Result<(), String> {
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        if let Some(pd) = &mut self.pd {
            return pd.seek(frame);
        }
        self.engine
            .as_mut()
            .ok_or("plugin playback does not support seek")?
            .seek(frame)
    }

    pub fn set_loop(&mut self, region: Option<(u64, u64)>) -> Result<(), String> {
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        if let Some(pd) = &mut self.pd {
            return pd.set_loop(region);
        }
        self.engine
            .as_mut()
            .ok_or("plugin playback does not support loops")?
            .set_loop(region)?;
        self.loop_region = region;
        Ok(())
    }

    /// Fill all samples, including silence after the duration and on malformed buffers.
    /// The conversion function must be allocation-free and map zero to sample equilibrium.
    pub fn fill<T: Copy>(
        &mut self,
        output: &mut [T],
        convert: impl Fn(f64) -> T,
    ) -> Result<u64, &'static str> {
        self.mixer_peaks = Default::default();
        output.fill(convert(0.0));
        if output.len() % self.channels != 0 {
            return Err("partial device frame");
        }
        let available = output.len() / self.channels;
        if let Some(metronome) = &mut self.metronome {
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            if let Some(pd) = &self.pd {
                pd.begin_callback(&mut self.mixer_peaks);
            }
            let mut rendered = 0;
            for destination in output.chunks_exact_mut(self.channels) {
                let count_sample = metronome.next_count_in();
                let frame = if let Some(click) = count_sample {
                    [click, click]
                } else {
                    if self.remaining == 0 {
                        break;
                    }
                    let mut position = self
                        .engine
                        .as_ref()
                        .map_or(self.timeline, Engine::frame_position);
                    #[cfg(all(feature = "native-audio", target_os = "macos"))]
                    if let Some(pd) = &self.pd {
                        position = pd.frame_position();
                    }
                    if let Some((start, end)) = self.loop_region {
                        if position >= end {
                            position = start;
                        }
                    }
                    let mut frame = [[0.0; 2]; 1];
                    if let Some(engine) = &mut self.engine {
                        engine.render_block(&mut frame);
                        self.mixer_peaks.merge(engine.mixer_peaks());
                    }
                    #[cfg(all(feature = "native-audio", target_os = "macos"))]
                    if let Some(pd) = &mut self.pd {
                        let Some(sample) = pd.next(&mut self.mixer_peaks)? else {
                            break;
                        };
                        frame[0] = sample;
                        metronome.set_tempo(pd.tempo());
                    }
                    let click = metronome.sample(position);
                    if !self.until_stopped {
                        self.remaining -= 1;
                    }
                    rendered += 1;
                    [
                        (frame[0][0] + click).clamp(-1.0, 1.0),
                        (frame[0][1] + click).clamp(-1.0, 1.0),
                    ]
                };
                destination[0] = convert(if self.channels == 1 {
                    (frame[0] + frame[1]) * 0.5 * self.volume
                } else {
                    frame[0] * self.volume
                });
                if self.channels >= 2 {
                    destination[1] = convert(frame[1] * self.volume);
                }
            }
            return Ok(rendered);
        }
        let frames = if self.until_stopped {
            available
        } else {
            available.min(usize::try_from(self.remaining).unwrap_or(usize::MAX))
        };
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        if let Some(pd) = &mut self.pd {
            pd.begin_callback(&mut self.mixer_peaks);
            let mut rendered = 0;
            for destination in output.chunks_exact_mut(self.channels).take(frames) {
                let Some(frame) = pd.next(&mut self.mixer_peaks)? else {
                    break;
                };
                destination[0] = convert(if self.channels == 1 {
                    (frame[0] + frame[1]) * 0.5 * self.volume
                } else {
                    frame[0] * self.volume
                });
                if self.channels >= 2 {
                    destination[1] = convert(frame[1] * self.volume);
                }
                rendered += 1;
            }
            if !self.until_stopped {
                self.remaining -= rendered;
            }
            return Ok(rendered);
        }
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
            self.mixer_peaks
                .merge(self.engine.as_ref().expect("built-in source").mixer_peaks());
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
        if !self.until_stopped {
            self.remaining -= frames as u64;
        }
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
            metronome: None,
            loop_region: None,
            pd: None,
            mixer_peaks: Default::default(),
            mixer_available: false,
            plugin: Some(consumer),
            timeline: 0,
            #[cfg(all(feature = "native-audio", target_os = "macos"))]
            source_digest: crate::live_ring::DIGEST_START,
            channels: 4,
            device_rate: 0,
            remaining: 8,
            until_stopped: false,
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
    #[test]
    fn owned_renderer_callback_is_allocation_free_and_discards_stale_transport_blocks() {
        // The Pd worker can render built-ins too. This fixture proves its callback
        // contract on every native CI run without requiring a foreign runtime.
        let session: Session = serde_json::from_value(serde_json::json!({
            "schema_version":10,"sample_rate":48000,"tempo_milli_bpm":120000,
            "tracks":[{"id":"melody","mode":"sequenced",
            "device":{"kind":"sine","frequency_hz":440,"gain":0.2},"effects":[],
            "clips":[{"kind":"notes","id":"c","start_frame":0,"length_frames":4096,
            "notes":[{"id":"first","start_frame":0,"duration_frames":1024,"frequency_hz":440,"velocity":0.8},
            {"id":"second","start_frame":2048,"duration_frames":1024,"frequency_hz":880,"velocity":0.4}]}]}]
        })).unwrap();
        let (mut playback, mut worker) =
            PlaybackBuffer::prepare_pd(&session, 48000, 2, None, 0.5).unwrap();
        assert!(
            playback.engine.is_none(),
            "callback cannot own the renderer engine"
        );
        let mut reference = Engine::prepare(&session).unwrap();
        let mut actual = [0.0; 74];
        let mut expected = [[0.0; 2]; 37];
        fn compare_frames(
            playback: &mut PlaybackBuffer,
            reference: &mut Engine,
            actual: &mut [f64; 74],
            expected: &mut [[f64; 2]; 37],
            count: usize,
        ) {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
            let mut done = 0;
            while done < count {
                let requested = (count - done).min(37);
                let before_timeline = playback.frame_position();
                let before_output = playback.output_position();
                OPERATIONS.set(0);
                WATCH.set(true);
                let filled = playback.fill(&mut actual[..requested * 2], |sample| sample);
                WATCH.set(false);
                assert_eq!(OPERATIONS.get(), 0, "callback performed a heap operation");
                let rendered = filled.unwrap() as usize;
                if rendered == 0 {
                    assert_eq!(playback.frame_position(), before_timeline);
                    assert_eq!(playback.output_position(), before_output);
                    assert!(actual[..requested * 2].iter().all(|&value| value == 0.0));
                    assert!(
                        std::time::Instant::now() < deadline,
                        "renderer queue failed to refill"
                    );
                    std::thread::sleep(std::time::Duration::from_micros(100));
                    continue;
                }
                reference.render_block(&mut expected[..rendered]);
                for index in 0..rendered {
                    assert_eq!(actual[index * 2], expected[index][0] * 0.5);
                    assert_eq!(actual[index * 2 + 1], expected[index][1] * 0.5);
                }
                assert_eq!(playback.frame_position(), reference.frame_position());
                assert_eq!(playback.output_position(), reference.output_position());
                assert_eq!(playback.remaining_frames(), u64::MAX);
                done += rendered;
            }
        }
        // Startup has queued four blocks. Leave most of them unread, then seek.
        compare_frames(
            &mut playback,
            &mut reference,
            &mut actual,
            &mut expected,
            137,
        );
        OPERATIONS.set(0);
        WATCH.set(true);
        let sought = playback.seek(2048);
        WATCH.set(false);
        assert_eq!(OPERATIONS.get(), 0);
        sought.unwrap();
        reference.seek(2048).unwrap();
        // The second note differs in frequency/velocity; any stale first-note
        // samples or partially consumed local block would fail exact parity.
        compare_frames(
            &mut playback,
            &mut reference,
            &mut actual,
            &mut expected,
            311,
        );
        OPERATIONS.set(0);
        WATCH.set(true);
        let looped = playback.set_loop(Some((2048, 2304)));
        let sought = playback.seek(2048);
        WATCH.set(false);
        assert_eq!(OPERATIONS.get(), 0);
        looped.unwrap();
        sought.unwrap();
        reference.set_loop(Some((2048, 2304))).unwrap();
        reference.seek(2048).unwrap();
        compare_frames(
            &mut playback,
            &mut reference,
            &mut actual,
            &mut expected,
            1025,
        );
        // Stop while the producer may be waiting behind a full bounded queue.
        drop(playback);
        worker.finish().unwrap();
        worker.finish().unwrap();
    }
    #[test]
    fn live_arrangement_queue_keeps_held_notes_and_callback_heap_free() {
        let session: Session = serde_json::from_value(serde_json::json!({
            "schema_version":10,"sample_rate":48000,"tempo_milli_bpm":120000,
            "tracks":[{"id":"lead","mode":"sequenced","device":{"kind":"sine","frequency_hz":440,"gain":0.2},"effects":[],
            "clips":[{"kind":"notes","id":"phrase","start_frame":0,"length_frames":48000,
            "notes":[{"id":"held","start_frame":0,"duration_frames":40000,"frequency_hz":440,"velocity":0.8}]}]}]
        })).unwrap();
        let (mut playback, mut worker) =
            PlaybackBuffer::prepare_pd(&session, 48000, 2, None, 1.0).unwrap();
        let mut reference = Engine::prepare(&session).unwrap();
        let mut output = [0.0; 512];
        let mut expected = [[0.0; 2]; 256];
        // Identical replacement tests phase retention after queued blocks drain.
        worker.update(session.clone(), 7).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while playback.output_position() < 4096 {
            OPERATIONS.set(0);
            WATCH.set(true);
            let count = playback.fill(&mut output, |v| v).unwrap() as usize;
            WATCH.set(false);
            assert_eq!(OPERATIONS.get(), 0);
            reference.render_block(&mut expected[..count]);
            for index in 0..count {
                assert_eq!(output[index * 2], expected[index][0]);
            }
            assert_eq!(playback.frame_position(), reference.frame_position());
            assert!(std::time::Instant::now() < deadline);
            if count == 0 {
                std::thread::sleep(std::time::Duration::from_micros(100));
            }
        }
        assert_eq!(worker.audible_revision(), 7);
        let mut changed = session.clone();
        changed.tracks[0].device = Device::Sine {
            frequency_hz: 440.0,
            gain: 0.1,
        };
        worker.update(changed.clone(), 8).unwrap();
        let mut rejected = changed.clone();
        rejected.tracks.clear();
        assert!(worker.update(rejected, 9).is_err());
        while worker.audible_revision() != 8 {
            playback.fill(&mut output, |v| v).unwrap();
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(std::time::Duration::from_micros(100));
        }
        assert!(playback.output_position() >= 4096);
        drop(playback);
        worker.finish().unwrap();
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
