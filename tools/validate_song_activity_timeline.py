#!/usr/bin/env python3
"""Validate Echoes song activity timeline evidence without shell-specific semantics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_CHECKS = (
    "labelsValid",
    "positiveDuration",
    "bounded",
    "ordered",
    "nonOverlapping",
    "gapFree",
    "alternatingLabels",
    "exactCoverage",
    "durationExact",
    "probabilitiesFinite",
    "probabilitiesBounded",
    "speechInsidePinnedContainersOrReleaseTail",
    "speechSegmentsStartInsidePinnedContainers",
    "recurrentReleaseTailWithinTolerance",
    "bothSpeechWindowsDetected",
    "timelineDeterministic",
    "speechAndNonSpeechPresent",
    "editingCuesTruthful",
    "officialSamplePinned",
    "productionModelPinned",
)

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
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--mode", choices=("discovery", "pass"), required=True)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()

    proof = json.loads(args.proof.read_text(encoding="utf-8"))
    expected_status = "DISCOVERY" if args.mode == "discovery" else "PASS"
    require(proof.get("schema") == "echoes.song-activity-timeline-proof.v1", "Timeline schema drifted")
    require(proof.get("status") == expected_status, f"Expected status {expected_status}")
    require(not proof.get("blockers"), f"Timeline blockers present: {proof.get('blockers')}")

    checks = proof.get("checks", {})
    for name in REQUIRED_CHECKS:
        require(checks.get(name) is True, f"Timeline check failed: {name}")

    segmentation = proof.get("segmentation", {})
    require(segmentation.get("recurrentReleaseToleranceMs") == 750, "Release-tail tolerance drifted")
    require(float(segmentation.get("maximumObservedReleaseTailMs", 1e9)) <= 750.0, "Release tail exceeded 750 ms")
    require(float(segmentation.get("maximumObservedReleaseTailMs", -1)) >= 0.0, "Release tail is invalid")

    fixture = proof.get("fixture", {})
    require(fixture.get("durationSeconds") == 180, "Fixture duration must remain 180 seconds")
    require(fixture.get("fixtureOnly") is True, "Fixture boundary missing")
    require(fixture.get("userSongAnalyzed") is False, "CI must not claim a user song was analyzed")

    summary = proof.get("summary", {})
    digest = str(summary.get("canonicalSha256", ""))
    require(len(digest) == 64, "Canonical timeline digest is missing")
    require(int(summary.get("spanCount", 0)) >= 3, "Timeline did not produce enough spans")
    require(int(summary.get("speechSpanCount", 0)) > 0, "Timeline did not produce speech spans")
    require(int(summary.get("nonSpeechSpanCount", 0)) > 0, "Timeline did not produce non-speech spans")
    require(abs(float(summary.get("durationSeconds", 0.0)) - 180.0) < 1e-9, "Summary duration drifted")
    require(abs(float(summary.get("speechCoverage", 0.0)) + float(summary.get("nonSpeechCoverage", 0.0)) - 1.0) < 1e-9, "Coverage does not sum to one")

    truth = proof.get("truthBoundary", {})
    promoted = args.mode == "pass"
    for field in (
        "productionLengthTimelineProven",
        "voiceActivityTimelineProven",
        "gapFreeEditingTimelineProven",
    ):
        require(truth.get(field) is promoted, f"Truth boundary drifted: {field}")
    require(truth.get("userSongAnalyzed") is False, "User-song truth boundary drifted")
    for field in UNPROVEN_FIELDS:
        require(truth.get(field) is False, f"{field} must remain false")
    require(truth.get("requiresOperatorApproval") is True, "Operator approval must remain required")

    if args.expected_sha256:
        require(digest == args.expected_sha256.lower(), "Canonical timeline SHA-256 drifted")

    print(
        "EchoesSongActivityTimelineValidation PASS "
        f"mode={args.mode} sha256={digest} spans={summary['spanCount']} "
        f"maxTailMs={segmentation['maximumObservedReleaseTailMs']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Song activity timeline validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
