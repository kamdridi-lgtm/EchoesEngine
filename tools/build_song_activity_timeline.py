#!/usr/bin/env python3
"""Build a reusable Echoes speech/non-speech timeline from a local PCM WAV file."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

from prove_silero_vad import SAMPLE_RATE, SileroVadRunner, decode_wav, resample_linear
from silero_speech_segments import SegmentationConfig, speech_segments_from_probabilities
from song_activity_timeline import (
    all_checks_pass,
    build_activity_partition,
    canonical_digest,
    summarize,
    timeline_records,
    validate_partition,
)

SCHEMA = "echoes.song-activity-timeline.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "index",
                "label",
                "startSample",
                "endSample",
                "startSeconds",
                "endSeconds",
                "durationSeconds",
                "editingCue",
            ),
        )
        writer.writeheader()
        for record in records:
            writer.writerow({name: record[name] for name in writer.fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-label", default="local-wav")
    parser.add_argument("--declare-user-song", action="store_true")
    parser.add_argument("--expected-input-sha256")
    args = parser.parse_args()

    model_path = args.model.resolve()
    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "song-activity-timeline.json"
    csv_path = output_dir / "song-activity-timeline.csv"

    if not model_path.is_file() or model_path.suffix.lower() != ".onnx":
        raise RuntimeError("A local ONNX model file is required")
    if not input_path.is_file() or input_path.suffix.lower() != ".wav":
        raise RuntimeError("A local PCM WAV input file is required")

    input_sha = sha256_file(input_path)
    if args.expected_input_sha256 and input_sha != args.expected_input_sha256.lower():
        raise RuntimeError("Input WAV SHA-256 does not match the expected value")

    samples, original_rate = decode_wav(input_path)
    samples = resample_linear(samples, original_rate, SAMPLE_RATE)
    if samples.size <= 0:
        raise RuntimeError("Input audio is empty")

    runner = SileroVadRunner(model_path)
    probabilities = runner.run_audio(samples)
    config = SegmentationConfig()
    speech_segments = speech_segments_from_probabilities(probabilities, samples.size, config)
    timeline = build_activity_partition(samples.size, speech_segments)
    records = timeline_records(timeline, SAMPLE_RATE)
    summary = summarize(timeline, SAMPLE_RATE, samples.size)
    digest = canonical_digest(timeline, SAMPLE_RATE, samples.size)
    checks = {
        **validate_partition(timeline, samples.size),
        "probabilitiesFinite": all(math.isfinite(float(value)) for value in probabilities),
        "probabilitiesBounded": all(0.0 <= float(value) <= 1.0 for value in probabilities),
        "inputHashVerified": args.expected_input_sha256 is None or input_sha == args.expected_input_sha256.lower(),
        "timelineExported": bool(records),
    }
    blockers = [name for name, passed in checks.items() if not passed]
    passed = all_checks_pass(checks) and not blockers

    result = {
        "schema": SCHEMA,
        "status": "PASS" if passed else "BLOCKED",
        "source": {
            "label": args.source_label,
            "path": input_path.as_posix(),
            "sha256": input_sha,
            "sizeBytes": input_path.stat().st_size,
            "originalSampleRate": original_rate,
            "evaluatedSampleRate": SAMPLE_RATE,
            "evaluatedSamples": int(samples.size),
            "declaredUserSong": bool(args.declare_user_song),
        },
        "model": {
            "path": model_path.as_posix(),
            "sha256": sha256_file(model_path),
            "provider": "CPUExecutionProvider",
        },
        "segmentation": {
            "threshold": config.threshold,
            "negativeThreshold": config.negative_threshold,
            "minimumSpeechMs": config.min_speech_ms,
            "minimumSilenceMs": config.min_silence_ms,
            "speechPaddingMs": config.speech_pad_ms,
            "speechSegments": speech_segments,
        },
        "timeline": records,
        "summary": {**summary, "canonicalSha256": digest},
        "checks": checks,
        "blockers": blockers,
        "truthBoundary": {
            "localAudioFileAnalyzed": passed,
            "voiceActivityTimelineProduced": passed,
            "userSongAnalyzed": passed and bool(args.declare_user_song),
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
    json_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    write_csv(csv_path, records)
    print(
        "EchoesBuildSongActivityTimeline "
        f"status={result['status']} sha256={digest} spans={summary['spanCount']} "
        f"duration={summary['durationSeconds']}s userSong={str(result['truthBoundary']['userSongAnalyzed']).lower()}"
    )
    return 0 if passed else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Song activity timeline build failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
