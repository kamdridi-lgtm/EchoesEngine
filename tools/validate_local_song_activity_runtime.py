#!/usr/bin/env python3
"""Validate the installed Windows runtime and one completed analysis job."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
FALSE_BOUNDARIES = (
    "instrumentalClassificationProven",
    "vocalIsolationProven",
    "stemSeparationProven",
    "voiceConversionProven",
    "gpuInferenceProven",
    "tensorRtInferenceProven",
    "executionAuthorized",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str, blockers: list[str]) -> None:
    if not condition:
        blockers.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--analysis-manifest", type=Path, required=True)
    parser.add_argument("--timeline-json", type=Path, required=True)
    parser.add_argument("--timeline-csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runtime_path = args.runtime_manifest.resolve()
    analysis_path = args.analysis_manifest.resolve()
    timeline_path = args.timeline_json.resolve()
    csv_path = args.timeline_csv.resolve()
    runtime = load(runtime_path)
    analysis = load(analysis_path)
    timeline = load(timeline_path)
    blockers: list[str] = []

    require(runtime.get("schema") == "echoes.local-song-activity-runtime-installation.v1", "RUNTIME_SCHEMA_INVALID", blockers)
    require(runtime.get("status") == "PASS", "RUNTIME_STATUS_NOT_PASS", blockers)
    require(runtime.get("model", {}).get("sha256") == MODEL_SHA256, "RUNTIME_MODEL_SHA_DRIFT", blockers)
    require(runtime.get("model", {}).get("integrityVerified") is True, "RUNTIME_MODEL_NOT_VERIFIED", blockers)
    require(runtime.get("truthBoundary", {}).get("runtimeInstalledOnCurrentHost") is True, "RUNTIME_INSTALL_NOT_PROVEN", blockers)
    require(runtime.get("truthBoundary", {}).get("hpOmenExecutionProven") is False, "HP_OMEN_FALSE_BOUNDARY_DRIFT", blockers)
    require(runtime.get("truthBoundary", {}).get("userSongAnalyzed") is False, "RUNTIME_USER_SONG_BOUNDARY_DRIFT", blockers)

    require(analysis.get("schema") == "echoes.local-song-activity-analysis-run.v1", "ANALYSIS_SCHEMA_INVALID", blockers)
    require(analysis.get("status") == "PASS", "ANALYSIS_STATUS_NOT_PASS", blockers)
    require(analysis.get("model", {}).get("sha256") == MODEL_SHA256, "ANALYSIS_MODEL_SHA_DRIFT", blockers)
    require(analysis.get("timeline", {}).get("jsonSha256") == sha256(timeline_path), "TIMELINE_JSON_HASH_MISMATCH", blockers)
    require(analysis.get("timeline", {}).get("csvSha256") == sha256(csv_path), "TIMELINE_CSV_HASH_MISMATCH", blockers)
    require(analysis.get("source", {}).get("declaredUserSong") is False, "CI_FIXTURE_MUST_NOT_BE_USER_SONG", blockers)
    require(analysis.get("normalizedAudio", {}).get("converted") is False, "WAV_SMOKE_SHOULD_NOT_CONVERT", blockers)

    require(timeline.get("schema") == "echoes.song-activity-timeline.v1", "TIMELINE_SCHEMA_INVALID", blockers)
    require(timeline.get("status") == "PASS", "TIMELINE_STATUS_NOT_PASS", blockers)
    require(timeline.get("truthBoundary", {}).get("localAudioFileAnalyzed") is True, "LOCAL_AUDIO_NOT_ANALYZED", blockers)
    require(timeline.get("truthBoundary", {}).get("voiceActivityTimelineProduced") is True, "TIMELINE_NOT_PRODUCED", blockers)
    require(timeline.get("truthBoundary", {}).get("userSongAnalyzed") is False, "TIMELINE_USER_SONG_BOUNDARY_DRIFT", blockers)
    require(float(timeline.get("summary", {}).get("durationSeconds", 0.0)) > 0.0, "TIMELINE_DURATION_EMPTY", blockers)
    require(int(timeline.get("summary", {}).get("spanCount", 0)) >= 1, "TIMELINE_SPANS_EMPTY", blockers)
    for field in FALSE_BOUNDARIES:
        require(analysis.get("truthBoundary", {}).get(field) is False, f"ANALYSIS_FALSE_BOUNDARY_DRIFT:{field}", blockers)
        require(timeline.get("truthBoundary", {}).get(field) is False, f"TIMELINE_FALSE_BOUNDARY_DRIFT:{field}", blockers)

    proof = {
        "schema": "echoes.local-song-activity-runtime-proof.v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "runtimeManifestSha256": sha256(runtime_path),
        "analysisManifestSha256": sha256(analysis_path),
        "timelineJsonSha256": sha256(timeline_path),
        "timelineCsvSha256": sha256(csv_path),
        "modelSha256": MODEL_SHA256,
        "runtimeInstalled": runtime.get("truthBoundary", {}).get("runtimeInstalledOnCurrentHost"),
        "localAudioAnalyzed": timeline.get("truthBoundary", {}).get("localAudioFileAnalyzed"),
        "userSongAnalyzed": False,
        "hpOmenExecutionProven": False,
        "executionAuthorized": False,
        "blockers": blockers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(proof, separators=(",", ":")))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
