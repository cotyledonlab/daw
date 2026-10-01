//! Owned realtime SuperCollider process and bounded OSC control adapter.
//! This module is macOS-only and never enters the native audio callback.

#[cfg(not(target_os = "macos"))]
use std::path::Path;

#[derive(Debug, Clone, PartialEq)]
pub(crate) enum Arg {
    Int(i32),
    Float(f32),
    String(String),
    Blob(Vec<u8>),
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) enum ReplyArg {
    Int(i32),
    Float(f32),
    Double(f64),
    String(String),
}

#[cfg(target_os = "macos")]
mod macos {
    use super::{Arg, ReplyArg};
    use std::{
        ffi::OsStr,
        fs,
        io::{self, Read},
        net::UdpSocket,
        os::unix::{fs::PermissionsExt, io::AsRawFd, process::CommandExt},
        path::{Path, PathBuf},
        process::{Child, Command, Stdio},
        sync::{
            Arc, Mutex,
            atomic::{AtomicBool, Ordering},
        },
        thread::{self, JoinHandle},
        time::{Duration, Instant},
    };
    use tempfile::TempDir;

    const MAX_PACKET: usize = 65_507;
    const MAX_LOG: usize = 65_536;
    const MAX_LSOF: usize = 16_384;
    const WAIT: Duration = Duration::from_secs(2);
    const CHILD_POLL: Duration = Duration::from_millis(50);

    #[derive(Default)]
    struct Logs {
        bytes: Vec<u8>,
        exceeded: bool,
        ready: bool,
        error: bool,
    }

    pub(crate) struct Server {
        child: Option<Child>,
        pid: u32,
        socket: UdpSocket,
        endpoint: std::net::SocketAddr,
        _temp: TempDir,
        logs: Arc<Mutex<Logs>>,
        readers: Vec<JoinHandle<()>>,
        next_sync: i32,
        stop_readers: Arc<AtomicBool>,
    }

    impl Server {
        pub(crate) fn start(queue_path: &Path, nonce: u64) -> Result<Self, String> {
            if nonce == 0 || !queue_path.is_absolute() || !queue_path.is_file() {
                return Err("invalid SuperCollider stream queue or nonce".into());
            }
            let configured = std::env::var_os("DAW_SCSYNTH")
                .ok_or("DAW_SCSYNTH must name an absolute scsynth executable")?;
            let executable = PathBuf::from(configured);
            validate_executable(&executable)?;
            let builtins = executable
                .parent()
                .ok_or("scsynth executable has no parent directory")?
                .join("plugins");
            let custom = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("output/sc-stream/plugins");
            for directory in [&builtins, &custom] {
                if !directory.is_absolute() || !directory.is_dir() {
                    return Err(format!(
                        "SuperCollider plugin directory unavailable: {}",
                        directory.display()
                    ));
                }
                if directory.as_os_str().to_string_lossy().contains(':') {
                    return Err("SuperCollider -U plugin paths cannot contain ':'".into());
                }
            }
            if queue_path.as_os_str().to_string_lossy().contains('\0') {
                return Err("invalid queue path".into());
            }
            let temp = tempfile::Builder::new()
                .prefix("daw-sc-owned-")
                .tempdir()
                .map_err(|error| format!("SuperCollider private directory failed: {error}"))?;
            fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).map_err(
                |error| format!("SuperCollider private directory permissions failed: {error}"),
            )?;

