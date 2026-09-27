#!/usr/bin/env python3
"""Check design-only timeline arithmetic and event-reference fixtures.

Fixture shape: a version/status envelope contains cases with input data and
hand-authored expected results. This checker independently derives tempo frames,
ordered note events, half-open gates, and a loop trace using only Python's
standard library. It checks arithmetic and event references only; it is not an
audio acceptance test, production parser, session loader, or renderer.
"""

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "sessions" / "timeline-contract.json"


def frame_at_tick(ticks, sample_rate, tempo_milli_bpm):
    n = ticks * sample_rate * 60000
    d = 960 * tempo_milli_bpm
    return (2 * n + d) // (2 * d)


def event_key(event):
    priority = {"note_off": 1, "note_on": 3}[event["kind"]]
    return (
        event["frame"],
        priority,
        event["track_id"].encode("utf-8"),
        event["clip_id"].encode("utf-8"),
        event["note_id"].encode("utf-8"),
    )


def adjacent_events(case):
    inputs = case["input"]
    events = []
    for clip in inputs["clips"]:
        for note in clip["notes"]:
            start = clip["start_frame"] + note["start_frame"]
            end = start + note["duration_frames"]
            assert clip["start_frame"] <= start < end <= clip["start_frame"] + clip["length_frames"]
            common = {key: clip[key] for key in ("track_id", "clip_id")}
            common["note_id"] = note["note_id"]
            events.extend(
                [
                    {"frame": start,
                     "kind": "note_on", **common},
                    {"frame": end,
                     "kind": "note_off", **common},
                ]
            )
    return sorted(events, key=event_key)


def gate_expectations(case):
    result = {}
    for frame in case["input"]["gate_query_frames"]:
        active = []
        for clip in case["input"]["clips"]:
            for note in clip["notes"]:
                start = clip["start_frame"] + note["start_frame"]
                end = start + note["duration_frames"]
                if start <= frame < end:
                    active.append(f"{clip['clip_id']}/{note['note_id']}")
        result[str(frame)] = sorted(active, key=lambda s: s.encode("utf-8"))
    return result


def loop_trace(case):
    data = case["input"]
    lo, hi = data["loop_start"], data["loop_end"]
    trace = []
    active = set()
    timeline_frame = data["render_start"]
    for output_frame in range(data["output_frames"]):
        if timeline_frame >= hi:
            timeline_frame = lo
            active.clear()
            if output_frame:
                trace.append({"output_frame": output_frame, "timeline_frame": lo,
                              "kind": "reset", "note_id": None})
        # Only started gates can receive offs. A seek does not chase earlier notes.
        notes = sorted(data["notes"], key=lambda note: note["note_id"].encode("utf-8"))
        for kind, field in [("note_off", "end"), ("note_on", "start")]:
            for note in notes:
                if timeline_frame != note[field]:
                    continue
                identity = note["note_id"]
                if kind == "note_off":
                    if identity not in active:
                        continue
                    active.remove(identity)
                else:
                    active.add(identity)
                trace.append({"output_frame": output_frame, "timeline_frame": timeline_frame,
                              "kind": kind, "note_id": identity})
        timeline_frame += 1
    return trace


def derive(case):
    category = case["category"]
    if category == "tempo_conversion":
        converted = []
        for group in case["input"]["conversions"]:
            converted.append({
                "sample_rate": group["sample_rate"],
                "tempo_milli_bpm": group["tempo_milli_bpm"],
                "ticks": group["ticks"],
                "frames": [frame_at_tick(t, group["sample_rate"], group["tempo_milli_bpm"])
                           for t in group["ticks"]],
            })
        return converted
    if category == "adjacent_clips":
        return {"events": adjacent_events(case),
                "active_note_ids_by_frame": gate_expectations(case)}
    if category == "simultaneous_events":
        return sorted(case["input"]["events"], key=event_key)
    if category == "loop_trace":
        return loop_trace(case)
    raise ValueError(f"unknown fixture category: {category}")


def main():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["fixture_version"] == 1, "unsupported fixture_version"
    assert fixture["status"] == "design_only_not_loadable", "fixture must remain design-only"
    assert len(fixture["cases"]) == 4, "expected four contract cases"
    for case in fixture["cases"]:
        actual = derive(case)
        if actual != case["expected"]:
            raise SystemExit(
                f"{case['id']} mismatch\nexpected: {json.dumps(case['expected'], sort_keys=True)}"
                f"\nactual:   {json.dumps(actual, sort_keys=True)}"
            )
    print(f"timeline contract reference checks passed ({len(fixture['cases'])} cases)")


if __name__ == "__main__":
    main()
