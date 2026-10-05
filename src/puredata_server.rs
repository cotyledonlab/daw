//! Pure Data process/queue ownership on the source worker, never an audio callback.
use crate::{
    puredata_source::Source,
    sc_stream::{self, Queue},
};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    fs::{self, OpenOptions},
    io::Read,
    os::unix::{
        fs::{OpenOptionsExt, PermissionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

pub(crate) struct Server {
    child: Option<Child>,
    pid: u32,
    directory: PathBuf,
    gate: PathBuf,
    frames: u64,
    complete: bool,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Report {
    version: u32,
    published_blocks: u64,
    source_frames: u64,
    queue_frames: u64,
    sha256: String,
    backpressure_waits: u64,
    source_gain_applied: bool,
    puredata_released: bool,
}
fn read_json(path: &Path) -> Result<Value, String> {
    // macOS O_NOFOLLOW=0x100 and O_NONBLOCK=4: reports cannot redirect or block.
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(0x100 | 4)
        .open(path)
        .map_err(|e| format!("Pure Data report open failed: {e}"))?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err("Pure Data report must be a regular file".into());
    }
    let mut bytes = Vec::new();
    file.take(4097)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 4096 {
        return Err("Pure Data report exceeds 4 KiB".into());
    }
    serde_json::from_slice(&bytes).map_err(|e| format!("invalid Pure Data report: {e}"))
}
impl Server {
    pub(crate) fn start(
        source: &Source,
        root: &Path,
        index: usize,
        frames: u64,
    ) -> Result<(Self, Queue), String> {
        if !cfg!(target_arch = "aarch64") {
            return Err("live Pure Data transport requires macOS arm64".into());
        }
        let library = PathBuf::from(
            std::env::var_os("DAW_LIBPD_LIBRARY")
                .ok_or("DAW_LIBPD_LIBRARY must name an absolute libpd library")?,
        );
        let bridge = std::env::var_os("DAW_LIBPD_QUEUE_LIBRARY")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("output/csound-stream/libdaw-csound-queue.dylib")
            });
        if !library.is_absolute()
            || !library.is_file()
            || !bridge.is_absolute()
            || !bridge.is_file()
        {
            return Err("Pure Data library/queue bridge unavailable; set DAW_LIBPD_LIBRARY and build_queue.py".into());
        }
        let worker = std::env::var_os("DAW_LIBPD_STREAM_WORKER")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                Path::new(env!("CARGO_MANIFEST_DIR")).join("native/puredata/stream_worker.py")
            });
        if !worker.is_absolute() || !worker.is_file() {
            return Err("Pure Data stream worker unavailable".into());
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
        let directory = root.join(format!("puredata-{index}"));
        fs::create_dir(&directory).map_err(|e| e.to_string())?;
        let gate = directory.join("begin");
        let queue_path = directory.join("queue");
        let mut random = [0; 8];
        fs::File::open("/dev/urandom")
            .and_then(|mut f| f.read_exact(&mut random))
            .map_err(|e| e.to_string())?;
        let nonce = u64::from_ne_bytes(random).max(1);
        sc_stream::create_queue_with_library(&queue_path, nonce, &bridge)?;
        let queue = Queue::open_with_library(&queue_path, nonce, &bridge)?;
        let mut source = source.clone();
        source.duration_frames = source.duration_frames.min(frames);
        for control in &mut source.controls {
            control
                .points
                .retain(|point| point.frame < source.duration_frames);
        }
        source.validate(48000)?;
        let snapshot = directory.join("job.json");
        fs::write(
            &snapshot,
            serde_json::to_vec(&json!({"job_version":1,"sample_rate":48000,"source":source}))
                .map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        let child = Command::new(python)
            .arg(worker)
            .arg(snapshot)
            .arg(queue_path)
            .arg(nonce.to_string())
            .arg(bridge)
            .arg(library)
            .arg(directory.join("ready.json"))
            .arg(directory.join("report.json"))
            .arg(&gate)
            .current_dir(&directory)
            .stdin(Stdio::null())
            .stdout(fs::File::create(directory.join("stdout")).map_err(|e| e.to_string())?)
            .stderr(fs::File::create(directory.join("stderr")).map_err(|e| e.to_string())?)
            .process_group(0)
            .spawn()
            .map_err(|e| format!("Pure Data source start failed: {e}"))?;
        let mut server = Self {
            pid: child.id(),
            child: Some(child),
            directory,
            gate,
            frames: source.duration_frames,
            complete: false,
        };
        let deadline = Instant::now() + Duration::from_secs(5);
        let compiled = server.directory.join("begin.compiled");
        loop {
            server.check()?;
            if compiled.exists() {
                if read_json(&compiled)?
                    != json!({"version":1,"sample_rate":48000,"blocksize":64,"channels":2})
                {
                    return Err("invalid Pure Data source initialization report".into());
                }
                break;
            }
            if Instant::now() >= deadline {
                return Err("Pure Data source initialization timed out".into());
            }
            thread::sleep(Duration::from_millis(1));
        }
        Ok((server, queue))
    }
    pub(crate) fn pid(&self) -> u32 {
        self.pid
    }
    pub(crate) fn arm(&mut self) -> Result<(), String> {
        OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&self.gate)
            .map_err(|e| e.to_string())?;
        Ok(())
    }
    pub(crate) fn check(&mut self) -> Result<(), String> {
        for name in ["stdout", "stderr"] {
            let meta =
                fs::symlink_metadata(self.directory.join(name)).map_err(|e| e.to_string())?;
            if !meta.is_file() || meta.len() > 65536 {
                return Err("Pure Data diagnostics exceeded regular 64 KiB limit".into());
            }
        }
        if self.complete {
            return Ok(());
        }
        if let Some(child) = &mut self.child {
            if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
                if !status.success() {
                    let mut detail = String::new();
                    fs::File::open(self.directory.join("stderr"))
                        .map_err(|e| e.to_string())?
                        .take(1024)
                        .read_to_string(&mut detail)
                        .map_err(|e| e.to_string())?;
                    return Err(format!("owned Pure Data exited ({status}): {detail}"));
                }
                let report: Report =
                    serde_json::from_value(read_json(&self.directory.join("report.json"))?)
                        .map_err(|e| e.to_string())?;
                let blocks = self.frames.div_ceil(64);
                if report.version != 1
                    || report.source_frames != self.frames
                    || report.published_blocks != blocks
                    || report.queue_frames != blocks * 64
                    || report.source_gain_applied
                    || !report.puredata_released
                    || report.sha256.len() != 64
                    || !report.sha256.bytes().all(|c| c.is_ascii_hexdigit())
                    || report.backpressure_waits > 1_000_000
                {
                    return Err("invalid Pure Data completed source report".into());
                }
                self.complete = true;
            }
        }
        Ok(())
    }
    pub(crate) fn begin_control(
        &mut self,
        name: &str,
        value: f64,
        revision: u64,
    ) -> Result<(), String> {
        self.check()?;
        if self.complete {
            return Err("Pure Data source ended before control delivery".into());
        }
        let temporary = self.directory.join("control.partial");
        let destination = self.directory.join("control.json");
        let bytes = serde_json::to_vec(
            &json!({"version":1,"revision":revision.to_string(),"name":name,"value":value}),
        )
        .map_err(|e| e.to_string())?;
        use std::io::Write;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|e| e.to_string())?;
        file.write_all(&bytes).map_err(|e| e.to_string())?;
        drop(file);
        // Publish atomically and exclusively; one owner command is in flight.
        fs::hard_link(&temporary, &destination).map_err(|e| e.to_string())?;
        fs::remove_file(temporary).map_err(|e| e.to_string())?;
        Ok(())
    }
    pub(crate) fn poll_control(
        &mut self,
        value: f64,
        revision: u64,
    ) -> Result<Option<u64>, String> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Ack {
            version: u32,
            revision: String,
            value: f64,
            frame: u64,
        }
        self.check()?;
        let path = self.directory.join("ack.json");
        if !path.exists() {
            return Ok(None);
        }
        let ack: Ack = serde_json::from_value(read_json(&path)?).map_err(|e| e.to_string())?;
        if ack.version != 1
            || ack.revision != revision.to_string()
            || ack.value != f64::from(value as f32)
            || !ack.value.is_finite()
            || ack.frame == 0
            || ack.frame > self.frames
            || (ack.frame % 64 != 0 && ack.frame != self.frames)
        {
            return Err("invalid Pure Data receiver control acknowledgment".into());
        }
        fs::remove_file(path).map_err(|e| e.to_string())?;
        Ok(Some(ack.frame))
    }
    pub(crate) fn stop(&mut self) -> Result<(), String> {
        let Some(mut child) = self.child.take() else {
            return Ok(());
        };
        // Request graceful reset/destruction on the child owner; bound foreign hangs.
        let _ = fs::write(self.directory.join("begin.stop"), []);
        let deadline = Instant::now() + Duration::from_secs(2);
        while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        crate::csound::kill_group(self.pid);
        child.wait().map_err(|e| e.to_string())?;
        Ok(())
    }
    pub(crate) fn finish(&mut self) -> Result<(), String> {
        let deadline = Instant::now() + Duration::from_secs(2);
        while !self.complete {
            self.check()?;
            if self.complete {
                break;
            }
            if Instant::now() >= deadline {
                return Err("Pure Data source teardown timed out".into());
            }
            thread::sleep(Duration::from_millis(1));
        }
        self.stop()
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}
