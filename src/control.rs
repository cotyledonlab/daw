use crate::{
    assets,
    engine::Engine,
    render,
    session::{self, Device, Session, Track},
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
const MAX_EDIT_OPERATIONS: usize = 128;
pub const METHODS: &[&str] = &[
    "capabilities",
    "session.get",
    "session.inspect",
    "session.replace",
    "session.edit",
    "session.save",
    "session.load",
    "render",
    "transport.status",
    "transport.play",
    "transport.pause",
    "transport.resume",
    "transport.stop",
    "transport.volume",
    "transport.seek",
    "transport.loop",
];

// Discovery is additive to protocol v1. Defaults are construction suggestions;
// required session/device fields remain required during deserialization.
fn capabilities() -> Value {
    json!({
        "methods": METHODS,
        "devices": ["sine", "audio"],
        "live_audio": cfg!(all(feature = "native-audio", target_os = "macos")),
        "plugin_hosting": false,
        "automation": {
            "schema_version": 3, "parameters": ["gain"], "interpolation": ["step"],
            "max_lanes_per_track": session::MAX_AUTOMATION_LANES_PER_TRACK,
            "max_points": session::MAX_AUTOMATION_POINTS,
            "live_edits": false
        },
        "effects": {
            "schema_version": 3, "kinds": ["gain"],
            "max_per_track": session::MAX_EFFECTS_PER_TRACK,
            "gain": {"minimum": 0, "maximum": session::MAX_EFFECT_GAIN, "default": 1.0, "unit": "linear"},
            "bypass": true, "latency_frames": 0, "automation": true,
            "routing": "serial_track_stereo", "clipping": "master_only"
        },
        "timeline_transport": {"native_only": true, "max_frame": session::MAX_FRAME, "note_chase": false, "persisted": false},
        "audio_clips": {
            "schema_version": 2, "formats": ["wav_pcm16", "wav_pcm24", "wav_pcm32"],
            "channels": [1, 2], "requires_matching_sample_rate": true,
            "max_file_bytes": assets::MAX_FILE_BYTES, "max_decoded_bytes": assets::MAX_DECODED_BYTES,
            "max_assets": assets::MAX_ASSETS, "paths": "session_directory_relative",
            "save_outside_project": false
        },
        "session_schema_version": session::SCHEMA_VERSION,
        "supported_session_schema_versions": [1, 2, 3],
        "sequencing": {
            "schema_version": 2, "offline": true,
            "native_requires_matching_sample_rate": true,
            "browser_audition": false,
            "max_clips": session::MAX_CLIPS, "max_notes": session::MAX_NOTES,
            "max_voices": session::MAX_VOICES, "max_frame": session::MAX_FRAME,
            "envelope_ms": 5, "ticks_per_quarter": 960,
            "tempo_milli_bpm": {"minimum": 20000, "maximum": 300000, "default": 120000}
        },
        "editing": {
            "max_operations": MAX_EDIT_OPERATIONS,
            "revision_type": "decimal_string",
            "operations": ["add_track", "remove_track", "set_parameter"]
        },
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
            "audio": {
                "session_schema_versions": [2, 3], "track_modes": ["sequenced"],
                "description": "Preloaded PCM WAV clips on a sequenced v2 track.",
                "parameters": {"gain": {"type":"number", "unit":"linear", "default":1.0,
                    "minimum":0.0,"maximum":1.0,"finite":true,"required":true,
                    "description":"Track amplitude multiplied by each audio clip gain."}}
            },
            "sine": {
                "session_schema_versions": [1, 2, 3],
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
    revision: u64,
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
    #[serde(default, deserialize_with = "deserialize_expected_revision")]
    expected_revision: Option<String>,
}
fn deserialize_expected_revision<'de, D>(deserializer: D) -> Result<Option<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    String::deserialize(deserializer).map(Some)
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoadParams {
    path: String,
    #[serde(default, deserialize_with = "deserialize_expected_revision")]
    expected_revision: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EditParams {
    expected_revision: String,
    operations: Vec<EditOperation>,
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum EditOperation {
    AddTrack {
        track: Track,
    },
    RemoveTrack {
        track_id: String,
    },
    SetParameter {
        track_id: String,
        parameter: Parameter,
        value: f64,
    },
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Parameter {
    FrequencyHz,
    Gain,
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
struct SeekParams {
    frame: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoopRegion {
    start_frame: u64,
    end_frame: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LoopParams {
    #[serde(deserialize_with = "deserialize_region")]
    region: Option<LoopRegion>,
}
fn deserialize_region<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> Result<Option<LoopRegion>, D::Error> {
    Option::deserialize(d)
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
            "session.inspect" => {
                let _: EmptyParams = params(value)?;
                Ok(json!({"revision": self.revision.to_string(), "session": self.session}))
            }
            "session.replace" => {
                let mut replacement: ReplaceParams = params(value)?;
                if let Some(expected) = replacement.expected_revision.as_deref() {
                    self.check_revision(expected)?;
                }
                replacement
                    .session
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                replacement.session.asset_root = self.session.asset_root.clone();
                self.commit_session(replacement.session)?;
                Ok(json!(self.session))
            }
            "session.edit" => {
                let edit: EditParams = params(value)?;
                self.check_revision(&edit.expected_revision)?;
                if edit.operations.is_empty() || edit.operations.len() > MAX_EDIT_OPERATIONS {
                    return Err(ControlError::new(
                        "invalid_params",
                        "operations must contain 1..128 items",
                    ));
                }
                let mut candidate = self.session.clone();
                for operation in edit.operations {
                    match operation {
                        EditOperation::AddTrack { track } => candidate.tracks.push(track),
                        EditOperation::RemoveTrack { track_id } => {
                            let Some(index) = candidate
                                .tracks
                                .iter()
                                .position(|track| track.id == track_id)
                            else {
                                return Err(ControlError::new(
                                    "invalid_params",
                                    format!("track not found: {track_id}"),
                                ));
                            };
                            candidate.tracks.remove(index);
                        }
                        EditOperation::SetParameter {
                            track_id,
                            parameter,
                            value,
                        } => {
                            let Some(track) = candidate
                                .tracks
                                .iter_mut()
                                .find(|track| track.id == track_id)
                            else {
                                return Err(ControlError::new(
                                    "invalid_params",
                                    format!("track not found: {track_id}"),
                                ));
                            };
                            match (&mut track.device, parameter) {
                                (Device::Sine { frequency_hz, .. }, Parameter::FrequencyHz) => {
                                    *frequency_hz = value
                                }
                                (
                                    Device::Sine { gain, .. } | Device::Audio { gain },
                                    Parameter::Gain,
                                ) => *gain = value,
                                (Device::Audio { .. }, Parameter::FrequencyHz) => {
                                    return Err(ControlError::new(
                                        "invalid_params",
                                        "audio tracks do not have frequency_hz",
                                    ));
                                }
                            }
                        }
                    }
                    candidate
                        .validate()
                        .map_err(|e| ControlError::new("invalid_session", e))?;
                }
                self.commit_session(candidate)?;
                Ok(json!({"revision": self.revision.to_string(), "session": self.session}))
            }
            "session.save" => {
                let p: PathParams = params(value)?;
                if self.session.has_audio() {
                    let parent = Path::new(&p.path)
                        .parent()
                        .filter(|p| !p.as_os_str().is_empty())
                        .unwrap_or(Path::new("."));
                    let destination_root = parent
                        .canonicalize()
                        .map_err(|e| ControlError::new("io_error", e))?;
                    let source_root = self
                        .session
                        .asset_root
                        .as_ref()
                        .expect("committed asset root");
                    if &destination_root != source_root {
                        return Err(ControlError::new(
                            "invalid_params",
                            "save audio sessions inside their project directory; asset copying is not implemented",
                        ));
                    }
                }
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
                let p: LoadParams = params(value)?;
                if let Some(expected) = p.expected_revision.as_deref() {
                    self.check_revision(expected)?;
                }
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
                let mut replacement: Session = serde_json::from_slice(&bytes)
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                replacement
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                let location = Path::new(&p.path)
                    .canonicalize()
                    .map_err(|e| ControlError::new("io_error", e))?;
                replacement.asset_root = Some(
                    location
                        .parent()
                        .ok_or_else(|| {
                            ControlError::new("io_error", "session has no parent directory")
                        })?
                        .to_path_buf(),
                );
                self.commit_session(replacement)?;
                Ok(json!(self.session))
            }
            "render" => {
                let p: RenderParams = params(value)?;
                render::validate_duration(p.seconds)
                    .map_err(|e| ControlError::new("invalid_params", e))?;
                self.session
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                let mut engine = Engine::prepare(&self.session)
                    .map_err(|e| ControlError::new("asset_error", e))?;
                let report = write_new(&p.path, |file| {
                    render::render_prepared(&mut engine, self.session.sample_rate, p.seconds, file)
                        .map_err(|e| ControlError::new("io_error", e))
                })?;
                Ok(json!(report))
            }
            "transport.status" | "transport.play" | "transport.pause" | "transport.resume"
            | "transport.stop" | "transport.volume" | "transport.seek" | "transport.loop" => {
                self.transport_command(method, value)
            }
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

    fn check_revision(&self, expected: &str) -> Result<(), ControlError> {
        let parsed = expected.parse::<u64>().map_err(|_| {
            ControlError::new(
                "invalid_params",
                "expected_revision must be a canonical decimal u64 string",
            )
        })?;
        if parsed.to_string() != expected {
            return Err(ControlError::new(
                "invalid_params",
                "expected_revision must be a canonical decimal u64 string",
            ));
        }
        if parsed != self.revision {
            return Err(ControlError::new(
                "revision_conflict",
                format!(
                    "expected revision {expected}, current revision is {}",
                    self.revision
                ),
            ));
        }
        Ok(())
    }

    fn commit_session(&mut self, mut session: Session) -> Result<(), ControlError> {
        let next_revision = self.revision.checked_add(1).ok_or_else(|| {
            ControlError::new("revision_exhausted", "session revision is exhausted")
        })?;
        if session.asset_root.is_none() {
            session.asset_root = Some(
                std::env::current_dir()
                    .map_err(|e| ControlError::new("io_error", e))?
                    .canonicalize()
                    .map_err(|e| ControlError::new("io_error", e))?,
            );
        }
        assets::prepare(&session).map_err(|e| ControlError::new("asset_error", e))?;
        self.stop_transport()?;
        self.session = session;
        self.revision = next_revision;
        Ok(())
    }

    fn transport_command(&mut self, method: &str, value: Value) -> Result<Value, ControlError> {
        let mut frame = 0;
        let mut region = None;
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
            "transport.seek" => {
                frame = params::<SeekParams>(value)?.frame;
                if frame > session::MAX_FRAME {
                    return Err(ControlError::new(
                        "invalid_params",
                        "frame exceeds timeline limit",
                    ));
                }
            }
            "transport.loop" => {
                region = params::<LoopParams>(value)?
                    .region
                    .map(|r| (r.start_frame, r.end_frame));
                if region.is_some_and(|(start, end)| start >= end || end > session::MAX_FRAME) {
                    return Err(ControlError::new(
                        "invalid_params",
                        "loop requires start < end <= timeline limit",
                    ));
                }
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
                "transport.seek" => self.transport.seek(frame),
                "transport.loop" => self.transport.set_loop(region),
                _ => self.transport.status(),
            };
            result.map_err(|e| ControlError::new("audio_error", e))
        }
        #[cfg(not(all(feature = "native-audio", target_os = "macos")))]
        {
            let _ = (seconds, frame, region);
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

#[cfg(test)]
mod revision_tests {
    use super::*;

    #[test]
    fn exhausted_revision_rejects_commit_without_changing_session() {
        let mut controller = Controller {
            revision: u64::MAX,
            ..Controller::default()
        };
        let original = controller.session.clone();
        let replacement = Session {
            sample_rate: 44_100,
            ..Session::default()
        };
        let error = controller.commit_session(replacement).unwrap_err();
        assert_eq!(error.code, "revision_exhausted");
        assert_eq!(controller.revision, u64::MAX);
        assert_eq!(controller.session, original);
    }
}
