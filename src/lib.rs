//! Offline DAW core. No device drivers or foreign plugin code run in this slice.
#[cfg(all(feature = "native-audio", target_os = "macos"))]
pub mod audio;
pub mod audio_buffer;
pub mod control;
pub mod engine;
pub mod render;
pub mod session;
