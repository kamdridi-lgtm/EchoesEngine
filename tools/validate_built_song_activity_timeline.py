#!/usr/bin/env python3
"""Validate output from the reusable local WAV activity timeline CLI."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

UNPROVEN_FIELDS = (
    "instrumentalClassificationProven",
    "vocalIsolationProven",
    "stemSeparationProven",
    "voiceConversionProven",
    "gpuInferenceProven",
    "tensorRtInferenceProven",
    "executionAuthorized",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--expected-timeline-sha256", required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--expected-duration-seconds", type=float, required=True)
    parser.add_argument("--expect-user-song", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.timeline.read_text(encoding="utf-8"))
    require(data.get("schema") == "echoes.song-activity-timeline.v1", "Generic timeline schema drifted")
    require(data.get("status") == "PASS", "Generic timeline did not pass")
    require(not data.get("blockers"), f"Generic timeline blockers present: {data.get('blockers')}")
    require(all(data.get("checks", {}).values()), "Generic timeline contains a failed check")

    source = data.get("source", {})
    summary = data.get("summary", {})
    truth = data.get("truthBoundary", {})
    require(source.get("sha256") == args.expected_input_sha256.lower(), "Input WAV SHA-256 drifted")
    require(summary.get("canonicalSha256") == args.expected_timeline_sha256.lower(), "Timeline SHA-256 drifted")
    require(abs(float(summary.get("durationSeconds", 0.0)) - args.expected_duration_seconds) < 1e-9, "Timeline duration drifted")
    require(int(summary.get("spanCount", 0)) == len(data.get("timeline", [])), "Timeline span accounting drifted")
    require(int(summary.get("speechSpanCount", 0)) > 0, "No speech spans were emitted")
    require(int(summary.get("nonSpeechSpanCount", 0)) > 0, "No non-speech spans were emitted")
    require(truth.get("localAudioFileAnalyzed") is True, "Local audio analysis was not proven")
    require(truth.get("voiceActivityTimelineProduced") is True, "Voice-activity timeline was not produced")
    require(truth.get("userSongAnalyzed") is bool(args.expect_user_song), "User-song truth boundary drifted")
    for field in UNPROVEN_FIELDS:
        require(truth.get(field) is False, f"{field} must remain false")
    require(truth.get("requiresOperatorApproval") is True, "Operator approval must remain required")

    print(
        "EchoesBuiltSongActivityTimelineValidation PASS "
        f"sha256={summary['canonicalSha256']} spans={summary['spanCount']} "
        f"userSong={str(truth['userSongAnalyzed']).lower()}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Built song activity timeline validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
