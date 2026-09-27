use crate::{
    render,
    session::{self, Session},
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    fs::{File, OpenOptions},
    io::Read,
    path::Path,
};

pub const PROTOCOL_VERSION: u32 = 1;
pub const MAX_MESSAGE_BYTES: usize = 1_048_576;
pub const METHODS: &[&str] = &[
    "capabilities",
    "session.get",
    "session.replace",
    "session.save",
    "session.load",
    "render",
    "transport.status",
    "transport.play",
    "transport.pause",
    "transport.resume",
    "transport.stop",
    "transport.volume",
];

// Discovery is additive to protocol v1. Defaults are construction suggestions;
// required session/device fields remain required during deserialization.
fn capabilities() -> Value {
    json!({
        "methods": METHODS,
        "devices": ["sine"],
        "live_audio": cfg!(all(feature = "native-audio", target_os = "macos")),
        "plugin_hosting": false,
        "session_schema_version": session::SCHEMA_VERSION,
        "max_message_bytes": MAX_MESSAGE_BYTES,
        "render": {
            "format": "wav_pcm16", "channels": 2,
            "min_seconds": render::MIN_SECONDS, "max_seconds": render::MAX_SECONDS as u64
        },
        "session": {
            "sample_rate": {
                "type": "integer", "unit": "Hz", "description": "Session sample rate.",
                "minimum": session::MIN_SAMPLE_RATE, "maximum": session::MAX_SAMPLE_RATE,
                "default": session::DEFAULT_SAMPLE_RATE
            },
            "tracks": {"min_items": 0, "max_items": session::MAX_TRACKS},
            "track_id": {
                "type": "string", "min_utf8_bytes": 1,
                "max_utf8_bytes": session::MAX_TRACK_ID_BYTES, "unique": true
            },
            "unknown_fields": "reject"
        },
        "device_metadata": {
            "sine": {
                "description": "Sine oscillator mixed equally into left and right channels.",
                "parameters": {
                    "frequency_hz": {
                        "type": "number", "unit": "Hz", "default": 440.0,
                        "description": "Oscillator frequency, strictly below half the session sample rate.",
                        "exclusive_minimum": 0.0,
                        "maximum_from": {"field": "session.sample_rate", "factor": 0.5, "exclusive": true},
                        "finite": true, "required": true
                    },
                    "gain": {
                        "type": "number", "unit": "linear", "default": 0.15,
                        "description": "Oscillator amplitude before summing and hard clipping.",
                        "minimum": session::MIN_GAIN, "maximum": session::MAX_GAIN,
                        "finite": true, "required": true
                    }
                }
            }
        },
        "file_behavior": {
            "relative_paths": "process_working_directory", "overwrite": false,
            "parent_directories": "must_exist", "max_session_bytes": MAX_MESSAGE_BYTES
        }
    })
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub protocol_version: u32,
    pub id: String,
    pub method: String,
    #[serde(default = "empty_params")]
    pub params: Value,
}
fn empty_params() -> Value {
    json!({})
}

#[derive(Debug, Serialize)]
pub struct Response {
    pub protocol_version: u32,
    pub id: Option<String>,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<ControlError>,
}

#[derive(Debug, Serialize)]
pub struct ControlError {
    pub code: &'static str,
    pub message: String,
}
impl ControlError {
    fn new(code: &'static str, message: impl ToString) -> Self {
        Self {
            code,
            message: message.to_string(),
        }
    }
}
impl Response {
    pub fn failure(id: Option<String>, code: &'static str, message: impl ToString) -> Self {
        Self {
            protocol_version: PROTOCOL_VERSION,
            id,
            ok: false,
            result: None,
            error: Some(ControlError::new(code, message)),
        }
    }
}

