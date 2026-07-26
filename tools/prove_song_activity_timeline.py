#!/usr/bin/env python3
"""Prove a deterministic, production-length speech/non-speech editing timeline."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import wave
from pathlib import Path
from typing import Any

import numpy as np

from prove_silero_vad import (
    SAMPLE_RATE,
    SAMPLE_SHA_PLACEHOLDER,
    SAMPLE_URL,
    SileroVadRunner,
    decode_wav,
    download,
    resample_linear,
    sha256_bytes,
)
from silero_speech_segments import SegmentationConfig, speech_segments_from_probabilities
from song_activity_timeline import (
    NON_SPEECH,
    SPEECH,
    all_checks_pass,
    build_activity_partition,
    canonical_digest,
    summarize,
    timeline_records,
    validate_partition,
)

SCHEMA = "echoes.song-activity-timeline-proof.v1"
EXPECTED_TIMELINE_PLACEHOLDER = "PIN_AFTER_DISCOVERY"
FIXTURE_SECONDS = 180
SPEECH_WINDOWS_SECONDS = [(20, 80), (100, 160)]
EXPECTED_SAMPLE_SHA256 = "89f17d9c94c4b31eb320f424628bcbc920abaddbee6e2760fd868bfb1d9a2e47"
EXPECTED_MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


def write_pcm16(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = np.round(clipped * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def deterministic_music(samples: int, sample_rate: int, phase_offset: float) -> np.ndarray:
    positions = np.arange(samples, dtype=np.float64) / float(sample_rate)
    tone = (
        0.18 * np.sin(2.0 * np.pi * 110.0 * positions + phase_offset)
        + 0.08 * np.sin(2.0 * np.pi * 220.0 * positions + phase_offset * 0.5)
        + 0.04 * np.sin(2.0 * np.pi * 440.0 * positions)
    )
    beat_period = max(1, sample_rate // 2)
    beat = np.zeros(samples, dtype=np.float64)
    for start in range(0, samples, beat_period):
        length = min(sample_rate // 100, samples - start)
        if length > 0:
            envelope = np.linspace(1.0, 0.0, num=length, endpoint=False)
            beat[start : start + length] = 0.12 * envelope
    return np.asarray(tone + beat, dtype=np.float32)


def create_fixture(speech: np.ndarray) -> tuple[np.ndarray, list[dict[str, int]]]:
    total_samples = FIXTURE_SECONDS * SAMPLE_RATE
    fixture = np.zeros(total_samples, dtype=np.float32)
    containers: list[dict[str, int]] = []

    non_speech_regions = [(0, 20), (80, 100), (160, 180)]
    for index, (start_seconds, end_seconds) in enumerate(non_speech_regions):
        start = start_seconds * SAMPLE_RATE
        end = end_seconds * SAMPLE_RATE
        fixture[start:end] = deterministic_music(end - start, SAMPLE_RATE, phase_offset=index * 0.7)

    required_speech_samples = 60 * SAMPLE_RATE
    if speech.size < required_speech_samples:
        raise RuntimeError(f"Official sample is shorter than 60 seconds: {speech.size}")
    speech_clip = np.asarray(speech[:required_speech_samples], dtype=np.float32)
    peak = float(np.max(np.abs(speech_clip)))
    if not math.isfinite(peak) or peak <= 0.0:
        raise RuntimeError("Official speech sample has no finite signal")
    speech_clip = np.clip(speech_clip * min(0.95 / peak, 1.0), -0.95, 0.95)

    for start_seconds, end_seconds in SPEECH_WINDOWS_SECONDS:
        start = start_seconds * SAMPLE_RATE
        end = end_seconds * SAMPLE_RATE
        if end - start != speech_clip.size:
            raise RuntimeError("Speech window does not match the pinned 60-second clip")
        fixture[start:end] = speech_clip
        containers.append({"startSample": start, "endSample": end})

    return fixture, containers


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def segment_inside_any_container(segment: dict[str, int | float], containers: list[dict[str, int]]) -> bool:
    pad = int(math.ceil(SAMPLE_RATE * 30 / 1000.0))
    start = int(segment["startSample"])
    end = int(segment["endSample"])
    return any(
        start >= max(0, item["startSample"] - pad)
        and end <= item["endSample"] + pad
        for item in containers
    )


def write_edit_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "index",
                "label",
                "startSeconds",
                "endSeconds",
                "durationSeconds",
                "editingCue",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow({name: record[name] for name in writer.fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-timeline-sha256", default=EXPECTED_TIMELINE_PLACEHOLDER)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_path = output_dir / "official-en.wav"
    fixture_path = output_dir / "production-length-activity-fixture.wav"
    proof_path = output_dir / "song-activity-timeline.json"
    csv_path = output_dir / "song-activity-timeline.csv"

    model_path = args.model.resolve()
    model_sha = sha256_file(model_path)
    sample_bytes = download(SAMPLE_URL)
    sample_sha = sha256_bytes(sample_bytes)
    sample_path.write_bytes(sample_bytes)
    speech, original_rate = decode_wav(sample_path)
    speech = resample_linear(speech, original_rate, SAMPLE_RATE)

    fixture, containers = create_fixture(speech)
    write_pcm16(fixture_path, fixture, SAMPLE_RATE)
    decoded_fixture, fixture_rate = decode_wav(fixture_path)
    if fixture_rate != SAMPLE_RATE:
        raise RuntimeError("Generated fixture sample rate drifted")

    runner = SileroVadRunner(model_path)
    probabilities = runner.run_audio(decoded_fixture)
    repeat_probabilities = runner.run_audio(decoded_fixture)
    config = SegmentationConfig()
    speech_segments = speech_segments_from_probabilities(probabilities, decoded_fixture.size, config)
    repeat_segments = speech_segments_from_probabilities(repeat_probabilities, decoded_fixture.size, config)

    timeline = build_activity_partition(decoded_fixture.size, speech_segments)
    repeat_timeline = build_activity_partition(decoded_fixture.size, repeat_segments)
    records = timeline_records(timeline, SAMPLE_RATE)
    summary = summarize(timeline, SAMPLE_RATE, decoded_fixture.size)
    timeline_sha = canonical_digest(timeline, SAMPLE_RATE, decoded_fixture.size)
    repeat_sha = canonical_digest(repeat_timeline, SAMPLE_RATE, decoded_fixture.size)
    partition_checks = validate_partition(timeline, decoded_fixture.size)

    container_counts = []
    for container in containers:
        count = sum(
            int(segment["endSample"]) > container["startSample"]
            and int(segment["startSample"]) < container["endSample"]
            for segment in speech_segments
        )
        container_counts.append(count)

    probabilities_finite = all(math.isfinite(float(value)) for value in probabilities)
    probabilities_bounded = all(0.0 <= float(value) <= 1.0 for value in probabilities)
    speech_inside_containers = bool(speech_segments) and all(
        segment_inside_any_container(segment, containers) for segment in speech_segments
    )
    both_speech_windows_detected = len(container_counts) == 2 and all(count > 0 for count in container_counts)
    deterministic = speech_segments == repeat_segments and timeline == repeat_timeline and timeline_sha == repeat_sha
    duration_exact = decoded_fixture.size == FIXTURE_SECONDS * SAMPLE_RATE
    labels_present = {span.label for span in timeline} == {SPEECH, NON_SPEECH}
    cue_labels_safe = all(
        record["editingCue"] in {"voice_focus", "music_or_ambient_focus"}
        for record in records
    )

    checks = {
        **partition_checks,
        "durationExact": duration_exact,
        "probabilitiesFinite": probabilities_finite,
        "probabilitiesBounded": probabilities_bounded,
        "speechInsidePinnedContainers": speech_inside_containers,
        "bothSpeechWindowsDetected": both_speech_windows_detected,
        "timelineDeterministic": deterministic,
        "speechAndNonSpeechPresent": labels_present,
        "editingCuesTruthful": cue_labels_safe,
        "officialSamplePinned": sample_sha == EXPECTED_SAMPLE_SHA256,
        "productionModelPinned": model_sha == EXPECTED_MODEL_SHA256,
    }

    expected_timeline_sha = args.expected_timeline_sha256.strip().lower()
    pinned_timeline = expected_timeline_sha != EXPECTED_TIMELINE_PLACEHOLDER.lower()
    blockers: list[str] = []
    if not all_checks_pass(checks):
        blockers.extend(name for name, passed in checks.items() if not passed)
    if pinned_timeline and timeline_sha != expected_timeline_sha:
        blockers.append("TIMELINE_SHA256_MISMATCH")

    passed = pinned_timeline and not blockers
    proof = {
        "schema": SCHEMA,
        "status": "PASS" if passed else "DISCOVERY",
        "runtime": {
            "provider": "CPUExecutionProvider",
            "sampleRate": SAMPLE_RATE,
        },
        "model": {
            "path": model_path.as_posix(),
            "sha256": model_sha,
            "sizeBytes": model_path.stat().st_size,
        },
        "sourceSample": {
            "url": SAMPLE_URL,
            "sha256": sample_sha,
            "originalSampleRate": original_rate,
        },
        "fixture": {
            "path": fixture_path.as_posix(),
            "sha256": sha256_file(fixture_path),
            "durationSeconds": FIXTURE_SECONDS,
            "totalSamples": int(decoded_fixture.size),
            "speechContainers": containers,
            "fixtureOnly": True,
            "userSongAnalyzed": False,
        },
        "segmentation": {
            "threshold": config.threshold,
            "negativeThreshold": config.negative_threshold,
            "minimumSpeechMs": config.min_speech_ms,
            "minimumSilenceMs": config.min_silence_ms,
            "speechPaddingMs": config.speech_pad_ms,
            "speechSegments": speech_segments,
            "speechSegmentsPerContainer": container_counts,
        },
        "timeline": records,
        "summary": {
            **summary,
            "canonicalSha256": timeline_sha,
        },
        "checks": checks,
        "blockers": blockers,
        "truthBoundary": {
            "productionLengthTimelineProven": passed,
            "voiceActivityTimelineProven": passed,
            "gapFreeEditingTimelineProven": passed,
            "userSongAnalyzed": False,
            "instrumentalClassificationProven": False,
            "vocalIsolationProven": False,
            "stemSeparationProven": False,
            "voiceConversionProven": False,
            "gpuInferenceProven": False,
            "tensorRtInferenceProven": False,
            "executionAuthorized": False,
            "requiresOperatorApproval": True,
        },
    }
    proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    write_edit_csv(csv_path, records)
    print(json.dumps(proof, separators=(",", ":")))

    if not pinned_timeline:
        print(
            "SONG_ACTIVITY_TIMELINE_DISCOVERY "
            f"sha256={timeline_sha} spans={summary['spanCount']} "
            f"speechSpans={summary['speechSpanCount']} speechSeconds={summary['speechSeconds']} "
            f"fixtureSha256={proof['fixture']['sha256']}",
            file=sys.stderr,
        )
        return 3
    if blockers:
        print("Song activity timeline blocked: " + ",".join(blockers), file=sys.stderr)
        return 2
    print(
        "EchoesSongActivityTimeline PASS "
        f"sha256={timeline_sha} spans={summary['spanCount']} "
        f"speechSpans={summary['speechSpanCount']} duration={FIXTURE_SECONDS}s "
        "user-song=false instrumental-classification=false isolation=false execution=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
