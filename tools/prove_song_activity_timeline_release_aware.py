#!/usr/bin/env python3
"""Run the song timeline proof with an explicit Silero recurrent release-tail boundary."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import prove_song_activity_timeline as base

RELEASE_TOLERANCE_MS = 750
RELEASE_TOLERANCE_SAMPLES = int(math.ceil(base.SAMPLE_RATE * RELEASE_TOLERANCE_MS / 1000.0))
ENTRY_TOLERANCE_MS = 30
ENTRY_TOLERANCE_SAMPLES = int(math.ceil(base.SAMPLE_RATE * ENTRY_TOLERANCE_MS / 1000.0))


def release_aware_container_check(
    segment: dict[str, int | float], containers: list[dict[str, int]]
) -> bool:
    start = int(segment["startSample"])
    end = int(segment["endSample"])
    return any(
        start >= max(0, item["startSample"] - ENTRY_TOLERANCE_SAMPLES)
        and start < item["endSample"]
        and end <= item["endSample"] + RELEASE_TOLERANCE_SAMPLES
        for item in containers
    )


def _matching_container(segment: dict[str, Any], containers: list[dict[str, int]]) -> dict[str, int] | None:
    start = int(segment["startSample"])
    for container in containers:
        if start >= max(0, container["startSample"] - ENTRY_TOLERANCE_SAMPLES) and start < container["endSample"]:
            return container
    return None


def enrich_proof(proof_path: Path) -> None:
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    containers = proof["fixture"]["speechContainers"]
    segments = proof["segmentation"]["speechSegments"]

    tails: list[int] = []
    starts_inside = True
    for segment in segments:
        container = _matching_container(segment, containers)
        if container is None:
            starts_inside = False
            continue
        tails.append(max(0, int(segment["endSample"]) - int(container["endSample"])))

    maximum_tail = max(tails, default=0)
    total_tail = sum(tails)
    release_ok = maximum_tail <= RELEASE_TOLERANCE_SAMPLES
    old_check = bool(proof["checks"].pop("speechInsidePinnedContainers", False))
    proof["checks"]["speechInsidePinnedContainersOrReleaseTail"] = old_check
    proof["checks"]["speechSegmentsStartInsidePinnedContainers"] = starts_inside
    proof["checks"]["recurrentReleaseTailWithinTolerance"] = release_ok
    proof["segmentation"]["entryToleranceMs"] = ENTRY_TOLERANCE_MS
    proof["segmentation"]["recurrentReleaseToleranceMs"] = RELEASE_TOLERANCE_MS
    proof["segmentation"]["maximumObservedReleaseTailSamples"] = maximum_tail
    proof["segmentation"]["maximumObservedReleaseTailMs"] = round(maximum_tail * 1000.0 / base.SAMPLE_RATE, 3)
    proof["segmentation"]["totalObservedReleaseTailSamples"] = total_tail
    proof["segmentation"]["totalObservedReleaseTailMs"] = round(total_tail * 1000.0 / base.SAMPLE_RATE, 3)

    if not starts_inside and "SPEECH_SEGMENT_STARTED_OUTSIDE_CONTAINER" not in proof["blockers"]:
        proof["blockers"].append("SPEECH_SEGMENT_STARTED_OUTSIDE_CONTAINER")
    if not release_ok and "RECURRENT_RELEASE_TAIL_EXCEEDED" not in proof["blockers"]:
        proof["blockers"].append("RECURRENT_RELEASE_TAIL_EXCEEDED")

    if proof["status"] == "PASS" and proof["blockers"]:
        proof["status"] = "BLOCKED"
        for field in (
            "productionLengthTimelineProven",
            "voiceActivityTimelineProven",
            "gapFreeEditingTimelineProven",
        ):
            proof["truthBoundary"][field] = False

    proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    base.segment_inside_any_container = release_aware_container_check
    result = base.main()

    output_dir: Path | None = None
    for index, token in enumerate(sys.argv):
        if token == "--output-dir" and index + 1 < len(sys.argv):
            output_dir = Path(sys.argv[index + 1]).resolve()
            break
    if output_dir is None:
        raise RuntimeError("--output-dir is required")

    proof_path = output_dir / "song-activity-timeline.json"
    enrich_proof(proof_path)
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    print(
        "EchoesSongActivityReleaseBoundary "
        f"maxTailMs={proof['segmentation']['maximumObservedReleaseTailMs']} "
        f"toleranceMs={RELEASE_TOLERANCE_MS} startsInside={str(proof['checks']['speechSegmentsStartInsidePinnedContainers']).lower()}"
    )
    if proof["status"] == "BLOCKED":
        return 2
    return result


if __name__ == "__main__":
    raise SystemExit(main())