#[derive(Default)]
pub struct Controller {
    session: Session,
    #[cfg(all(feature = "native-audio", target_os = "macos"))]
    transport: crate::audio::Transport,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EmptyParams {}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PathParams {
    path: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplaceParams {
    session: Session,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RenderParams {
    path: String,
    seconds: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PlayParams {
    seconds: f64,
    volume: f64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct VolumeParams {
    volume: f64,
}

fn params<T: serde::de::DeserializeOwned>(value: Value) -> Result<T, ControlError> {
    serde_json::from_value(value).map_err(|e| ControlError::new("invalid_params", e))
}

impl Controller {
    pub fn handle_line(&mut self, line: &str) -> Response {
        if line.len() > MAX_MESSAGE_BYTES {
            return Response::failure(None, "request_too_large", "request exceeds 1 MiB");
        }
        let request: Request = match serde_json::from_str(line) {
            Ok(request) => request,
            Err(error) => return Response::failure(None, "invalid_request", error),
        };
        if request.id.is_empty() || request.id.len() > 128 {
            return Response::failure(None, "invalid_request", "id must be 1..128 UTF-8 bytes");
        }
        if request.protocol_version != PROTOCOL_VERSION {
            return Response::failure(
                Some(request.id),
                "unsupported_version",
                "expected protocol_version 1",
            );
        }
        let id = Some(request.id);
        match self.execute(&request.method, request.params) {
            Ok(result) => Response {
                protocol_version: PROTOCOL_VERSION,
                id,
                ok: true,
                result: Some(result),
                error: None,
            },
            Err(error) => Response {
                protocol_version: PROTOCOL_VERSION,
                id,
                ok: false,
                result: None,
                error: Some(error),
            },
        }
    }

    fn execute(&mut self, method: &str, value: Value) -> Result<Value, ControlError> {
        match method {
            "capabilities" => {
                let _: EmptyParams = params(value)?;
                Ok(capabilities())
            }
            "session.get" => {
                let _: EmptyParams = params(value)?;
                Ok(json!(self.session))
            }
            "session.replace" => {
                let replacement: ReplaceParams = params(value)?;
                replacement
                    .session
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                self.stop_transport()?;
                self.session = replacement.session;
                Ok(json!(self.session))
            }
            "session.save" => {
                let p: PathParams = params(value)?;
                let bytes = serde_json::to_vec_pretty(&self.session)
                    .map_err(|e| ControlError::new("internal_error", e))?;
                write_new(&p.path, |file| {
                    use std::io::Write;
                    file.write_all(&bytes)
                        .map_err(|e| ControlError::new("io_error", e))
                })?;
                Ok(json!({ "path": p.path }))
            }
            "session.load" => {
                let p: PathParams = params(value)?;
                let file = File::open(&p.path).map_err(|e| ControlError::new("io_error", e))?;
                let mut bytes = Vec::new();
                file.take(MAX_MESSAGE_BYTES as u64 + 1)
                    .read_to_end(&mut bytes)
                    .map_err(|e| ControlError::new("io_error", e))?;
                if bytes.len() > MAX_MESSAGE_BYTES {
                    return Err(ControlError::new(
                        "invalid_session",
                        "session file exceeds 1 MiB",
                    ));
                }
                let replacement: Session = serde_json::from_slice(&bytes)
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                replacement
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                self.stop_transport()?;
                self.session = replacement;
                Ok(json!(self.session))
            }
            "render" => {
                let p: RenderParams = params(value)?;
                render::validate_duration(p.seconds)
                    .map_err(|e| ControlError::new("invalid_params", e))?;
                self.session
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                let report = write_new(&p.path, |file| {
                    render::render(&self.session, p.seconds, file)
                        .map_err(|e| ControlError::new("io_error", e))
                })?;
                Ok(json!(report))
            }
            "transport.status" | "transport.play" | "transport.pause" | "transport.resume"
            | "transport.stop" | "transport.volume" => self.transport_command(method, value),
            _ => Err(ControlError::new(
                "unknown_method",
                format!("unknown method: {method}"),
            )),
        }
    }

    fn stop_transport(&mut self) -> Result<(), ControlError> {
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        self.transport
            .stop()
            .map_err(|e| ControlError::new("audio_error", e))?;
        Ok(())
    }

    fn transport_command(&mut self, method: &str, value: Value) -> Result<Value, ControlError> {
        let mut seconds = 0.0;
        let mut volume = 0.25;
        match method {
            "transport.play" => {
                let p: PlayParams = params(value)?;
                seconds = p.seconds;
                volume = p.volume;
                render::validate_duration(seconds)
                    .map_err(|e| ControlError::new("invalid_params", e))?;
            }
            "transport.volume" => {
                volume = params::<VolumeParams>(value)?.volume;
            }
            _ => {
                let _: EmptyParams = params(value)?;
            }
        }
        if !volume.is_finite() || !(0.0..=1.0).contains(&volume) {
            return Err(ControlError::new(
                "invalid_params",
                "volume must be between 0 and 1",
            ));
        }
        #[cfg(all(feature = "native-audio", target_os = "macos"))]
        {
            let result = match method {
                "transport.play" => self.transport.start(&self.session, seconds, volume),
                "transport.pause" => self.transport.pause(),
                "transport.resume" => self.transport.resume(),
                "transport.stop" => self.transport.stop(),
                "transport.volume" => self.transport.volume(volume),
                _ => self.transport.status(),
            };
            result.map_err(|e| ControlError::new("audio_error", e))
        }
        #[cfg(not(all(feature = "native-audio", target_os = "macos")))]
        {
            let _ = seconds;
            if matches!(method, "transport.status" | "transport.stop") {
                Ok(json!({"state":"stopped","level":0}))
            } else {
                Err(ControlError::new(
                    "audio_unavailable",
                    "build with --features native-audio on macOS",
                ))
            }
        }
    }
}

/// Never overwrite a user's output. Failed writes remove their incomplete file.
/// A process crash can leave an incomplete file, so callers should use fresh job paths.
fn write_new<T>(
    path: &str,
    write: impl FnOnce(&mut File) -> Result<T, ControlError>,
) -> Result<T, ControlError> {
    let path = Path::new(path);
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| ControlError::new("io_error", e))?;
    let result = write(&mut file).and_then(|value| {
        file.sync_all()
            .map_err(|e| ControlError::new("io_error", e))?;
        Ok(value)
    });
    drop(file);
    if result.is_err() {
        let _ = std::fs::remove_file(path);
    }
    result
}
