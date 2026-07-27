#!/usr/bin/env python3
"""Validate global K-Core integration of the proven song activity timeline."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from kcore_mission_planner import plan_mission

SCHEMA = "echoes.kcore-song-activity-integration-proof.v1"
MODULE_ID = "songActivityTimeline"
EXPECTED_PIPELINES = {
    "lyric_video_local_v1": "build_voice_activity_timeline",
    "cinema_low_vram_text_v1": "build_voice_activity_timeline",
    "cinema_identity_local_v1": "build_voice_activity_timeline",
    "cinema_hybrid_identity_v1": "build_voice_activity_timeline_local",
}
FALSE_BOUNDARIES = (
    "instrumentalClassificationProven",
    "vocalIsolationProven",
    "stemSeparationProven",
    "voiceConversionProven",
    "gpuInferenceProven",
    "tensorRtInferenceProven",
)
PROOF_FIELDS = (
    "fixtureDurationSeconds",
    "fixtureSha256",
    "timelineSha256",
    "timelineSpanCount",
    "speechSpanCount",
    "nonSpeechSpanCount",
    "maximumObservedRecurrentReleaseTailMs",
    "acceptedRecurrentReleaseTailMs",
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def require(condition: bool, message: str, blockers: list[str]) -> None:
    if not condition:
        blockers.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    registry = load(args.registry)
    manifest = load(args.manifest)
    blockers: list[str] = []

    require(registry.get("schema") == "echoes.capability-registry.v1", "REGISTRY_SCHEMA_INVALID", blockers)
    require(registry.get("version") == "1.5.0", "REGISTRY_VERSION_INVALID", blockers)
    require(manifest.get("schema") == "echoes.song-activity-timeline-capability.v1", "MANIFEST_SCHEMA_INVALID", blockers)
    require(manifest.get("status") == "REAL", "MANIFEST_STATUS_NOT_REAL", blockers)

    modules = registry.get("engineModules") or {}
    module = modules.get(MODULE_ID) if isinstance(modules, dict) else None
    require(isinstance(module, dict), "GLOBAL_MODULE_MISSING", blockers)
    module = module if isinstance(module, dict) else {}

    require(module.get("status") == "REAL", "GLOBAL_MODULE_NOT_REAL", blockers)
    require(module.get("capabilityManifest") == "config/song-activity-timeline.v1.json", "MANIFEST_REFERENCE_DRIFT", blockers)
    require(module.get("genericLocalWavCliProven") is True, "LOCAL_WAV_CLI_NOT_PROVEN", blockers)
    require(module.get("gapFreeActivityTimelineProven") is True, "GAP_FREE_TIMELINE_NOT_PROVEN", blockers)
    require(module.get("crossPlatformProof") is True, "CROSS_PLATFORM_PROOF_MISSING", blockers)
    require(module.get("requiresOperatorApproval") is True, "OPERATOR_APPROVAL_NOT_REQUIRED", blockers)
    require(module.get("executionAuthorized") is False, "EXECUTION_MUST_REMAIN_UNAUTHORIZED", blockers)

    proof = manifest.get("proof") if isinstance(manifest.get("proof"), dict) else {}
    for field in PROOF_FIELDS:
        require(module.get(field) == proof.get(field), f"PROOF_FIELD_DRIFT:{field}", blockers)

    boundary = manifest.get("truthBoundary") if isinstance(manifest.get("truthBoundary"), dict) else {}
    require(module.get("userSongAnalyzed") is boundary.get("userSongAnalyzed") is False, "USER_SONG_BOUNDARY_DRIFT", blockers)
    for field in FALSE_BOUNDARIES:
        require(module.get(field) is False and boundary.get(field) is False, f"FALSE_BOUNDARY_DRIFT:{field}", blockers)

    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    require(outputs.get("labels") == ["speech", "non_speech"], "LABELS_NOT_CONSERVATIVE", blockers)
    require(outputs.get("editingCues") == ["voice_focus", "music_or_ambient_focus"], "EDITING_CUES_NOT_CONSERVATIVE", blockers)

    pipelines = {
        str(item.get("id")): item
        for item in registry.get("pipelines", [])
        if isinstance(item, dict)
    }
    for pipeline_id, required_stage in EXPECTED_PIPELINES.items():
        pipeline = pipelines.get(pipeline_id)
        require(isinstance(pipeline, dict), f"PIPELINE_MISSING:{pipeline_id}", blockers)
        if not isinstance(pipeline, dict):
            continue
        requirements = pipeline.get("requirements") if isinstance(pipeline.get("requirements"), dict) else {}
        engine_modules = requirements.get("engineModules") if isinstance(requirements.get("engineModules"), list) else []
        stages = pipeline.get("stages") if isinstance(pipeline.get("stages"), list) else []
        require(MODULE_ID in engine_modules, f"PIPELINE_MODULE_NOT_REQUIRED:{pipeline_id}", blockers)
        require(required_stage in stages, f"PIPELINE_STAGE_MISSING:{pipeline_id}", blockers)
        dangerous = [
            stage
            for stage in stages
            if any(token in str(stage).lower() for token in ("isolate_vocal", "instrumental_stem", "separate_stems"))
        ]
        require(not dangerous, f"UNPROVEN_STAGE_PRESENT:{pipeline_id}", blockers)

    inventory = {
        "schema": "echoes.cinema-runtime-inventory.v1",
        "status": "PASS",
        "cuda": {"available": True, "totalMemoryGiB": 6.0},
    }
    provider = {
        "schema": "echoes.render-provider-health.v1",
        "status": "PASS",
        "realModelLoaded": True,
        "commercialUseAllowed": True,
        "capabilities": {
            "textToVideo": True,
            "referenceImage": True,
            "subjectIdentity": True,
        },
    }

    lyric = plan_mission(
        registry,
        {
            "jobId": "timeline-lyric-proof",
            "missionType": "lyric_video",
            "requireIdentity": False,
            "commercialUse": False,
            "cloudAllowed": False,
        },
        inventory,
        None,
    )
    require(lyric.get("status") == "PLANNED", "LYRIC_PLAN_NOT_PLANNED", blockers)
    require(lyric.get("selectedPipeline") == "lyric_video_local_v1", "LYRIC_PIPELINE_DRIFT", blockers)
    require("build_voice_activity_timeline" in lyric.get("stages", []), "LYRIC_TIMELINE_STAGE_ABSENT", blockers)
    require(lyric.get("mutationsAllowed") is False, "PLANNER_MUTATIONS_ENABLED", blockers)
    require(lyric.get("requiresOperatorApproval") is True, "PLANNER_APPROVAL_DISABLED", blockers)

    cinema = plan_mission(
        registry,
        {
            "jobId": "timeline-cinema-proof",
            "missionType": "music_video",
            "requireIdentity": True,
            "commercialUse": True,
            "cloudAllowed": False,
        },
        inventory,
        provider,
    )
    require(cinema.get("status") == "PLANNED", "CINEMA_PLAN_NOT_PLANNED", blockers)
    require(cinema.get("selectedPipeline") == "cinema_identity_local_v1", "CINEMA_PIPELINE_DRIFT", blockers)
    require("build_voice_activity_timeline" in cinema.get("stages", []), "CINEMA_TIMELINE_STAGE_ABSENT", blockers)

    blocked_registry = copy.deepcopy(registry)
    blocked_registry["engineModules"][MODULE_ID]["status"] = "MISSING"
    blocked = plan_mission(
        blocked_registry,
        {
            "jobId": "timeline-fail-closed",
            "missionType": "lyric_video",
            "requireIdentity": False,
            "commercialUse": False,
            "cloudAllowed": False,
        },
        inventory,
        None,
    )
    require(blocked.get("status") == "BLOCKED", "MISSING_MODULE_DID_NOT_BLOCK", blockers)
    require(
        f"UNPROVEN_ENGINE_MODULES:{MODULE_ID}" in blocked.get("blockers", []),
        "CANONICAL_MODULE_BLOCKER_MISSING",
        blockers,
    )

    result = {
        "schema": SCHEMA,
        "status": "PASS" if not blockers else "BLOCKED",
        "registryVersion": registry.get("version"),
        "moduleId": MODULE_ID,
        "timelineSha256": module.get("timelineSha256"),
        "fixtureSha256": module.get("fixtureSha256"),
        "pipelines": sorted(EXPECTED_PIPELINES),
        "lyricPipeline": lyric.get("selectedPipeline"),
        "cinemaPipeline": cinema.get("selectedPipeline"),
        "missingModulePlanStatus": blocked.get("status"),
        "missingModuleBlockers": blocked.get("blockers"),
        "mutationsAllowed": False,
        "requiresOperatorApproval": True,
        "executionAuthorized": False,
        "instrumentalClassificationProven": False,
        "vocalIsolationProven": False,
        "stemSeparationProven": False,
        "userSongAnalyzed": False,
        "blockers": blockers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, separators=(",", ":")))
    if blockers:
        print("K-Core song activity integration blocked: " + ",".join(blockers), file=sys.stderr)
        return 2
    print(
        "KCoreSongActivityIntegration PASS "
        "module=songActivityTimeline pipelines=4 "
        "missing-module=blocked instrumental=false isolation=false execution=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
