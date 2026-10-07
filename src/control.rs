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
    "effect.inspect",
    "effect.set_parameter",
    "source.set_control",
    "session.replace",
    "session.update_live",
    "session.edit",
    "session.save",
    "session.load",
    "render",
    "note.preview",
    "supercollider.render",
    "supercollider.inspect",
    "csound.render",
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
    let sc_live = json!({"implemented":cfg!(all(feature="native-audio",target_os="macos")),"source_mode":"live","max_seconds":10,"sample_rate":48000,"effects":["gain"],"pause":false,"seek":false,"loop":false,"live_control_edits":cfg!(all(feature="native-audio",target_os="macos")),"max_pending_controls":8,"requires_capture_plugin":true,"session_schema_versions":[6,7,8]});
    let mut result = json!({
        "methods": METHODS,
        "devices": ["sine", "audio", "supercollider", "csound", "puredata", "drumkit", "synth", "pd_instrument"],
        "supercollider_sources": {"implemented":cfg!(unix),"schema_version":6,"preparation":"owned_nrt_float32","max_sources":4,"max_total_seconds":10,"max_program_bytes":61440,"max_points":512,"native_requires_matching_sample_rate":true,"interactive_dsp":false},
        "supercollider_programs": {"inspection":true,"format":"scgf_v2","max_bytes":crate::synthdef::MAX_BYTES,"max_controls":crate::synthdef::MAX_CONTROLS,"max_parameters":crate::synthdef::MAX_PARAMETERS,"max_ugens":crate::synthdef::MAX_UGENS,"runtime_validation":false,"session_device":true},
        "supercollider_nrt": {"implemented":cfg!(unix), "configured":std::env::var_os("DAW_SCSYNTH").is_some_and(|p| Path::new(&p).is_absolute() && Path::new(&p).is_file()), "session_device":false, "native_playback":false, "command_acknowledgements":false, "max_score_bytes":1048576, "max_seconds":10, "channels":2, "sample_format":"wav_pcm16", "worker_timeout_seconds":15},
        "live_audio": cfg!(all(feature = "native-audio", target_os = "macos")),
        "plugin_hosting": cfg!(all(feature = "vst3-live", target_os = "macos")),
        "live_parameter_edits": {"implemented":cfg!(all(feature="vst3-live",target_os="macos")),"max_pending":8,"automation_override":false},
        "parameter_metadata": {"implemented": cfg!(all(feature = "vst3-offline", target_os = "macos")), "saved_parameters_only": true, "max_parameters": 64},
        "native_vst3": {"implemented": cfg!(all(feature = "vst3-live", target_os = "macos")), "experimental": true, "isolation": "in_process_worker", "queue_frames": 1024, "sample_rate": 48000, "seek": false, "loop": false, "editors": false, "instruments": false},
        "automation": {
            "schema_version": 3, "parameters": ["gain"], "interpolation": ["step"],
            "max_lanes_per_track": session::MAX_AUTOMATION_LANES_PER_TRACK,
            "max_points": session::MAX_AUTOMATION_POINTS,
            "live_edits": false
        },
        "effects": {
            "schema_version": 3, "builtin_schema_version": 11, "kinds": ["gain", "lowpass", "delay"],
            "max_per_track": session::MAX_EFFECTS_PER_TRACK,
            "gain": {"minimum": 0, "maximum": session::MAX_EFFECT_GAIN, "default": 1.0, "unit": "linear"},
            "bypass": true, "latency_frames": 0, "automation": true,
            "routing": "serial_track_stereo", "clipping": "master_only"
        },
        "timeline_transport": {"until_stopped":cfg!(all(feature="native-audio",target_os="macos")),"until_stopped_devices":["sine","synth","drumkit","audio"],"native_only": true, "max_frame": session::MAX_FRAME, "note_chase": false, "persisted": false},
        "audio_clips": {
            "schema_version": 2, "formats": ["wav_pcm16", "wav_pcm24", "wav_pcm32"],
            "channels": [1, 2], "requires_matching_sample_rate": true,
            "max_file_bytes": assets::MAX_FILE_BYTES, "max_decoded_bytes": assets::MAX_DECODED_BYTES,
            "max_assets": assets::MAX_ASSETS, "paths": "session_directory_relative",
            "save_outside_project": false,
            "fades": {"implemented": true, "fields": ["fade_in_frames", "fade_out_frames"],
                "unit": "frames", "default": 0, "curve": "linear", "optional": true,
                "max_combined": "length_frames", "position": "clip_timeline_relative",
                "endpoints": "zero_at_first_and_last_faded_samples",
                "one_frame": "mute_edge_sample", "native": true, "export": true}
        },
        "session_schema_version": session::SCHEMA_VERSION,
        "supported_session_schema_versions": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
        "offline_au": {"implemented":cfg!(all(feature="au-offline", target_os="macos")), "schema_version":5, "sample_rate":48000, "max_seconds":10, "native_playback":false, "supported_components":[{"type":"aufx","subtype":"lpas","manufacturer":"appl"}], "parameter_values":"native", "automation":false, "max_state_bytes":65536, "worker_timeout_seconds":15},
        "offline_vst3": {"implemented": cfg!(all(feature = "vst3-offline", target_os = "macos")), "schema_version": 4, "sample_rate": 48000, "max_seconds": 10, "native_playback": cfg!(all(feature = "vst3-live", target_os = "macos")), "routing": "serial_track_stereo", "state_encoding": "hex", "max_state_bytes": session::MAX_VST3_STATE_BYTES, "max_plugins": session::MAX_VST3_PLUGINS, "max_parameters": session::MAX_VST3_PARAMETERS, "worker_timeout_seconds": 15, "latency_compensation": false},
        "sequencing": {
            "schema_version": 2, "offline": true,
            "native_requires_matching_sample_rate": true,
            "browser_audition": true,
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
            "min_seconds": render::MIN_SECONDS, "max_seconds": render::MAX_BUILTIN_RENDER_SECONDS as u64,
            "builtin_max_seconds":render::MAX_BUILTIN_RENDER_SECONDS as u64,
            "runtime_max_seconds":render::MAX_SECONDS as u64,
            "plugin_max_seconds":render::MAX_PLUGIN_RENDER_SECONDS as u64,
            "native_max_seconds":render::MAX_SECONDS as u64,
            "song_devices":["sine","synth","drumkit","audio","pd_instrument"], "song_effects":["gain","lowpass","delay"],
            "streaming":true
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
            "supercollider": {
                "session_schema_versions":[6,7,8], "track_modes":["continuous"],
                "description":"Embedded SCgf-v2 program, prepared offline as finite stereo audio.",
                "required_fields":["synthdef_hex","synth_name","duration_frames","gain","controls"],
                "parameters":{"gain":{"type":"number","unit":"linear","default":1.0,"minimum":0.0,"maximum":1.0,"finite":true,"required":true}},
                "control_values":"native_float32_arrays", "control_points":"saved_step_events", "interactive_edits":false
            },
            "audio": {
                "session_schema_versions": [2, 3, 4, 5, 6, 7, 8], "track_modes": ["sequenced"],
                "description": "Preloaded PCM WAV clips on a sequenced v2 track.",
                "parameters": {"gain": {"type":"number", "unit":"linear", "default":1.0,
                    "minimum":0.0,"maximum":1.0,"finite":true,"required":true,
                    "description":"Track amplitude multiplied by each audio clip gain."}}
            },
            "sine": {
                "session_schema_versions": [1, 2, 3, 4, 5, 6, 7, 8],
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
    });
    result["live_arrangement_edits"] = json!({"implemented":cfg!(all(feature="native-audio",target_os="macos")),"same_tracks":true,"devices":["sine","synth","drumkit","audio"],"max_pending":1});
    result["supercollider_live_transport"] = sc_live;
    result["csound_offline"] = json!({"implemented":cfg!(unix),"configured":std::env::var_os("DAW_CSOUND").is_some_and(|p| Path::new(&p).is_absolute() && Path::new(&p).is_file()),"session_device":false,"native_playback":false,"max_csd_bytes":1048576,"max_seconds":10,"channels":2,"sample_format":"wav_pcm16","worker_timeout_seconds":15,"duration":"exact_requested_frames","asset_preparation":false});
    result["csound_live_transport"] = json!({"implemented":cfg!(all(feature="native-audio",target_os="macos",target_arch="aarch64")),"source_mode":"live","schema_version":7,"session_schema_versions":[7,8],"sample_rate":48000,"max_seconds":10,"effects":["gain"],"pause":false,"seek":false,"loop":false,"live_control_edits":cfg!(all(feature="native-audio",target_os="macos",target_arch="aarch64")),"max_pending_controls":8,"requires_queue_bridge":true});
    result["csound_sources"] = json!({"implemented":cfg!(unix),"schema_version":7,"preparation":"owned_block_float64","max_sources":4,"max_total_seconds":10,"max_program_bytes":61440,"max_controls":64,"max_points":512,"ksmps":64,"native_requires_matching_sample_rate":true,"interactive_dsp":false,"runtime_abi":"csound7_double"});
    result["puredata_live_transport"] = json!({"implemented":cfg!(all(feature="native-audio",target_os="macos",target_arch="aarch64")),"source_mode":"live","schema_version":8,"sample_rate":48000,"max_seconds":10,"effects":["gain"],"pause":false,"seek":false,"loop":false,"live_control_edits":cfg!(all(feature="native-audio",target_os="macos",target_arch="aarch64")),"max_pending_controls":8,"control_acknowledgment":"receiver_message_and_block_publication","requires_queue_bridge":true,"gui":false});
    result["puredata_sources"] = json!({"implemented":cfg!(unix),"configured":std::env::var_os("DAW_LIBPD_LIBRARY").is_some_and(|p| Path::new(&p).is_absolute() && Path::new(&p).is_file()),"schema_version":8,"preparation":"owned_block_float32_to_float64","max_sources":4,"max_total_seconds":10,"max_program_bytes":61440,"max_abstractions":16,"max_controls":64,"max_points":512,"blocksize":64,"native_requires_matching_sample_rate":true,"interactive_dsp":false,"gui":false,"runtime_abi":"libpd_public_float_multi"});
    result["device_metadata"]["csound"] = json!({"session_schema_versions":[7,8],"track_modes":["continuous"],"required_fields":["program","duration_frames","gain","controls"],"description":"Embedded CSD program prepared as finite stereo float64 audio.","parameters":{"gain":{"type":"number","unit":"linear","default":1.0,"minimum":0.0,"maximum":1.0,"finite":true,"required":true}},"control_values":"native_scalar_float64","control_points":"saved_step_events","interactive_edits":false});
    result["device_metadata"]["puredata"] = json!({"session_schema_versions":[8],"track_modes":["continuous"],"required_fields":["program","abstractions","duration_frames","gain","controls"],"description":"Embedded Pd patch and explicit abstractions prepared as finite stereo audio.","parameters":{"gain":{"type":"number","unit":"linear","default":1.0,"minimum":0.0,"maximum":1.0,"finite":true,"required":true}},"control_values":"receiver_scalar_float32","control_points":"saved_step_events","interactive_edits":false});
    result["device_metadata"]["drumkit"] = json!({"session_schema_versions":[9],"track_modes":["sequenced"],"required_fields":["kit_id","gain"],"parameters":{"gain":{"type":"number","unit":"linear","default":0.45,"minimum":0,"maximum":1,"finite":true,"required":true}},"kits":["factory-v1"],"kit_id_default":"factory-v1","pads":[{"midi":36,"name":"Kick"},{"midi":38,"name":"Snare"},{"midi":42,"name":"Closed hat"}],"one_shot":true});
    result["device_metadata"]["synth"] = json!({"session_schema_versions":[9],"track_modes":["sequenced"],"required_fields":["waveform","gain","attack_ms","release_ms","cutoff_hz"],"waveforms":["saw","square"],"waveform_default":"saw","parameters":{"gain":{"type":"number","unit":"linear","default":0.12,"minimum":0,"maximum":1,"finite":true,"required":true},"attack_ms":{"type":"number","unit":"ms","default":8,"minimum":1,"maximum":2000,"finite":true,"required":true},"release_ms":{"type":"number","unit":"ms","default":90,"minimum":0,"maximum":2000,"finite":true,"required":true},"cutoff_hz":{"type":"number","unit":"Hz","default":3000,"minimum":20,"maximum":20000,"maximum_from":{"field":"session.sample_rate","factor":0.5,"exclusive":true},"finite":true,"required":true}}});
    for kind in ["sine", "audio", "supercollider", "csound", "puredata"] {
        result["device_metadata"][kind]["session_schema_versions"]
            .as_array_mut()
            .expect("metadata versions")
            .push(json!(9));
    }
    for kind in ["sine", "audio", "synth", "drumkit"] {
        result["device_metadata"][kind]["session_schema_versions"]
            .as_array_mut()
            .expect("metadata versions")
            .push(json!(10));
        result["device_metadata"][kind]["session_schema_versions"]
            .as_array_mut()
            .expect("metadata versions")
            .push(json!(11));
        result["device_metadata"][kind]["session_schema_versions"]
            .as_array_mut()
            .expect("metadata versions")
            .push(json!(12));
    }
    result["pd_instrument"] = json!({"implemented": cfg!(unix), "configured": std::env::var_os("DAW_LIBPD_LIBRARY").is_some_and(|p| Path::new(&p).is_absolute() && Path::new(&p).is_file()),
        "schema_version": 12, "sample_rate": 48000, "preset": "pd-mono-v1", "polyphony": 1,
        "block_frames": 64, "event_timing": "next_64_frame_tick", "minimum_note_frames": 64,
        "native": cfg!(all(feature="native-audio",target_os="macos")), "native_dsp_owner": "bounded_worker",
        "controls": ["cutoff"], "custom_programs": false, "gui": true,
        "reset": "seek_loop_note_on", "held_note_chase": false,
        "meter_scope": "peaks_of_consumed_256_frame_worker_blocks", "meter_lookahead_max_frames": 255});
    result["device_metadata"]["pd_instrument"] = json!({"session_schema_versions":[12],"track_modes":["sequenced"],"required_fields":["program","abstractions","controls","gain"]});
    result["effect_metadata"] = json!({
        "gain": {"schema_version":3,"parameters":{"gain":{"minimum":0.0,"maximum":4.0,"default":1.0,"unit":"linear"}},"automation":["gain"]},
        "lowpass": {"schema_version":11,"parameters":{"cutoff_hz":{"minimum":20.0,"maximum":20000.0,"default":4000.0,"unit":"Hz","below_nyquist":true}},"automation":[],"algorithm":"one_pole"},
        "delay": {"schema_version":11,"parameters":{"time_ms":{"minimum":1.0,"maximum":2000.0,"default":250.0,"unit":"ms"},"feedback":{"minimum":0.0,"maximum":0.95,"default":0.3},"mix":{"minimum":0.0,"maximum":1.0,"default":0.25}},"automation":[],"algorithm":"stereo_feedback","time_rounding":"nearest_frame"}
    });
    result["effects"]["max_delay_state_bytes"] = json!(session::MAX_DELAY_STATE_BYTES);
    result["effects"]["reset_on_seek_loop"] = json!(true);
    result["effects"]["tail_policy"] = json!("within_requested_render_length");
    result["timeline_transport"]["until_stopped_effects"] = json!(["gain", "lowpass", "delay"]);
    result["timeline_transport"]["until_stopped_devices"] =
        json!(["sine", "synth", "drumkit", "audio", "pd_instrument"]);
    result["timeline_transport"]["metronome"] =
        json!(cfg!(all(feature = "native-audio", target_os = "macos")));
    result["timeline_transport"]["metronome_details"] = json!({"implemented":cfg!(all(feature="native-audio",target_os="macos")),"default":false,"time_signature":"4/4","accent":"bar_start","frame_grid":"960_ppq_half_up","persisted":false,"exported":false,"prepared_only":true,"foreign_workers":false});
    result["timeline_transport"]["count_in_max_bars"] = json!(2);
    result["timeline_transport"]["metronome_devices"] =
        json!(["sine", "synth", "drumkit", "audio"]);
    result["timeline_transport"]["count_in_bars"] = json!({"minimum":0,"maximum":2,"default":0,"timeline_frozen":true,"click_when_metronome_off":true});
    result["note_preview"] = json!({"implemented":true,"devices":["sine","synth","drumkit","pd_instrument"],"effects":["gain","lowpass","delay"],"track_mode":"sequenced","stopped_only":true,"gate_seconds":0.25,"seconds":0.5,"format":"wav_pcm16","channels":2,"revision_required":true,"changes_session":false,"mixer":"saved_gain_pan_ignore_mute_solo","automation":"saved_at_preview_frame_zero","tail_policy":"truncate_at_0.5_seconds"});
    result["mixer"] = json!({"implemented":true,"schema_version":10,"devices":["sine","synth","drumkit","audio","pd_instrument"],"effects":["gain","lowpass","delay"],"gain":{"minimum":0.0,"maximum":2.0,"default":1.0},"pan":{"minimum":-1.0,"maximum":1.0,"default":0.0,"law":"stereo_balance_cosine"},"mute_default":false,"solo_default":false,"solo":"inclusive_mute_overrides","position":"post_effect_pre_master","meters":{"native":cfg!(all(feature="native-audio",target_os="macos")),"scope":"peaks_of_consumed_256_frame_worker_blocks","lookahead_max_frames":255,"peak":"absolute_linear","clipped_at":1.0,"master":"pre_clamp_pre_monitor"}});
    result
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
struct EffectParameterParams {
    expected_revision: String,
    track_id: String,
    effect_id: String,
    parameter_id: u32,
    value: f64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceControlParams {
    expected_revision: String,
    track_id: String,
    control_name: String,
    values: Vec<f64>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct EffectInspectParams {
    track_id: String,
    effect_id: String,
}

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
struct SuperColliderRenderParams {
    score_path: String,
    path: String,
    sample_rate: u32,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SuperColliderInspectParams {
    synthdef_hex: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CsoundRenderParams {
    csd_path: String,
    path: String,
    sample_rate: u32,
    duration_frames: u64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RenderParams {
    path: String,
    seconds: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NotePreviewParams {
    expected_revision: String,
    track_id: String,
    frequency_hz: f64,
    velocity: f64,
    path: String,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PlayParams {
    #[serde(default)]
    metronome: bool,
    #[serde(default)]
    count_in_bars: u8,
    #[serde(default, deserialize_with = "deserialize_seconds")]
    seconds: Option<f64>,
    #[serde(default)]
    until_stopped: bool,
    volume: f64,
    #[serde(default)]
    source_mode: SourceMode,
}
fn deserialize_seconds<'de, D: serde::Deserializer<'de>>(d: D) -> Result<Option<f64>, D::Error> {
    f64::deserialize(d).map(Some)
}
#[derive(Deserialize, Default, PartialEq)]
#[serde(rename_all = "snake_case")]
enum SourceMode {
    #[default]
    Prepared,
    Live,
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
            "source.set_control" => {
                let p: SourceControlParams = params(value)?;
                self.check_revision(&p.expected_revision)?;
                if p.track_id.is_empty()
                    || p.track_id.len() > 128
                    || p.control_name.is_empty()
                    || p.control_name.len() > 255
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "source control requires bounded track/control names",
                    ));
                }
                let revision = self.revision.checked_add(1).ok_or_else(|| {
                    ControlError::new("revision_exhausted", "session revision is exhausted")
                })?;
                let mut updated = self.session.clone();
                let track = updated
                    .tracks
                    .iter_mut()
                    .find(|track| track.id == p.track_id)
                    .ok_or_else(|| ControlError::new("invalid_params", "track not found"))?;
                let mut pd_target = false;
                let native_index = match &mut track.device {
                    session::Device::Supercollider(source) => {
                        let program = crate::synthdef::inspect(
                            &crate::synthdef::decode_hex(&source.synthdef_hex)
                                .map_err(|e| ControlError::new("invalid_params", e))?,
                        )
                        .map_err(|e| ControlError::new("invalid_params", e))?;
                        let native = program
                            .controls
                            .iter()
                            .find(|control| control.name == p.control_name)
                            .ok_or_else(|| {
                                ControlError::new("invalid_params", "native control not found")
                            })?;
                        if program.scalar_parameters
                            [native.index..native.index + native.default_values.len()]
                            .iter()
                            .any(|value| *value)
                        {
                            return Err(ControlError::new(
                                "invalid_params",
                                "initialization-rate controls cannot change while playing",
                            ));
                        }
                        let control = source
                            .controls
                            .iter_mut()
                            .find(|control| control.name == p.control_name)
                            .ok_or_else(|| {
                                ControlError::new("invalid_params", "saved control not found")
                            })?;
                        if !control.points.is_empty() {
                            return Err(ControlError::new(
                                "invalid_params",
                                "live edits cannot override saved control automation",
                            ));
                        }
                        control.values = p.values.clone();
                        source.prepared = None;
                        Some(native.index)
                    }
                    session::Device::Csound(source) => {
                        if p.values.len() != 1 || !p.values[0].is_finite() {
                            return Err(ControlError::new(
                                "invalid_params",
                                "Csound controls require one finite float64 value",
                            ));
                        }
                        let control = source
                            .controls
                            .iter_mut()
                            .find(|control| control.name == p.control_name)
                            .ok_or_else(|| {
                                ControlError::new(
                                    "invalid_params",
                                    "saved Csound control not found",
                                )
                            })?;
                        if !control.points.is_empty() {
                            return Err(ControlError::new(
                                "invalid_params",
                                "live edits cannot override saved control automation",
                            ));
                        }
                        control.value = p.values[0];
                        source.prepared = None;
                        None
                    }
                    session::Device::Puredata(source) => {
                        if p.values.len() != 1 || !(p.values[0] as f32).is_finite() {
                            return Err(ControlError::new(
                                "invalid_params",
                                "Pd controls require one finite float32 value",
                            ));
                        }
                        let control = source
                            .controls
                            .iter_mut()
                            .find(|control| control.name == p.control_name)
                            .ok_or_else(|| {
                                ControlError::new("invalid_params", "saved Pd control not found")
                            })?;
                        if !control.points.is_empty() {
                            return Err(ControlError::new(
                                "invalid_params",
                                "live edits cannot override saved control automation",
                            ));
                        }
                        control.value = p.values[0];
                        source.prepared = None;
                        pd_target = true;
                        None
                    }
                    _ => {
                        return Err(ControlError::new(
                            "invalid_params",
                            "track is not a runtime source",
                        ));
                    }
                };
                updated
                    .validate()
                    .map_err(|e| ControlError::new("invalid_params", e))?;
                if serde_json::to_vec(&updated)
                    .map_err(|e| ControlError::new("invalid_params", e))?
                    .len()
                    > MAX_MESSAGE_BYTES - 4096
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "updated runtime session exceeds size limit",
                    ));
                }
                #[cfg(all(feature = "native-audio", target_os = "macos"))]
                {
                    self.transport
                        .source_control(crate::sc_session::ParameterChange {
                            track_id: p.track_id,
                            target: if let Some(index) = native_index {
                                crate::sc_session::ControlTarget::Supercollider {
                                    index: index as u32,
                                    values: p.values.iter().map(|value| *value as f32).collect(),
                                }
                            } else if pd_target {
                                crate::sc_session::ControlTarget::Puredata {
                                    name: p.control_name,
                                    value: p.values[0],
                                }
                            } else {
                                crate::sc_session::ControlTarget::Csound {
                                    name: p.control_name,
                                    value: p.values[0],
                                }
                            },
                            revision,
                        })
                        .map_err(|e| ControlError::new("audio_error", e))?;
                    self.session = updated;
                    self.revision = revision;
                    Ok(
                        json!({"revision":revision.to_string(),"session":self.session,"queued":true}),
                    )
                }
                #[cfg(not(all(feature = "native-audio", target_os = "macos")))]
                {
                    let _ = (revision, native_index, pd_target);
                    Err(ControlError::new(
                        "audio_unavailable",
                        "live source edits require native-audio on macOS",
                    ))
                }
            }
            "effect.set_parameter" => {
                let p: EffectParameterParams = params(value)?;
                self.check_revision(&p.expected_revision)?;
                if !p.value.is_finite()
                    || !(0.0..=1.0).contains(&p.value)
                    || [p.track_id.as_str(), p.effect_id.as_str()]
                        .iter()
                        .any(|id| id.is_empty() || id.len() > 128)
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "parameter requires bounded IDs and a normalized value",
                    ));
                }
                let next_revision = self.revision.checked_add(1).ok_or_else(|| {
                    ControlError::new("revision_exhausted", "session revision is exhausted")
                })?;
                let mut updated = self.session.clone();
                let track_index = updated
                    .tracks
                    .iter()
                    .position(|track| track.id == p.track_id)
                    .ok_or_else(|| ControlError::new("invalid_params", "track not found"))?;
                let effects = updated.tracks[track_index]
                    .effects
                    .as_mut()
                    .ok_or_else(|| ControlError::new("invalid_params", "effect not found"))?;
                let effect_index = effects
                    .iter()
                    .position(|effect| match effect {
                        session::Effect::Gain { id, .. }
                        | session::Effect::Lowpass { id, .. }
                        | session::Effect::Delay { id, .. }
                        | session::Effect::Vst3 { id, .. }
                        | session::Effect::Au { id, .. } => id == &p.effect_id,
                    })
                    .ok_or_else(|| ControlError::new("invalid_params", "effect not found"))?;
                let session::Effect::Vst3 {
                    bypass, parameters, ..
                } = &mut effects[effect_index]
                else {
                    return Err(ControlError::new(
                        "invalid_params",
                        "live edits require a VST3 effect",
                    ));
                };
                if *bypass {
                    return Err(ControlError::new(
                        "invalid_params",
                        "bypassed plugin has no live processor",
                    ));
                }
                let parameter = parameters
                    .iter_mut()
                    .find(|parameter| parameter.id == p.parameter_id)
                    .ok_or_else(|| {
                        ControlError::new("invalid_params", "saved parameter not found")
                    })?;
                if !parameter.points.is_empty() {
                    return Err(ControlError::new(
                        "invalid_params",
                        "live edits cannot override saved automation",
                    ));
                }
                parameter.value = p.value;
                updated
                    .validate()
                    .map_err(|error| ControlError::new("invalid_params", error))?;
                #[cfg(all(feature = "vst3-live", target_os = "macos"))]
                {
                    self.transport
                        .parameter(crate::live_plugins::ParameterChange {
                            track: track_index,
                            effect: effect_index,
                            id: p.parameter_id,
                            value: p.value,
                            revision: next_revision,
                        })
                        .map_err(|error| ControlError::new("audio_error", error))?;
                    // Queue acceptance is the commit point. DSP delivery is reported
                    // separately; no foreign initialization or snapshot replacement.
                    self.session = updated;
                    self.revision = next_revision;
                    Ok(
                        json!({"revision":self.revision.to_string(),"session":self.session,"queued":true}),
                    )
                }
                #[cfg(not(all(feature = "vst3-live", target_os = "macos")))]
                {
                    let _ = (effect_index, next_revision);
                    Err(ControlError::new(
                        "audio_unavailable",
                        "live parameter edits require vst3-live on macOS",
                    ))
                }
            }
            "effect.inspect" => {
                let p: EffectInspectParams = params(value)?;
                if [p.track_id.as_str(), p.effect_id.as_str()]
                    .iter()
                    .any(|id| id.is_empty() || id.len() > 128)
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "track/effect IDs must contain 1..128 UTF-8 bytes",
                    ));
                }
                let track = self
                    .session
                    .tracks
                    .iter()
                    .find(|track| track.id == p.track_id)
                    .ok_or_else(|| ControlError::new("invalid_params", "track not found"))?;
                let effect = track
                    .effects
                    .as_deref()
                    .unwrap_or_default()
                    .iter()
                    .find(|effect| match effect {
                        session::Effect::Gain { id, .. }
                        | session::Effect::Lowpass { id, .. }
                        | session::Effect::Delay { id, .. }
                        | session::Effect::Vst3 { id, .. }
                        | session::Effect::Au { id, .. } => id == &p.effect_id,
                    })
                    .ok_or_else(|| ControlError::new("invalid_params", "effect not found"))?;
                if !matches!(effect, session::Effect::Vst3 { .. }) {
                    return Err(ControlError::new(
                        "invalid_params",
                        "parameter metadata requires a VST3 effect",
                    ));
                }
                let parameters = crate::hosting::inspect(effect)
                    .map_err(|e| ControlError::new("plugin_error", e))?;
                Ok(
                    json!({"track_id":p.track_id,"effect_id":p.effect_id,"revision":self.revision.to_string(),"parameters":parameters}),
                )
            }
            "session.inspect" => {
                let _: EmptyParams = params(value)?;
                Ok(json!({"revision": self.revision.to_string(), "session": self.session}))
            }
            "session.replace" | "session.update_live" => {
                let mut replacement: ReplaceParams = params(value)?;
                if let Some(expected) = replacement.expected_revision.as_deref() {
                    self.check_revision(expected)?;
                }
                replacement
                    .session
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                replacement.session.asset_root = self.session.asset_root.clone();
                if method == "session.update_live" {
                    if replacement.expected_revision.is_none() {
                        return Err(ControlError::new(
                            "invalid_params",
                            "live edits require expected_revision",
                        ));
                    }
                    #[cfg(all(feature = "native-audio", target_os = "macos"))]
                    let next = self.revision.checked_add(1).ok_or_else(|| {
                        ControlError::new("revision_exhausted", "session revision exhausted")
                    })?;
                    #[cfg(all(feature = "native-audio", target_os = "macos"))]
                    self.transport
                        .update_session(&replacement.session, next)
                        .map_err(|e| ControlError::new("transport_error", e))?;
                    #[cfg(not(all(feature = "native-audio", target_os = "macos")))]
                    return Err(ControlError::new(
                        "unsupported",
                        "live edits require native macOS audio",
                    ));
                    #[cfg(all(feature = "native-audio", target_os = "macos"))]
                    {
                        self.session = replacement.session;
                        self.revision = next;
                    }
                } else {
                    self.commit_session(replacement.session)?;
                }
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
                                    Device::Sine { gain, .. }
                                    | Device::Audio { gain }
                                    | Device::Drumkit { gain, .. }
                                    | Device::Synth { gain, .. },
                                    Parameter::Gain,
                                ) => *gain = value,
                                (Device::Supercollider(source), Parameter::Gain) => {
                                    source.gain = value
                                }
                                (Device::Csound(source), Parameter::Gain) => {
                                    source.gain = value;
                                }
                                (Device::Puredata(source), Parameter::Gain) => {
                                    source.gain = value;
                                }
                                (Device::PdInstrument(instrument), Parameter::Gain) => {
                                    instrument.gain = value;
                                }
                                (
                                    Device::Audio { .. }
                                    | Device::Drumkit { .. }
                                    | Device::Synth { .. }
                                    | Device::Supercollider(_)
                                    | Device::Csound(_)
                                    | Device::Puredata(_),
                                    // Frequency belongs to each sequenced note.
                                    Parameter::FrequencyHz,
                                ) => {
                                    return Err(ControlError::new(
                                        "invalid_params",
                                        "frequency_hz is available only on sine devices",
                                    ));
                                }
                                (Device::PdInstrument(_), Parameter::FrequencyHz) => {
                                    return Err(ControlError::new(
                                        "invalid_params",
                                        "frequency_hz is available only on sine devices",
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
            "supercollider.inspect" => {
                let p: SuperColliderInspectParams = params(value)?;
                let bytes = crate::synthdef::decode_hex(&p.synthdef_hex)
                    .map_err(|e| ControlError::new("invalid_params", e))?;
                let program = crate::synthdef::inspect(&bytes)
                    .map_err(|e| ControlError::new("runtime_error", e))?;
                Ok(json!(program))
            }
            "supercollider.render" => {
                let p: SuperColliderRenderParams = params(value)?;
                if p.score_path.is_empty()
                    || p.path.is_empty()
                    || p.score_path.len() > 4096
                    || p.path.len() > 4096
                    || !(8000..=192000).contains(&p.sample_rate)
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "score_path/path must be nonempty paths up to 4096 UTF-8 bytes; sample_rate must be between 8000 and 192000",
                    ));
                }
                let report = crate::supercollider::render_score(
                    Path::new(&p.score_path),
                    Path::new(&p.path),
                    p.sample_rate,
                )
                .map_err(|e| ControlError::new("runtime_error", e))?;
                Ok(json!(report))
            }
            "csound.render" => {
                let p: CsoundRenderParams = params(value)?;
                if p.csd_path.is_empty()
                    || p.path.is_empty()
                    || p.csd_path.len() > 4096
                    || p.path.len() > 4096
                    || p.csd_path.contains('\0')
                    || p.path.contains('\0')
                    || !(8000..=192000).contains(&p.sample_rate)
                    || p.duration_frames == 0
                    || p.duration_frames > u64::from(p.sample_rate) * 10
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "csd_path/path must be nonempty paths up to 4096 UTF-8 bytes without NUL; sample_rate must be 8000..192000; duration_frames must be 1..sample_rate*10",
                    ));
                }
                let report = crate::csound::render(
                    Path::new(&p.csd_path),
                    Path::new(&p.path),
                    p.sample_rate,
                    p.duration_frames,
                )
                .map_err(|e| ControlError::new("runtime_error", e))?;
                Ok(json!(report))
            }
            "note.preview" => {
                let p: NotePreviewParams = params(value)?;
                self.check_revision(&p.expected_revision)?;
                if p.track_id.is_empty()
                    || p.track_id.len() > session::MAX_TRACK_ID_BYTES
                    || !p.frequency_hz.is_finite()
                    || p.frequency_hz <= 0.0
                    || p.frequency_hz >= self.session.sample_rate as f64 / 2.0
                    || !p.velocity.is_finite()
                    || !(0.0..=1.0).contains(&p.velocity)
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "preview requires a bounded track_id, positive frequency below Nyquist and velocity between 0 and 1",
                    ));
                }
                let mut track = self
                    .session
                    .tracks
                    .iter()
                    .find(|track| track.id == p.track_id)
                    .cloned()
                    .ok_or_else(|| {
                        ControlError::new("invalid_params", "preview track does not exist")
                    })?;
                if track.mode != Some(session::TrackMode::Sequenced)
                    || !matches!(
                        track.device,
                        Device::Sine { .. }
                            | Device::Synth { .. }
                            | Device::Drumkit { .. }
                            | Device::PdInstrument(_)
                    )
                    || track.effects.as_ref().is_some_and(|effects| {
                        effects.iter().any(|effect| {
                            !matches!(
                                effect,
                                session::Effect::Gain { .. }
                                    | session::Effect::Lowpass { .. }
                                    | session::Effect::Delay { .. }
                            )
                        })
                    })
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "preview requires a sequenced note instrument with built-in effects",
                    ));
                }
                #[cfg(all(feature = "native-audio", target_os = "macos"))]
                if self
                    .transport
                    .status()
                    .map_err(|e| ControlError::new("audio_error", e))?["state"]
                    != "stopped"
                {
                    return Err(ControlError::new(
                        "transport_active",
                        "stop transport before previewing notes",
                    ));
                }
                let frames = self.session.sample_rate as u64 / 4;
                track.clips = Some(vec![session::Clip::Notes(session::NoteClip {
                    kind: session::ClipKind::Notes,
                    id: "preview-clip".into(),
                    start_frame: 0,
                    length_frames: self.session.sample_rate as u64 / 2,
                    notes: vec![session::Note {
                        id: "preview-note".into(),
                        start_frame: 0,
                        duration_frames: frames,
                        frequency_hz: p.frequency_hz,
                        velocity: p.velocity,
                    }],
                })]);
                if let Some(mixer) = track.mixer.as_mut() {
                    mixer.mute = false;
                    mixer.solo = false;
                }
                // This owned snapshot never replaces the project or its asset root.
                let preview = Session {
                    schema_version: self.session.schema_version,
                    sample_rate: self.session.sample_rate,
                    tempo_milli_bpm: self.session.tempo_milli_bpm,
                    asset_root: self.session.asset_root.clone(),
                    tracks: vec![track],
                };
                preview
                    .validate()
                    .map_err(|e| ControlError::new("invalid_params", e))?;
                let mut engine =
                    Engine::prepare(&preview).map_err(|e| ControlError::new("runtime_error", e))?;
                let report = write_new(&p.path, |file| {
                    render::render_prepared(&mut engine, preview.sample_rate, 0.5, file)
                        .map_err(|e| ControlError::new("io_error", e))
                })?;
                Ok(json!(report))
            }
            "render" => {
                let p: RenderParams = params(value)?;
                render::validate_render_duration(&self.session, p.seconds)
                    .map_err(|e| ControlError::new("invalid_params", e))?;
                self.session
                    .validate()
                    .map_err(|e| ControlError::new("invalid_session", e))?;
                if crate::hosting::has_plugins(&self.session) {
                    let prepared = crate::plugin_render::prepare(&self.session, p.seconds)
                        .map_err(|e| ControlError::new("plugin_error", e))?;
                    let report = write_new(&p.path, |file| {
                        prepared
                            .encode(file)
                            .map_err(|e| ControlError::new("io_error", e))
                    })?;
                    return Ok(json!(report));
                }
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
        assets::prepare_with_budget(&session, crate::runtime_sources::decoded_bytes(&session))
            .map_err(|e| ControlError::new("asset_error", e))?;
        crate::runtime_sources::prepare_session(&mut session)
            .map_err(|e| ControlError::new("runtime_error", e))?;
        crate::hosting::prepare_session(&mut session)
            .map_err(|e| ControlError::new("plugin_error", e))?;
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
        let mut source_mode = SourceMode::Prepared;
        let mut until_stopped = false;
        let mut metronome = false;
        let mut count_in_bars = 0;
        match method {
            "transport.play" => {
                let p: PlayParams = params(value)?;
                metronome = p.metronome;
                count_in_bars = p.count_in_bars;
                if count_in_bars > 2 {
                    return Err(ControlError::new(
                        "invalid_params",
                        "count_in_bars must be between 0 and 2",
                    ));
                }
                if (metronome || count_in_bars != 0)
                    && (p.source_mode == SourceMode::Live
                        || !crate::metronome::supports_session(&self.session))
                {
                    return Err(ControlError::new(
                        "invalid_params",
                        "metronome and count-in require built-in prepared playback",
                    ));
                }
                until_stopped = p.until_stopped;
                volume = p.volume;
                source_mode = p.source_mode;
                if until_stopped {
                    if p.seconds.is_some() || source_mode != SourceMode::Prepared {
                        return Err(ControlError::new(
                            "invalid_params",
                            "until_stopped requires prepared mode and no seconds",
                        ));
                    }
                    crate::audio_buffer::PlaybackBuffer::validate_until_stopped(&self.session)
                        .map_err(|e| ControlError::new("invalid_params", e))?;
                } else {
                    seconds = p.seconds.ok_or_else(|| {
                        ControlError::new("invalid_params", "finite playback requires seconds")
                    })?;
                }
                if source_mode == SourceMode::Live && seconds > 10.0 {
                    return Err(ControlError::new(
                        "invalid_params",
                        "live runtime playback is limited to ten seconds",
                    ));
                }
                if !until_stopped {
                    render::validate_duration(seconds)
                        .map_err(|e| ControlError::new("invalid_params", e))?;
                }
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
        if method == "transport.play" && crate::hosting::has_au(&self.session) {
            return Err(ControlError::new(
                "audio_unavailable",
                "AU sessions support offline rendering only",
            ));
        }
        if method == "transport.play"
            && crate::hosting::has_plugins(&self.session)
            && !cfg!(all(feature = "vst3-live", target_os = "macos"))
        {
            return Err(ControlError::new(
                "audio_unavailable",
                "VST3 sessions support offline rendering only; native plugin playback is unavailable",
            ));
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
                "transport.play" if metronome || count_in_bars != 0 => {
                    self.transport.start_with_metronome(
                        &self.session,
                        if until_stopped { None } else { Some(seconds) },
                        volume,
                        metronome,
                        count_in_bars,
                    )
                }
                "transport.play" if source_mode == SourceMode::Live => {
                    self.transport.start_live(&self.session, seconds, volume)
                }
                "transport.play" if until_stopped => {
                    self.transport.start_until_stopped(&self.session, volume)
                }
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
            let _ = (
                seconds,
                frame,
                region,
                source_mode,
                until_stopped,
                metronome,
                count_in_bars,
            );
            if matches!(method, "transport.status" | "transport.stop") {
                Ok(
                    json!({"state":"stopped","level":0,"metronome":false,"count_in_remaining_frames":0}),
                )
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
