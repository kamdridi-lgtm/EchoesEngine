#!/usr/bin/env python3
"""Convert Silero VAD frame probabilities into deterministic speech timestamps."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Iterable, Sequence

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 512


@dataclass(frozen=True)
class SegmentationConfig:
    threshold: float = 0.5
    negative_threshold: float = 0.35
    min_speech_ms: int = 250
    min_silence_ms: int = 100
    speech_pad_ms: int = 30

    def validate(self) -> None:
        if not 0.0 < self.threshold <= 1.0:
            raise ValueError("threshold must be in (0, 1]")
        if not 0.0 <= self.negative_threshold < self.threshold:
            raise ValueError("negative_threshold must be below threshold")
        if self.min_speech_ms <= 0 or self.min_silence_ms <= 0:
            raise ValueError("minimum durations must be positive")
        if self.speech_pad_ms < 0:
            raise ValueError("speech_pad_ms must be non-negative")


def _milliseconds_to_samples(milliseconds: int) -> int:
    return int(math.ceil(SAMPLE_RATE * milliseconds / 1000.0))


def _validate_probabilities(probabilities: Iterable[float]) -> list[float]:
    values = [float(value) for value in probabilities]
    if not values:
        raise ValueError("probabilities must not be empty")
    for value in values:
        if not math.isfinite(value):
            raise ValueError("probability is not finite")
        if value < 0.0 or value > 1.0:
            raise ValueError("probability is outside [0, 1]")
    return values


def speech_segments_from_probabilities(
    probabilities: Sequence[float],
    audio_samples: int,
    config: SegmentationConfig | None = None,
) -> list[dict[str, int | float]]:
    """Return padded, sorted, non-overlapping speech segments.

    The state machine mirrors Silero's default hysteresis behavior: speech starts at
    ``threshold`` and ends only after ``min_silence_ms`` below
    ``negative_threshold``. Short speech bursts are discarded before padding.
    """
    cfg = config or SegmentationConfig()
    cfg.validate()
    values = _validate_probabilities(probabilities)
    if audio_samples <= 0:
        raise ValueError("audio_samples must be positive")

    min_speech_samples = _milliseconds_to_samples(cfg.min_speech_ms)
    min_silence_samples = _milliseconds_to_samples(cfg.min_silence_ms)
    pad_samples = _milliseconds_to_samples(cfg.speech_pad_ms)

    raw_segments: list[tuple[int, int]] = []
    triggered = False
    speech_start = 0
    tentative_end: int | None = None

    for frame_index, probability in enumerate(values):
        frame_start = min(frame_index * CHUNK_SAMPLES, audio_samples)

        if probability >= cfg.threshold:
            if not triggered:
                triggered = True
                speech_start = frame_start
            tentative_end = None
            continue

        if triggered and probability < cfg.negative_threshold:
            if tentative_end is None:
                tentative_end = frame_start
            if frame_start - tentative_end >= min_silence_samples:
                speech_end = tentative_end
                if speech_end - speech_start >= min_speech_samples:
                    raw_segments.append((speech_start, speech_end))
                triggered = False
                tentative_end = None

    if triggered:
        speech_end = audio_samples
        if speech_end - speech_start >= min_speech_samples:
            raw_segments.append((speech_start, speech_end))

    padded_segments: list[tuple[int, int]] = []
    for start, end in raw_segments:
        padded_start = max(0, start - pad_samples)
        padded_end = min(audio_samples, end + pad_samples)
        if padded_segments and padded_start <= padded_segments[-1][1]:
            previous_start, previous_end = padded_segments[-1]
            padded_segments[-1] = (previous_start, max(previous_end, padded_end))
        else:
            padded_segments.append((padded_start, padded_end))

    result: list[dict[str, int | float]] = []
    for index, (start, end) in enumerate(padded_segments):
        result.append(
            {
                "index": index,
                "startSample": start,
                "endSample": end,
                "durationSamples": end - start,
                "startSeconds": round(start / SAMPLE_RATE, 6),
                "endSeconds": round(end / SAMPLE_RATE, 6),
                "durationSeconds": round((end - start) / SAMPLE_RATE, 6),
            }
        )
    return result


def validate_segments(
    segments: Sequence[dict[str, int | float]], audio_samples: int
) -> dict[str, bool]:
    nonempty = bool(segments)
    sorted_segments = True
    non_overlapping = True
    bounded = True
    positive_duration = True

    previous_end = -1
    for index, segment in enumerate(segments):
        start = int(segment["startSample"])
        end = int(segment["endSample"])
        if int(segment["index"]) != index or start < previous_end:
            sorted_segments = False
        if start < previous_end:
            non_overlapping = False
        if start < 0 or end > audio_samples:
            bounded = False
        if end <= start:
            positive_duration = False
        previous_end = end

    return {
        "segmentsNonempty": nonempty,
        "segmentsSorted": sorted_segments,
        "segmentsNonOverlapping": non_overlapping,
        "segmentsBounded": bounded,
        "segmentsPositiveDuration": positive_duration,
    }


def canonical_segments_sha256(
    segments: Sequence[dict[str, int | float]],
) -> str:
    canonical = [
        {
            "startSample": int(segment["startSample"]),
            "endSample": int(segment["endSample"]),
        }
        for segment in segments
    ]
    payload = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