            let mut command = Command::new(&executable);
            command
                .args([
                    OsStr::new("-u"),
                    OsStr::new("0"),
                    OsStr::new("-B"),
                    OsStr::new("127.0.0.1"),
                    OsStr::new("-i"),
                    OsStr::new("0"),
                    OsStr::new("-o"),
                    OsStr::new("2"),
                    OsStr::new("-S"),
                    OsStr::new("48000"),
                    OsStr::new("-z"),
                    OsStr::new("64"),
                    OsStr::new("-a"),
                    OsStr::new("32"),
                    OsStr::new("-b"),
                    OsStr::new("16"),
                    OsStr::new("-n"),
                    OsStr::new("64"),
                    OsStr::new("-d"),
                    OsStr::new("16"),
                    OsStr::new("-D"),
                    OsStr::new("0"),
                    OsStr::new("-R"),
                    OsStr::new("0"),
                    OsStr::new("-P"),
                    temp.path().as_os_str(),
                    OsStr::new("-U"),
                ])
                .arg(format!("{}:{}", builtins.display(), custom.display()))
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .env_remove("SC_PLUGIN_PATH")
                .env("DAW_SC_STREAM_PATH", queue_path)
                .env("DAW_SC_STREAM_NONCE", nonce.to_string())
                .process_group(0);
            let socket = UdpSocket::bind(("127.0.0.1", 0))
                .map_err(|error| format!("loopback OSC socket failed: {error}"))?;
            socket
                .set_read_timeout(Some(CHILD_POLL))
                .map_err(|error| format!("OSC socket timeout setup failed: {error}"))?;
            let mut child = command
                .spawn()
                .map_err(|error| format!("scsynth start failed: {error}"))?;
            let pid = child.id();
            let logs = Arc::new(Mutex::new(Logs::default()));
            let mut readers = Vec::new();
            let stop_readers = Arc::new(AtomicBool::new(false));
            let stdout = child.stdout.take().unwrap();
            let stderr = child.stderr.take().unwrap();
            if nonblocking(stdout.as_raw_fd()).is_err() || nonblocking(stderr.as_raw_fd()).is_err()
            {
                terminate(&mut child, pid);
                return Err("cannot configure bounded SC diagnostic readers".into());
            }
            let streams: [Box<dyn Read + Send>; 2] = [Box::new(stdout), Box::new(stderr)];
            for stream in streams {
                let shared = Arc::clone(&logs);
                let stop = Arc::clone(&stop_readers);
                readers.push(thread::spawn(move || drain(stream, shared, stop)));
            }
            let mut server = Self {
                child: Some(child),
                pid,
                socket,
                endpoint: std::net::SocketAddr::from(([127, 0, 0, 1], 0)),
                _temp: temp,
                logs,
                readers,
                next_sync: 1,
                stop_readers,
            };
            let startup_deadline = Instant::now() + Duration::from_secs(5);
            loop {
                server.check()?;
                if server.logs.lock().unwrap().ready {
                    break;
                }
                if Instant::now() >= startup_deadline {
                    return Err("scsynth readiness timed out".into());
                }
                thread::sleep(CHILD_POLL);
            }
            server.endpoint = owned_endpoint(pid, startup_deadline)?;
            server.check()?;
            server.send("/status", &[])?;
            let remaining = startup_deadline.saturating_duration_since(Instant::now());
            let status =
                server.wait_for(WAIT.min(remaining), |address, _| address == "/status.reply")?;
            if status.len() != 9 || status.get(7) != Some(&ReplyArg::Double(48_000.0)) {
                return Err(format!(
                    "unexpected scsynth status or sample rate: {status:?}"
                ));
            }
            Ok(server)
        }

        pub(crate) fn check(&mut self) -> Result<(), String> {
            if crate::sc_session::interrupted() {
                return Err("SC session interrupted".into());
            }
            check_parts(
                self.child
                    .as_mut()
                    .ok_or("scsynth child is already closed")?,
                self.pid,
                &self.logs,
            )
        }

        pub(crate) fn pid(&self) -> u32 {
            self.pid
        }

        pub(crate) fn read_buffer(
            &mut self,
            buffer: i32,
            offset: i32,
            count: i32,
        ) -> Result<Vec<f32>, String> {
            if !(1..=256).contains(&count) || offset < 0 {
                return Err("invalid buffer read range".into());
            }
            self.send(
                "/b_getn",
                &[Arg::Int(buffer), Arg::Int(offset), Arg::Int(count)],
            )?;
            let values = self.wait_for(WAIT, |reply, values| {
                reply == "/b_setn"
                    && values.get(..3)
                        == Some(
                            &[
                                ReplyArg::Int(buffer),
                                ReplyArg::Int(offset),
                                ReplyArg::Int(count),
                            ][..],
                        )
            })?;
            if values.len() != count as usize + 3 {
                return Err("invalid buffer reply length".into());
            }
            values[3..]
                .iter()
                .map(|value| match value {
                    ReplyArg::Float(sample) if sample.is_finite() => Ok(*sample),
                    _ => Err("invalid buffer sample".into()),
                })
                .collect()
        }

