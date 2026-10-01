//! Embedded Pd patches prepared outside callbacks by an owned libpd worker.
use serde::{Deserialize, Serialize};
use std::{collections::HashSet, sync::Arc};

pub const MAX_PROGRAM_BYTES: usize = 60 * 1024;
pub const MAX_CONTROLS: usize = 64;
pub const BLOCK_FRAMES: u64 = 64;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Source {
    pub program: String,
    pub duration_frames: u64,
    pub gain: f64,
    pub controls: Vec<Control>,
    pub abstractions: Vec<Abstraction>,
    #[serde(skip)]
    pub prepared: Option<Arc<Prepared>>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Abstraction {
    pub name: String,
    pub program: String,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Control {
    pub name: String,
    pub value: f64,
    pub points: Vec<Point>,
}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Point {
    pub frame: u64,
    pub value: f64,
}
#[derive(Debug, PartialEq)]
pub struct Prepared {
    key: Vec<u8>,
    sample_rate: u32,
    pub(crate) audio: Arc<Vec<[f64; 2]>>,
}
impl Source {
    pub fn validate(&self, rate: u32) -> Result<(), String> {
        if self.program.is_empty()
            || self.program.len() > MAX_PROGRAM_BYTES
            || self.program.contains('\0')
        {
            return Err(
                "Pure Data program must be nonempty UTF-8 without NUL, at most 60 KiB".into(),
            );
        }
        if self.abstractions.len() > 16 {
            return Err("at most 16 Pure Data abstractions are supported".into());
        }
        let mut abstraction_names = HashSet::new();
        let mut program_bytes = self.program.len();
        for abstraction in &self.abstractions {
            let name = abstraction.name.as_bytes();
            if name.is_empty()
                || name.len() > 64
                || !(name[0].is_ascii_alphabetic() || name[0] == b'_')
                || !name
                    .iter()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-'))
                || !abstraction_names.insert(&abstraction.name)
                || abstraction.program.is_empty()
                || abstraction.program.len() > MAX_PROGRAM_BYTES
                || abstraction.program.contains('\0')
            {
                return Err("Pure Data abstractions require unique ASCII names of at most 64 bytes and nonempty UTF-8 programs without NUL".into());
            }
            program_bytes += abstraction.program.len();
        }
        if program_bytes > MAX_PROGRAM_BYTES {
            return Err("Pure Data patch and abstractions together exceed 60 KiB".into());
        }
        if self.duration_frames < u64::from(rate).div_ceil(1000)
            || self.duration_frames > u64::from(rate) * 10
        {
            return Err("Pure Data duration must be between 0.001 and 10 seconds".into());
        }
        if !self.gain.is_finite() || !(0.0..=1.0).contains(&self.gain) {
            return Err("Pure Data source gain must be finite and between 0 and 1".into());
        }
        if self.controls.len() > MAX_CONTROLS {
            return Err("at most 64 Pure Data controls are supported".into());
        }
        let mut names = HashSet::new();
        let mut points = 0;
        for control in &self.controls {
            if control.name.is_empty()
                || control.name.len() > 128
                || control.name.contains('\0')
                || !names.insert(&control.name)
                || !control.value.is_finite()
                || !(control.value as f32).is_finite()
            {
                return Err("Pure Data control names must be unique, nonempty and at most 128 UTF-8 bytes without NUL; values must be finite float32".into());
            }
            let mut previous = None;
            for point in &control.points {
                if !point.value.is_finite()
                    || !(point.value as f32).is_finite()
                    || point.frame >= self.duration_frames
                    || point.frame % BLOCK_FRAMES != 0
                    || previous.is_some_and(|frame| point.frame <= frame)
                {
                    return Err("Pure Data points must have finite values and strictly increasing 64-frame-aligned positions within the source duration".into());
                }
                previous = Some(point.frame);
            }
            points += control.points.len();
        }
        if points > crate::sc_source::MAX_POINTS {
            return Err("Pure Data source exceeds 512 control points".into());
        }
        Ok(())
    }
    fn key(&self) -> Result<Vec<u8>, String> {
        let mut saved = self.clone();
        saved.gain = 1.0;
        serde_json::to_vec(&saved).map_err(|e| e.to_string())
    }
    pub(crate) fn prepare(&self, rate: u32) -> Result<Arc<Prepared>, String> {
        self.validate(rate)?;
        let key = self.key()?;
        if let Some(cache) = &self.prepared {
            if cache.key == key && cache.sample_rate == rate {
                return Ok(Arc::clone(cache));
            }
        }
        let audio = render_source(self, rate)?;
        Ok(Arc::new(Prepared {
            key,
            sample_rate: rate,
            audio: Arc::new(audio),
        }))
    }
}

#[cfg(not(unix))]
fn render_source(_: &Source, _: u32) -> Result<Vec<[f64; 2]>, String> {
    Err("Pure Data source preparation is unavailable on this platform".into())
}
#[cfg(unix)]
fn render_source(source: &Source, rate: u32) -> Result<Vec<[f64; 2]>, String> {
    use std::{
        fs,
        os::unix::{fs::PermissionsExt, process::CommandExt},
        path::{Path, PathBuf},
        process::{Command, Stdio},
        thread,
        time::{Duration, Instant},
    };
    let library = PathBuf::from(
        std::env::var_os("DAW_LIBPD_LIBRARY")
            .ok_or("DAW_LIBPD_LIBRARY must name an absolute libpd library")?,
    );
    if !library.is_absolute() || !library.is_file() {
        return Err("DAW_LIBPD_LIBRARY must name an existing absolute library".into());
    }
    let worker = std::env::var_os("DAW_LIBPD_WORKER")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR")).join("native/puredata/source_worker.py")
        });
    if !worker.is_absolute() || !worker.is_file() {
        return Err("Pure Data source worker is unavailable".into());
    }
    let python = if let Some(path) = std::env::var_os("DAW_LIBPD_PYTHON") {
        let path = PathBuf::from(path);
        if !path.is_absolute()
            || !path.is_file()
            || fs::metadata(&path)
                .map_err(|e| e.to_string())?
                .permissions()
                .mode()
                & 0o111
                == 0
        {
            return Err("DAW_LIBPD_PYTHON must name an absolute executable".into());
        }
        path
    } else {
        PathBuf::from("python3")
    };
    let temp = tempfile::tempdir().map_err(|e| e.to_string())?;
    let job = temp.path().join("job.json");
    let output = temp.path().join("source.pcm");
    fs::write(
        &job,
        serde_json::to_vec(
            &serde_json::json!({"job_version":1,"sample_rate":rate,"source":source}),
        )
        .map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    let mut child = Command::new(python)
        .arg(worker)
        .arg(job)
        .arg(&output)
        .arg(library)
        .current_dir(temp.path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|e| format!("Pure Data source worker start failed: {e}"))?;
    let pid = child.id();
    let out = child.stdout.take().unwrap();
    let err = child.stderr.take().unwrap();
    let stdout = thread::spawn(move || crate::csound::diagnostics(out));
    let stderr = thread::spawn(move || crate::csound::diagnostics(err));
    let start = Instant::now();
    let max_bytes = 20 + source.duration_frames * 16;
    let mut failure = None;
    let status = loop {
        if start.elapsed() >= Duration::from_secs(15) {
            failure = Some("Pure Data source worker timed out after 15 seconds");
        }
        if fs::symlink_metadata(&output)
            .is_ok_and(|m| !m.file_type().is_file() || m.len() > max_bytes)
        {
            failure = Some("Pure Data source output outside file limit");
        }
        if failure.is_some() {
            crate::csound::kill_group(pid);
            break child.wait();
        }
        match child.try_wait() {
            Ok(Some(status)) => break Ok(status),
            Ok(None) => thread::sleep(Duration::from_millis(10)),
            Err(e) => {
                crate::csound::kill_group(pid);
                let _ = child.wait();
                break Err(e);
            }
        }
    };
    crate::csound::kill_group(pid);
    let out = stdout
        .join()
        .map_err(|_| "Pure Data stdout reader failed")?
        .map_err(|e| e.to_string())?;
    let err = stderr
        .join()
        .map_err(|_| "Pure Data stderr reader failed")?
        .map_err(|e| e.to_string())?;
    if let Some(failure) = failure {
        return Err(failure.into());
    }
    if out.1 || err.1 {
        return Err("Pure Data source diagnostics exceeded 64 KiB".into());
    }
    let status = status.map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!(
            "Pure Data source worker failed ({status}): {}",
            String::from_utf8_lossy(&err.0)
                .chars()
                .take(1024)
                .collect::<String>()
        ));
    }
    let meta = fs::symlink_metadata(&output)
        .map_err(|e| format!("Pure Data source output unavailable: {e}"))?;
    if !meta.file_type().is_file() || meta.len() != max_bytes {
        return Err(
            "Pure Data source output must be a regular file with the exact requested PCM length"
                .into(),
        );
    }
    let bytes = fs::read(&output).map_err(|e| e.to_string())?;
    if bytes.len() != max_bytes as usize
        || &bytes[..4] != b"DPD1"
        || u32::from_le_bytes(bytes[4..8].try_into().unwrap()) != rate
        || u64::from_le_bytes(bytes[8..16].try_into().unwrap()) != source.duration_frames
        || u32::from_le_bytes(bytes[16..20].try_into().unwrap()) != 2
    {
        return Err("invalid Pure Data source PCM header".into());
    }
    let mut audio = Vec::with_capacity(source.duration_frames as usize);
    for pair in bytes[20..].chunks_exact(16) {
        let left = f64::from_le_bytes(pair[..8].try_into().unwrap());
        let right = f64::from_le_bytes(pair[8..].try_into().unwrap());
        if !left.is_finite() || !right.is_finite() {
            return Err("Pure Data source PCM contains nonfinite samples".into());
        }
        audio.push([left, right]);
    }
    Ok(audio)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::session::{Device, Session};
    #[test]
    fn prepared_puredata_cache_preserves_headroom_gain_seek_loop_and_freshness() {
        let mut session:Session=serde_json::from_value(serde_json::json!({"schema_version":8,"sample_rate":8000,"tempo_milli_bpm":120000,"tracks":[{"id":"pd","mode":"continuous","clips":[],"effects":[{"kind":"gain","id":"trim","gain":0.25,"bypass":false}],"device":{"kind":"puredata","program":"x","duration_frames":80,"gain":0.5,"controls":[],"abstractions":[]}}]})).unwrap();
        let Device::Puredata(source) = &mut session.tracks[0].device else {
            unreachable!()
        };
        source.prepared = Some(Arc::new(Prepared {
            key: source.key().unwrap(),
            sample_rate: 8000,
            audio: Arc::new(vec![[3.0, -2.0]; 80]),
        }));
        let cache = source.prepare(8000).unwrap();
        source.gain = 0.25;
        assert!(Arc::ptr_eq(&cache, &source.prepare(8000).unwrap()));
        let mut engine = crate::engine::Engine::prepare(&session).unwrap();
        let mut frames = [[0.0; 2]; 81];
        engine.render_block(&mut frames);
        assert_eq!(frames[0], [0.1875, -0.125]);
        assert_eq!(frames[79], frames[0]);
        assert_eq!(frames[80], [0.0; 2]);
        engine.seek(40).unwrap();
        engine.render_block(&mut frames[..1]);
        assert_eq!(frames[0], [0.1875, -0.125]);
        engine.set_loop(Some((79, 80))).unwrap();
        engine.seek(79).unwrap();
        engine.render_block(&mut frames);
        assert!(frames.iter().all(|f| *f == [0.1875, -0.125]));
        let Device::Puredata(source) = &mut session.tracks[0].device else {
            unreachable!()
        };
        source.program.push('y');
        assert_ne!(cache.key, source.key().unwrap());
    }
}
