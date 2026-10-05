//! Finite runtime sources share transactional preparation and decoded asset budgets.
use crate::{
    assets::PreparedAudioClip,
    session::{Device, Session},
};
use std::{collections::HashSet, sync::Arc};

pub fn prepare_session(session: &mut Session) -> Result<(), String> {
    session.validate()?;
    for track in &session.tracks {
        if let Device::PdInstrument(instrument) = &track.device {
            instrument.preflight(session.sample_rate)?;
        }
    }
    if !session.tracks.iter().any(|track| {
        matches!(
            track.device,
            Device::Supercollider(_) | Device::Csound(_) | Device::Puredata(_)
        )
    }) {
        return Ok(());
    }
    if serde_json::to_vec_pretty(session)
        .map_err(|e| e.to_string())?
        .len()
        > crate::control::MAX_MESSAGE_BYTES - 4096
    {
        return Err("runtime source session exceeds the save/load byte limit".into());
    }
    let assets = crate::assets::prepare_with_budget(session, decoded_bytes(session))?;
    for track in &mut session.tracks {
        if let Device::Supercollider(source) = &mut track.device {
            source.prepared = Some(source.prepare(session.sample_rate)?);
        }
        if let Device::Csound(source) = &mut track.device {
            source.prepared = Some(source.prepare(session.sample_rate)?);
        }
        if let Device::Puredata(source) = &mut track.device {
            source.prepared = Some(source.prepare(session.sample_rate)?);
        }
    }
    // Audio assets and runtime PCM share the decoded-session budget.
    let runtimes = prepare_clips(session)?;
    check_budget(assets.iter().chain(&runtimes))
}

pub(crate) fn decoded_bytes(session: &Session) -> usize {
    session
        .tracks
        .iter()
        .map(|track| match &track.device {
            Device::Supercollider(source) => {
                source.duration_frames as usize * std::mem::size_of::<[f64; 2]>()
            }
            Device::Csound(source) => {
                source.duration_frames as usize * std::mem::size_of::<[f64; 2]>()
            }
            Device::Puredata(source) => {
                source.duration_frames as usize * std::mem::size_of::<[f64; 2]>()
            }
            _ => 0,
        })
        .sum()
}

pub fn prepare_clips(session: &Session) -> Result<Vec<PreparedAudioClip>, String> {
    let mut clips = Vec::new();
    for (track_index, track) in session.tracks.iter().enumerate() {
        let (duration, gain, audio) = match &track.device {
            Device::Supercollider(source) => (
                source.duration_frames,
                source.gain,
                Arc::clone(&source.prepare(session.sample_rate)?.audio),
            ),
            Device::Csound(source) => (
                source.duration_frames,
                source.gain,
                Arc::clone(&source.prepare(session.sample_rate)?.audio),
            ),
            Device::Puredata(source) => (
                source.duration_frames,
                source.gain,
                Arc::clone(&source.prepare(session.sample_rate)?.audio),
            ),
            _ => continue,
        };
        clips.push(PreparedAudioClip {
            track_index,
            start: 0,
            end: duration,
            source_offset: 0,
            fade_in_frames: 0,
            fade_out_frames: 0,
            gain,
            frames: audio,
        });
    }
    Ok(clips)
}

pub fn check_budget<'a>(clips: impl Iterator<Item = &'a PreparedAudioClip>) -> Result<(), String> {
    let mut unique = HashSet::new();
    let mut bytes = 0;
    for clip in clips {
        if unique.insert(Arc::as_ptr(&clip.frames)) {
            bytes += clip.frames.len() * std::mem::size_of::<[f64; 2]>();
        }
    }
    if bytes > crate::assets::MAX_DECODED_BYTES {
        Err("combined assets and runtime audio exceed decoded session budget".into())
    } else {
        Ok(())
    }
}
