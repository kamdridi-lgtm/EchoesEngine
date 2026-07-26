#!/usr/bin/env python3
"""Build a deterministic, gap-free speech/non-speech activity timeline."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

SPEECH = "speech"
NON_SPEECH = "non_speech"
ALLOWED_LABELS = {SPEECH, NON_SPEECH}


@dataclass(frozen=True)
class ActivitySpan:
    label: str
    start_sample: int
    end_sample: int

    def __post_init__(self) -> None:
        if self.label not in ALLOWED_LABELS:
            raise ValueError(f"Unsupported activity label: {self.label}")
        if self.start_sample < 0 or self.end_sample <= self.start_sample:
            raise ValueError("Activity spans require 0 <= start < end")


def _segment_bounds(segment: Any) -> tuple[int, int]:
    if hasattr(segment, "start_sample") and hasattr(segment, "end_sample"):
        return int(segment.start_sample), int(segment.end_sample)
    if isinstance(segment, dict):
        return int(segment["startSample"]), int(segment["endSample"])
    if isinstance(segment, Sequence) and len(segment) == 2:
        return int(segment[0]), int(segment[1])
    raise TypeError(f"Unsupported speech segment: {segment!r}")


def build_activity_partition(total_samples: int, speech_segments: Iterable[Any]) -> list[ActivitySpan]:
    """Convert speech spans into an exact speech/non-speech partition.

    The returned spans cover [0, total_samples) without gaps or overlaps.
    Adjacent spans with the same label are merged.
    """
    if total_samples <= 0:
        raise ValueError("total_samples must be positive")

    normalized: list[tuple[int, int]] = []
    for item in speech_segments:
        start, end = _segment_bounds(item)
        if start < 0 or end <= start or end > total_samples:
            raise ValueError("Speech segment is outside the audio bounds")
        normalized.append((start, end))
    normalized.sort()

    previous_end = 0
    for start, end in normalized:
        if start < previous_end:
            raise ValueError("Speech segments must not overlap")
        previous_end = end

    timeline: list[ActivitySpan] = []
    cursor = 0
    for start, end in normalized:
        if start > cursor:
            timeline.append(ActivitySpan(NON_SPEECH, cursor, start))
        timeline.append(ActivitySpan(SPEECH, start, end))
        cursor = end
    if cursor < total_samples:
        timeline.append(ActivitySpan(NON_SPEECH, cursor, total_samples))

    merged: list[ActivitySpan] = []
    for span in timeline:
        if merged and merged[-1].label == span.label and merged[-1].end_sample == span.start_sample:
            previous = merged.pop()
            merged.append(ActivitySpan(span.label, previous.start_sample, span.end_sample))
        else:
            merged.append(span)
    return merged


def validate_partition(timeline: Sequence[ActivitySpan], total_samples: int) -> dict[str, bool]:
    labels_valid = all(span.label in ALLOWED_LABELS for span in timeline)
    positive = all(span.end_sample > span.start_sample for span in timeline)
    bounded = all(0 <= span.start_sample < span.end_sample <= total_samples for span in timeline)
    ordered = all(timeline[i].start_sample <= timeline[i + 1].start_sample for i in range(len(timeline) - 1))
    no_overlap = all(timeline[i].end_sample <= timeline[i + 1].start_sample for i in range(len(timeline) - 1))
    gap_free = bool(timeline) and timeline[0].start_sample == 0 and timeline[-1].end_sample == total_samples
    if gap_free:
        gap_free = all(timeline[i].end_sample == timeline[i + 1].start_sample for i in range(len(timeline) - 1))
    alternating = all(timeline[i].label != timeline[i + 1].label for i in range(len(timeline) - 1))
    exact_coverage = sum(span.end_sample - span.start_sample for span in timeline) == total_samples
    return {
        "labelsValid": labels_valid,
        "positiveDuration": positive,
        "bounded": bounded,
        "ordered": ordered,
        "nonOverlapping": no_overlap,
        "gapFree": gap_free,
        "alternatingLabels": alternating,
        "exactCoverage": exact_coverage,
    }


def canonical_payload(timeline: Sequence[ActivitySpan], sample_rate: int, total_samples: int) -> dict[str, Any]:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return {
        "schema": "echoes.song-activity-timeline-canonical.v1",
        "sampleRate": sample_rate,
        "totalSamples": total_samples,
        "spans": [
            {
                "label": span.label,
                "startSample": span.start_sample,
                "endSample": span.end_sample,
            }
            for span in timeline
        ],
    }


def canonical_digest(timeline: Sequence[ActivitySpan], sample_rate: int, total_samples: int) -> str:
    encoded = json.dumps(
        canonical_payload(timeline, sample_rate, total_samples),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def timeline_records(timeline: Sequence[ActivitySpan], sample_rate: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, span in enumerate(timeline):
        start_seconds = span.start_sample / sample_rate
        end_seconds = span.end_sample / sample_rate
        records.append(
            {
                "index": index,
                "label": span.label,
                "startSample": span.start_sample,
                "endSample": span.end_sample,
                "startSeconds": round(start_seconds, 6),
                "endSeconds": round(end_seconds, 6),
                "durationSeconds": round(end_seconds - start_seconds, 6),
                "editingCue": "voice_focus" if span.label == SPEECH else "music_or_ambient_focus",
            }
        )
    return records


def summarize(timeline: Sequence[ActivitySpan], sample_rate: int, total_samples: int) -> dict[str, Any]:
    speech_samples = sum(span.end_sample - span.start_sample for span in timeline if span.label == SPEECH)
    non_speech_samples = total_samples - speech_samples
    return {
        "durationSeconds": round(total_samples / sample_rate, 6),
        "spanCount": len(timeline),
        "speechSpanCount": sum(span.label == SPEECH for span in timeline),
        "nonSpeechSpanCount": sum(span.label == NON_SPEECH for span in timeline),
        "speechSamples": speech_samples,
        "nonSpeechSamples": non_speech_samples,
        "speechSeconds": round(speech_samples / sample_rate, 6),
        "nonSpeechSeconds": round(non_speech_samples / sample_rate, 6),
        "speechCoverage": round(speech_samples / total_samples, 9),
        "nonSpeechCoverage": round(non_speech_samples / total_samples, 9),
    }


def all_checks_pass(checks: dict[str, bool]) -> bool:
    return bool(checks) and all(checks.values())


def finite_number(value: float) -> bool:
    return math.isfinite(value)
