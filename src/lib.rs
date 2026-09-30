//! Scriptable sine/note engine with offline rendering and optional macOS playback.
pub mod assets;
mod au_hosting;
#[cfg(all(feature = "native-audio", target_os = "macos"))]
pub mod audio;
pub mod audio_buffer;
pub mod control;
mod effects;
pub mod engine;
pub mod hosting;
pub mod plugin_render;
pub mod render;
pub mod session;
pub mod supercollider;

#[cfg(all(feature = "vst3-live", target_os = "macos"))]
mod live_plugins;
#[cfg(all(feature = "vst3-live", target_os = "macos"))]
mod live_ring;