        pub(crate) fn command(&mut self, address: &str, args: &[Arg]) -> Result<(), String> {
            self.send(address, args)
        }

        pub(crate) fn done(
            &mut self,
            address: &str,
            args: &[Arg],
        ) -> Result<Vec<ReplyArg>, String> {
            self.send(address, args)?;
            let expected = address.to_owned();
            self.wait_for(WAIT, |reply, values| {
                reply == "/done" && values.first() == Some(&ReplyArg::String(expected.clone()))
            })
        }

        pub(crate) fn sync(&mut self) -> Result<(), String> {
            let id = self.next_sync;
            self.next_sync = self.next_sync.wrapping_add(1).max(1);
            self.send("/sync", &[Arg::Int(id)])?;
            self.wait_for(WAIT, |reply, values| {
                reply == "/synced" && values.first() == Some(&ReplyArg::Int(id))
            })?;
            Ok(())
        }

        pub(crate) fn node(
            &mut self,
            address: &str,
            node_id: i32,
            args: &[Arg],
        ) -> Result<Vec<ReplyArg>, String> {
            if !matches!(address, "/g_new" | "/n_free") {
                return Err("node acknowledgment supports /g_new or /n_free".into());
            }
            let mut values = Vec::with_capacity(args.len() + 1);
            values.push(Arg::Int(node_id));
            values.extend_from_slice(args);
            self.send(address, &values)?;
            let expected = if address == "/n_free" {
                "/n_end"
            } else {
                "/n_go"
            };
            self.wait_for(WAIT, |reply, values| {
                reply == expected && values.first() == Some(&ReplyArg::Int(node_id))
            })
        }

        pub(crate) fn synth(
            &mut self,
            name: &str,
            node_id: i32,
            add_action: i32,
            group: i32,
            controls: &[Arg],
        ) -> Result<(), String> {
            if name.is_empty() || name.as_bytes().contains(&0) {
                return Err("invalid SuperCollider SynthDef name".into());
            }
            let mut values = Vec::with_capacity(4 + controls.len());
            values.push(Arg::String(name.to_owned()));
            values.push(Arg::Int(node_id));
            values.push(Arg::Int(add_action));
            values.push(Arg::Int(group));
            values.extend_from_slice(controls);
            self.send("/s_new", &values)?;
            self.wait_for(WAIT, |reply, values| {
                reply == "/n_go" && values.first() == Some(&ReplyArg::Int(node_id))
            })?;
            Ok(())
        }

        pub(crate) fn definition(&mut self, bytes: &[u8]) -> Result<Vec<ReplyArg>, String> {
            self.done("/d_recv", &[Arg::Blob(bytes.to_vec())])
        }

        pub(crate) fn pause_group(&mut self, group: i32, paused: bool) -> Result<(), String> {
            self.command("/n_run", &[Arg::Int(group), Arg::Int(i32::from(!paused))])?;
            self.sync()
        }

