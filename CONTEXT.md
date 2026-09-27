# DAW

Language for sessions, playback, and the planned timeline.

## Language

**Session**:
The saved musical arrangement, including tracks and their sound settings. Playback position and listening volume belong to the current transport, not the arrangement.

**Frame**:
One sample instant across all audio channels. A stereo frame contains a left sample and a right sample.
_Avoid_: Sample when referring to a position shared by all channels.

**Timeline position**:
A frame position within the arrangement. It can repeat during looping.

**Output position**:
The number of frames produced during a playback or render run. It continues increasing when the timeline loops.

**Clip**:
A bounded placement of notes or audio on a track. Its end is the first frame outside that placement.

**Note gate**:
The interval between a note's onset and its release trigger. A release tail may remain audible after the gate ends.
_Avoid_: Note duration when referring to the entire audible tail.

**Voice**:
One sounding instance of a note, including its release tail. Notes with the same pitch can have separate voices.

**Loop region**:
The timeline interval replayed by the transport. Its end is excluded; wrapping returns to its start.

**Listening volume**:
The output level used for audition, independent of saved track gains and WAV exports.
