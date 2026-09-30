//! Scriptable sine/note engine with offline rendering and optional macOS playback.
pub mod assets;
#[cfg(all(feature = "native-audio", target_os = "macos"))]
pub mod audio;
pub mod audio_buffer;
pub mod control;
mod effects;
pub mod engine;
pub mod render;
pub mod session;