        pub(crate) fn schedule(
            &mut self,
            unix_seconds: f64,
            address: &str,
            args: &[Arg],
        ) -> Result<(), String> {
            if !unix_seconds.is_finite() {
                return Err("OSC bundle timestamp must be finite".into());
            }
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|_| "system clock predates Unix epoch")?
                .as_secs_f64();
            if unix_seconds <= now || unix_seconds > now + 60.0 {
                return Err("OSC bundle timestamp must be within the next 60 seconds".into());
            }
            let ntp = unix_seconds + 2_208_988_800.0;
            let seconds = ntp.floor();
            if !(0.0..=f64::from(u32::MAX)).contains(&seconds) {
                return Err("OSC bundle timestamp is outside NTP era zero".into());
            }
            let fraction = ((ntp - seconds) * 4_294_967_296.0).floor() as u32;
            let message = encode(address, args)?;
            let element_size =
                i32::try_from(message.len()).map_err(|_| "OSC bundle element is too large")?;
            let mut bundle = b"#bundle\0".to_vec();
            bundle.extend_from_slice(&(seconds as u32).to_be_bytes());
            bundle.extend_from_slice(&fraction.to_be_bytes());
            bundle.extend_from_slice(&element_size.to_be_bytes());
            bundle.extend_from_slice(&message);
            if bundle.len() > MAX_PACKET {
                return Err("OSC bundle exceeds UDP packet limit".into());
            }
            self.check()?;
            self.socket
                .send_to(&bundle, self.endpoint)
                .map_err(|error| format!("OSC bundle send failed: {error}"))?;
            Ok(())
        }

        fn send(&mut self, address: &str, args: &[Arg]) -> Result<(), String> {
            self.check()?;
            let packet = encode(address, args)?;
            self.socket
                .send_to(&packet, self.endpoint)
                .map_err(|error| format!("OSC send failed: {error}"))?;
            Ok(())
        }

        fn wait_for(
            &mut self,
            timeout: Duration,
            mut matches: impl FnMut(&str, &[ReplyArg]) -> bool,
        ) -> Result<Vec<ReplyArg>, String> {
            let deadline = Instant::now() + timeout;
            let mut buffer = [0u8; MAX_PACKET + 1];
            loop {
                self.check()?;
                let now = Instant::now();
                if now >= deadline {
                    return Err("OSC acknowledgement timed out".into());
                }
                self.socket
                    .set_read_timeout(Some((deadline - now).min(CHILD_POLL)))
                    .map_err(|error| format!("OSC receive timeout setup failed: {error}"))?;
                match self.socket.recv_from(&mut buffer) {
                    Ok((size, sender)) => {
                        if sender != self.endpoint {
                            continue;
                        }
                        if size > MAX_PACKET {
                            return Err("OSC reply exceeds UDP packet limit".into());
                        }
                        let (address, values) = decode(&buffer[..size])?;
                        if address == "/fail" {
                            return Err(format!("scsynth command failed: {values:?}"));
                        }
                        if matches(&address, &values) {
                            return Ok(values);
                        }
                    }
                    Err(error)
                        if matches!(
                            error.kind(),
                            io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                        ) => {}
                    Err(error) => return Err(format!("OSC receive failed: {error}")),
                }
            }
        }
    }

    impl Drop for Server {
        fn drop(&mut self) {
            if let Some(mut child) = self.child.take() {
                terminate(&mut child, self.pid);
            }
            self.stop_readers.store(true, Ordering::Release);
            join_readers(std::mem::take(&mut self.readers));
        }
    }

    fn validate_executable(path: &Path) -> Result<(), String> {
        if !path.is_absolute() {
            return Err("DAW_SCSYNTH must be an absolute executable path".into());
        }
        let metadata =
            fs::metadata(path).map_err(|error| format!("DAW_SCSYNTH unavailable: {error}"))?;
        if !metadata.is_file() || metadata.permissions().mode() & 0o111 == 0 {
            return Err("DAW_SCSYNTH must be an existing regular executable".into());
        }
        Ok(())
    }

    fn drain(mut stream: impl Read, shared: Arc<Mutex<Logs>>, stop: Arc<AtomicBool>) {
        let mut buffer = [0u8; 4096];
        while !stop.load(Ordering::Acquire) {
            let size = match stream.read(&mut buffer) {
                Ok(0) => return,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    thread::sleep(Duration::from_millis(10));
                    continue;
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => return,
                Ok(size) => size,
            };
            let mut logs = shared.lock().unwrap();
            let room = MAX_LOG.saturating_sub(logs.bytes.len());
            let take = room.min(size);
            logs.bytes.extend_from_slice(&buffer[..take]);
            if take < size {
                logs.exceeded = true;
            }
            let text = String::from_utf8_lossy(&logs.bytes).to_ascii_lowercase();
            logs.error = text.contains("failure in server") || text.contains("exception in ");
            if logs
                .bytes
                .windows(b"server ready".len())
                .any(|w| w == b"server ready")
            {
                logs.ready = true;
            }
        }
    }

    fn check_parts(child: &mut Child, pid: u32, shared: &Arc<Mutex<Logs>>) -> Result<(), String> {
        let logs = shared.lock().unwrap();
        if logs.exceeded {
            drop(logs);
            kill_group(pid, 9);
            let _ = child.wait();
            return Err("scsynth stdout/stderr exceeded combined 64 KiB".into());
        }
        if logs.error {
            drop(logs);
            kill_group(pid, 9);
            let _ = child.wait();
            return Err(format!(
                "scsynth reported a server error: {}",
                log_tail(shared)
            ));
        }
        drop(logs);
        match child.try_wait() {
            Ok(Some(status)) => Err(format!(
                "owned scsynth exited ({status}): {}",
                log_tail(shared)
            )),
            Ok(None) => Ok(()),
            Err(error) => Err(format!("scsynth child status failed: {error}")),
        }
    }

    fn log_tail(shared: &Arc<Mutex<Logs>>) -> String {
        let logs = shared.lock().unwrap();
        let start = logs.bytes.len().saturating_sub(1024);
        String::from_utf8_lossy(&logs.bytes[start..]).into_owned()
    }

    fn owned_endpoint(pid: u32, startup_deadline: Instant) -> Result<std::net::SocketAddr, String> {
        let deadline = Instant::now() + Duration::from_secs(2);
        let deadline = deadline.min(startup_deadline);
        let pid_string = pid.to_string();
        let mut command = Command::new("/usr/sbin/lsof")
            .args(["-nP", "-a", "-p", pid_string.as_str(), "-iUDP", "-Fn"])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| format!("lsof start failed: {error}"))?;
        let mut stdout = command.stdout.take().unwrap();
        let reader = thread::spawn(move || {
            let mut bytes = Vec::new();
            let _ = (&mut stdout)
                .take((MAX_LSOF + 1) as u64)
                .read_to_end(&mut bytes);
            bytes
        });
        loop {
            match command.try_wait() {
                Ok(Some(status)) => {
                    let bytes = reader.join().map_err(|_| "lsof output reader failed")?;
                    if !status.success() || bytes.len() > MAX_LSOF {
                        return Err("cannot inspect owned scsynth UDP endpoint".into());
                    }
                    let text = String::from_utf8_lossy(&bytes);
                    let names: Vec<_> = text
                        .lines()
                        .filter_map(|line| line.strip_prefix('n'))
                        .collect();
                    if names.len() != 1 {
                        return Err("cannot prove one owned scsynth UDP endpoint".into());
                    }
                    let name = names[0];
                    let port = name
                        .strip_prefix("127.0.0.1:")
                        .ok_or("owned scsynth UDP endpoint is not loopback")?
                        .parse::<u16>()
                        .map_err(|_| "invalid owned scsynth UDP port")?;
                    if port == 0 {
                        return Err("owned scsynth UDP endpoint has port zero".into());
                    }
                    return Ok(std::net::SocketAddr::from(([127, 0, 0, 1], port)));
                }
                Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(20)),
                Ok(None) => {
                    let _ = command.kill();
                    let _ = command.wait();
                    let _ = reader.join();
                    return Err("owned scsynth endpoint lookup timed out".into());
                }
                Err(error) => {
                    let _ = command.kill();
                    let _ = command.wait();
                    let _ = reader.join();
                    return Err(format!("owned scsynth endpoint lookup failed: {error}"));
                }
            }
        }
    }

    unsafe extern "C" {
        fn kill(pid: i32, signal: i32) -> i32;
        fn fcntl(fd: i32, command: i32, ...) -> i32;
    }

    fn nonblocking(fd: i32) -> Result<(), String> {
        // SAFETY: owned live pipe fd; macOS F_GETFL=3, F_SETFL=4, O_NONBLOCK=4.
        unsafe {
            let flags = fcntl(fd, 3);
            if flags < 0 || fcntl(fd, 4, flags | 4) < 0 {
                return Err(io::Error::last_os_error().to_string());
            }
        }
        Ok(())
    }
    fn kill_group(pid: u32, signal: i32) {
        // SAFETY: CommandExt::process_group(0) gives this exact child a distinct group.
        unsafe {
            kill(-(pid as i32), signal);
        }
    }

    fn terminate(child: &mut Child, pid: u32) {
        kill_group(pid, 15);
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() < deadline => thread::sleep(CHILD_POLL),
                _ => {
                    kill_group(pid, 9);
                    let _ = child.wait();
                    break;
                }
            }
        }
        // Also stop any child in the group that inherited stdout/stderr pipes.
        kill_group(pid, 9);
    }

    fn join_readers(readers: Vec<JoinHandle<()>>) {
        for reader in readers {
            let _ = reader.join();
        }
    }

    fn encode(address: &str, args: &[Arg]) -> Result<Vec<u8>, String> {
        if !address.starts_with('/') || address.as_bytes().contains(&0) {
            return Err("invalid OSC address".into());
        }
        let mut packet = Vec::new();
        put_string(&mut packet, address)?;
        let mut tags = String::from(",");
        for arg in args {
            tags.push(match arg {
                Arg::Int(_) => 'i',
                Arg::Float(value) if value.is_finite() => 'f',
                Arg::Float(_) => return Err("OSC float must be finite".into()),
                Arg::String(_) => 's',
                Arg::Blob(_) => 'b',
            });
        }
        put_string(&mut packet, &tags)?;
        for arg in args {
            match arg {
                Arg::Int(value) => packet.extend_from_slice(&value.to_be_bytes()),
                Arg::Float(value) => packet.extend_from_slice(&value.to_be_bytes()),
                Arg::String(value) => put_string(&mut packet, value)?,
                Arg::Blob(value) => {
                    let size = i32::try_from(value.len()).map_err(|_| "OSC blob is too large")?;
                    packet.extend_from_slice(&size.to_be_bytes());
                    packet.extend_from_slice(value);
                    pad(&mut packet);
                }
            }
            if packet.len() > MAX_PACKET {
                return Err("OSC request exceeds UDP packet limit".into());
            }
        }
        if packet.len() > MAX_PACKET {
            return Err("OSC request exceeds UDP packet limit".into());
        }
        Ok(packet)
    }

    fn put_string(output: &mut Vec<u8>, value: &str) -> Result<(), String> {
        if value.as_bytes().contains(&0) {
            return Err("OSC strings cannot contain NUL".into());
        }
        output.extend_from_slice(value.as_bytes());
        output.push(0);
        pad(output);
        Ok(())
    }

    fn pad(output: &mut Vec<u8>) {
        while output.len() % 4 != 0 {
            output.push(0);
        }
    }

    fn decode(packet: &[u8]) -> Result<(String, Vec<ReplyArg>), String> {
        if packet.is_empty() || packet.len() > MAX_PACKET || packet.len() % 4 != 0 {
            return Err("invalid OSC reply length".into());
        }
        let mut offset = 0;
        let address = get_string(packet, &mut offset)?;
        let tags = get_string(packet, &mut offset)?;
        if !address.starts_with('/') || !tags.starts_with(',') {
            return Err("invalid OSC reply address or type tags".into());
        }
        let mut values = Vec::new();
        for tag in tags[1..].chars() {
            let value = match tag {
                's' => ReplyArg::String(get_string(packet, &mut offset)?),
                'i' => {
                    let bytes = take(packet, &mut offset, 4)?;
                    ReplyArg::Int(i32::from_be_bytes(bytes.try_into().unwrap()))
                }
                'f' => {
                    let bytes = take(packet, &mut offset, 4)?;
                    let value = f32::from_be_bytes(bytes.try_into().unwrap());
                    if !value.is_finite() {
                        return Err("nonfinite OSC reply float".into());
                    }
                    ReplyArg::Float(value)
                }
                'd' => {
                    let bytes = take(packet, &mut offset, 8)?;
                    let value = f64::from_be_bytes(bytes.try_into().unwrap());
                    if !value.is_finite() {
                        return Err("nonfinite OSC reply double".into());
                    }
                    ReplyArg::Double(value)
                }
                _ => return Err("unsupported OSC reply type tag".into()),
            };
            values.push(value);
        }
        if offset != packet.len() {
            return Err("trailing OSC reply bytes".into());
        }
        Ok((address, values))
    }

    fn get_string(packet: &[u8], offset: &mut usize) -> Result<String, String> {
        let tail = packet.get(*offset..).ok_or("truncated OSC string")?;
        let relative_end = tail
            .iter()
            .position(|byte| *byte == 0)
            .ok_or("unterminated OSC string")?;
        let end = *offset + relative_end;
        let padded_end = (end + 4) & !3;
        if padded_end > packet.len() || packet[end..padded_end].iter().any(|byte| *byte != 0) {
            return Err("invalid OSC string padding".into());
        }
        let value = std::str::from_utf8(&packet[*offset..end])
            .map_err(|_| "OSC string is not UTF-8")?
            .to_owned();
        *offset = padded_end;
        Ok(value)
    }

    fn take<'a>(packet: &'a [u8], offset: &mut usize, size: usize) -> Result<&'a [u8], String> {
        let end = offset
            .checked_add(size)
            .ok_or("OSC reply length overflow")?;
        let value = packet.get(*offset..end).ok_or("truncated OSC argument")?;
        *offset = end;
        Ok(value)
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn osc_round_trip_scalar_arguments_and_blob_padding() {
            let packet = encode(
                "/test",
                &[
                    Arg::Int(-9),
                    Arg::Float(0.25),
                    Arg::String("hé".into()),
                    Arg::Blob(vec![1, 2, 3]),
                ],
            )
            .unwrap();
            let mut reply = encode(
                "/reply",
                &[Arg::Int(-9), Arg::Float(0.25), Arg::String("hé".into())],
            )
            .unwrap();
            assert!(packet.len() < MAX_PACKET);
            assert_eq!(
                decode(&reply).unwrap(),
                (
                    "/reply".into(),
                    vec![
                        ReplyArg::Int(-9),
                        ReplyArg::Float(0.25),
                        ReplyArg::String("hé".into()),
                    ]
                )
            );
            reply.extend_from_slice(&[0; 4]);
            assert!(decode(&reply).is_err());
        }

        #[test]
        fn osc_rejects_nonfinite_and_bad_padding() {
            assert!(encode("/x", &[Arg::Float(f32::NAN)]).is_err());
            assert!(encode("/x", &[Arg::String("a\0b".into())]).is_err());
            let mut packet = encode("/r", &[Arg::String("a".into())]).unwrap();
            let last = packet.len() - 1;
            packet[last] = 1;
            assert!(decode(&packet).is_err());
        }

        #[test]
        fn osc_reply_decoder_keeps_finite_double_precision() {
            let mut packet = Vec::new();
            put_string(&mut packet, "/reply").unwrap();
            put_string(&mut packet, ",d").unwrap();
            packet.extend_from_slice(&0.125_f64.to_be_bytes());
            assert_eq!(
                decode(&packet).unwrap(),
                ("/reply".into(), vec![ReplyArg::Double(0.125)])
            );
            let mut invalid = packet;
            let start = invalid.len() - 8;
            invalid[start..].copy_from_slice(&f64::NAN.to_be_bytes());
            assert!(decode(&invalid).is_err());
        }
    }
}

#[cfg(target_os = "macos")]
pub(crate) use macos::Server;

#[cfg(not(target_os = "macos"))]
pub(crate) struct Server;

#[cfg(not(target_os = "macos"))]
impl Server {
    pub(crate) fn start(_: &Path, _: u64) -> Result<Self, String> {
        Err("live SuperCollider streaming is available only on macOS".into())
    }
}
