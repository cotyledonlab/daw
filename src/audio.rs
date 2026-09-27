//! macOS CPAL spike. Stream setup, waiting, and teardown stay on the caller thread.
use crate::{audio_buffer::PlaybackBuffer, session::Session};
use cpal::{
    FromSample, SizedSample,
    traits::{DeviceTrait, HostTrait, StreamTrait},
};
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering::Relaxed},
    },
    time::{Duration, Instant},
};

#[derive(Default)]
struct Stats {
    error: AtomicBool,
    done: AtomicBool,
    calls: AtomicU64,
    frames: AtomicU64,
    max_frames: AtomicU64,
    max_ns: AtomicU64,
    over_budget: AtomicU64,
}

pub fn devices() -> Result<Value, String> {
    let host = cpal::default_host();
    let default = host.default_output_device().and_then(|d| d.name().ok());
    let devices: Vec<_> = host.output_devices().map_err(|e| e.to_string())?.map(|device| {
        let name = device.name().unwrap_or_else(|_| "Unknown".into());
        match device.default_output_config() {
            Ok(c) => json!({"name":name,"sample_rate":c.sample_rate().0,"channels":c.channels(),"format":format!("{:?}",c.sample_format())}),
            Err(e) => json!({"name":name,"error":e.to_string()}),
        }
    }).collect();
    Ok(json!({"default_output":default,"outputs":devices}))
}

pub fn play(session: &Session, seconds: f64, volume: f64) -> Result<Value, String> {
    session.validate()?;
    crate::render::validate_duration(seconds)?;
    let device = cpal::default_host()
        .default_output_device()
        .ok_or("no default output device")?;
    let selected = device.default_output_config().map_err(|e| e.to_string())?;
    let format = selected.sample_format();
    let config: cpal::StreamConfig = selected.into();
    let renderer = PlaybackBuffer::prepare(
        session,
        config.sample_rate.0,
        config.channels as usize,
        seconds,
        volume,
    )?;
    let name = device.name().map_err(|e| e.to_string())?;
    let stats = Arc::new(Stats::default());
    let stream = match format {
        cpal::SampleFormat::F32 => stream::<f32>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::F64 => stream::<f64>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::I16 => stream::<i16>(&device, &config, renderer, stats.clone()),
        cpal::SampleFormat::U16 => stream::<u16>(&device, &config, renderer, stats.clone()),
        other => {
            return Err(format!(
                "unsupported default device sample format: {other:?}"
            ));
        }
    }?;
    eprintln!(
        "Playing on {name}: {} Hz, {} channels, {format:?}, {seconds}s, volume {volume}. Ctrl+C stops.",
        config.sample_rate.0, config.channels
    );
    stream.play().map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_secs_f64(seconds + 3.0);
    while !stats.done.load(Relaxed) && !stats.error.load(Relaxed) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(5));
    }
    let completed = stats.done.load(Relaxed);
    if completed {
        // Allow the last submitted buffer to reach the device before tearing down.
        let tail = stats.max_frames.load(Relaxed) as f64 / f64::from(config.sample_rate.0);
        std::thread::sleep(Duration::from_secs_f64(tail.min(1.0) + 0.1));
    }
    drop(stream);
    if stats.error.load(Relaxed) {
        return Err("audio stream failed or returned malformed buffers; playback stopped".into());
    }
    if !completed {
        return Err("audio callback timed out; playback stopped".into());
    }
    Ok(json!({
        "device":name, "session_sample_rate":session.sample_rate, "device_sample_rate":config.sample_rate.0,
        "channels":config.channels,"format":format!("{format:?}"),"volume":volume,
        "submitted_frames":stats.frames.load(Relaxed),"callbacks":stats.calls.load(Relaxed),
        "max_callback_frames":stats.max_frames.load(Relaxed),"max_render_microseconds":stats.max_ns.load(Relaxed) as f64 / 1000.0,
        "callbacks_over_buffer_budget":stats.over_budget.load(Relaxed),"stream_released":true
    }))
}

fn stream<T: SizedSample + FromSample<f64>>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    mut renderer: PlaybackBuffer,
    stats: Arc<Stats>,
) -> Result<cpal::Stream, String> {
    let errors = stats.clone();
    let channels = config.channels as usize;
    let rate = u64::from(config.sample_rate.0);
    device
        .build_output_stream(
            config,
            move |output: &mut [T], _| {
                let start = Instant::now();
                if stats.error.load(Relaxed) {
                    output.fill(T::EQUILIBRIUM);
                    return;
                }
                let frames = output.len() / channels;
                match renderer.fill(output, T::from_sample) {
                    Ok(rendered) => {
                        stats.frames.fetch_add(rendered, Relaxed);
                    }
                    Err(_) => {
                        stats.error.store(true, Relaxed);
                    }
                }
                stats.calls.fetch_add(1, Relaxed);
                stats.max_frames.fetch_max(frames as u64, Relaxed);
                let nanos = start.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64;
                stats.max_ns.fetch_max(nanos, Relaxed);
                if nanos > frames as u64 * 1_000_000_000 / rate {
                    stats.over_budget.fetch_add(1, Relaxed);
                }
                if renderer.remaining_frames() == 0 {
                    stats.done.store(true, Relaxed);
                }
            },
            move |_| {
                errors.error.store(true, Relaxed);
            },
            Some(Duration::from_secs(5)),
        )
        .map_err(|e| e.to_string())
}
