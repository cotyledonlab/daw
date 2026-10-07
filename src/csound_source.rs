//! Embedded CSD sources prepared outside callbacks by an owned Csound 7 worker.
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
    #[serde(skip)]
    pub prepared: Option<Arc<Prepared>>,
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
            return Err("Csound program must be nonempty UTF-8 without NUL, at most 60 KiB".into());
        }
        if self.duration_frames < u64::from(rate).div_ceil(1000)
            || self.duration_frames > u64::from(rate) * 10
        {
            return Err("Csound duration must be between 0.001 and 10 seconds".into());
        }
        if !self.gain.is_finite() || !(0.0..=1.0).contains(&self.gain) {
            return Err("Csound source gain must be finite and between 0 and 1".into());
        }
        if self.controls.len() > MAX_CONTROLS {
            return Err("at most 64 Csound controls are supported".into());
        }
        let mut names = HashSet::new();
        let mut points = 0;
        for control in &self.controls {
            if control.name.is_empty()
                || control.name.len() > 128
                || control.name.contains('\0')
                || !names.insert(&control.name)
                || !control.value.is_finite()
            {
                return Err("Csound control names must be unique, nonempty and at most 128 UTF-8 bytes without NUL; values must be finite float64".into());
            }
            let mut previous = None;
            for point in &control.points {
                if !point.value.is_finite()
                    || point.frame >= self.duration_frames
                    || point.frame % BLOCK_FRAMES != 0
                    || previous.is_some_and(|frame| point.frame <= frame)
                {
                    return Err("Csound points must have finite values and strictly increasing 64-frame-aligned positions within the source duration".into());
                }
                previous = Some(point.frame);
            }
            points += control.points.len();
        }
        if points > crate::sc_source::MAX_POINTS {
            return Err("Csound source exceeds 512 control points".into());
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
    Err("Csound source preparation is unavailable on this platform".into())
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
        std::env::var_os("DAW_CSOUND_LIBRARY")
            .ok_or("DAW_CSOUND_LIBRARY must name an absolute Csound 7 double-sample library")?,
    );
    if !library.is_absolute() || !library.is_file() {
        return Err("DAW_CSOUND_LIBRARY must name an existing absolute library".into());
    }
    let worker = std::env::var_os("DAW_CSOUND_WORKER")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR")).join("native/csound/source_worker.py")
        });
    if !worker.is_absolute() || !worker.is_file() {
        return Err("Csound source worker is unavailable".into());
    }
    let python = if let Some(path) = std::env::var_os("DAW_CSOUND_PYTHON") {
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
            return Err("DAW_CSOUND_PYTHON must name an absolute executable".into());
        }
        path
    } else {
        PathBuf::from("python3")
    };
    let temp = tempfile::tempdir().map_err(|e| e.to_string())?;
    let job = temp.path().join("job.json");
    let output = temp.path().join("source.pcm");
    let rc = temp.path().join("empty.rc");
    fs::write(
        &job,
        serde_json::to_vec(
            &serde_json::json!({"job_version":1,"sample_rate":rate,"source":source}),
        )
        .map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    fs::write(&rc, []).map_err(|e| e.to_string())?;
    fs::create_dir(temp.path().join("empty-opcodes")).map_err(|e| e.to_string())?;
    let mut child = Command::new(python)
        .arg(worker)
        .arg(job)
        .arg(&output)
        .arg(library)
        .env("CSOUND6RC", &rc)
        .env("CSOUND7RC", &rc)
        .current_dir(temp.path())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|e| format!("Csound source worker start failed: {e}"))?;
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
            failure = Some("Csound source worker timed out after 15 seconds");
        }
        if fs::symlink_metadata(&output)
            .is_ok_and(|m| !m.file_type().is_file() || m.len() > max_bytes)
        {
            failure = Some("Csound source output outside file limit");
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
        .map_err(|_| "Csound stdout reader failed")?
        .map_err(|e| e.to_string())?;
    let err = stderr
        .join()
        .map_err(|_| "Csound stderr reader failed")?
        .map_err(|e| e.to_string())?;
    if let Some(failure) = failure {
        return Err(failure.into());
    }
    if out.1 || err.1 {
        return Err("Csound source diagnostics exceeded 64 KiB".into());
    }
    let status = status.map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!(
            "Csound source worker failed ({status}): {}",
            String::from_utf8_lossy(&err.0)
                .chars()
                .take(1024)
                .collect::<String>()
        ));
    }
    let meta = fs::symlink_metadata(&output)
        .map_err(|e| format!("Csound source output unavailable: {e}"))?;
    if !meta.file_type().is_file() || meta.len() != max_bytes {
        return Err(
            "Csound source output must be a regular file with the exact requested PCM length"
                .into(),
        );
    }
    let bytes = fs::read(&output).map_err(|e| e.to_string())?;
    if bytes.len() != max_bytes as usize
        || &bytes[..4] != b"DCS1"
        || u32::from_le_bytes(bytes[4..8].try_into().unwrap()) != rate
        || u64::from_le_bytes(bytes[8..16].try_into().unwrap()) != source.duration_frames
        || u32::from_le_bytes(bytes[16..20].try_into().unwrap()) != 2
    {
        return Err("invalid Csound source PCM header".into());
    }
    let mut audio = Vec::with_capacity(source.duration_frames as usize);
    for pair in bytes[20..].chunks_exact(16) {
        let left = f64::from_le_bytes(pair[..8].try_into().unwrap());
        let right = f64::from_le_bytes(pair[8..].try_into().unwrap());
        if !left.is_finite() || !right.is_finite() {
            return Err("Csound source PCM contains nonfinite samples".into());
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
    fn prepared_csound_cache_preserves_headroom_gain_seek_loop_and_freshness() {
        let mut session:Session=serde_json::from_value(serde_json::json!({"schema_version":7,"sample_rate":8000,"tempo_milli_bpm":120000,"tracks":[{"id":"cs","mode":"continuous","clips":[],"effects":[{"kind":"gain","id":"trim","gain":0.25,"bypass":false}],"device":{"kind":"csound","program":"x","duration_frames":80,"gain":0.5,"controls":[]}}]})).unwrap();
        let Device::Csound(source) = &mut session.tracks[0].device else {
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
        let Device::Csound(source) = &mut session.tracks[0].device else {
            unreachable!()
        };
        source.program.push('y');
        assert_ne!(cache.key, source.key().unwrap());
    }
}
