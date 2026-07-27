#!/usr/bin/env python3
"""Validate truthful K-Core integration of the proven HTDemucs stem runtime."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from kcore_mission_planner import plan_mission

SCHEMA = "echoes.kcore-stem-integration-proof.v1"
MODULE_ID = "htdemucsStemSeparation"
PIPELINE_ID = "stem_separation_windows_v1"
EXPECTED_STAGES = [
    "verify_source_analysis",
    "verify_source_hash",
    "provision_pinned_htdemucs",
    "separate_four_stems",
    "validate_stem_geometry",
    "record_stem_hashes",
    "verify_source_preserved",
    "emit_stem_manifest",
]
FALSE_BOUNDARIES = (
    "hpOmenExecutionProven",
    "userSongSeparated",
    "gpuInferenceProven",
    "tensorRtInferenceProven",
    "vocalIsolationQualityProven",
    "voiceConversionProven",
    "autonomousScheduledExecutionProven",
    "executionAuthorized",
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
    require(registry.get("integrationRevision") == "2026-07-27-kcore-stems", "INTEGRATION_REVISION_INVALID", blockers)
    require(manifest.get("schema") == "echoes.stem-runtime.v1", "MANIFEST_SCHEMA_INVALID", blockers)
    require(manifest.get("status") == "REAL", "MANIFEST_STATUS_NOT_REAL", blockers)

    modules = registry.get("engineModules") or {}
    module = modules.get(MODULE_ID) if isinstance(modules, dict) else None
    require(isinstance(module, dict), "GLOBAL_MODULE_MISSING", blockers)
    module = module if isinstance(module, dict) else {}

    require(module.get("status") == "REAL", "GLOBAL_MODULE_NOT_REAL", blockers)
    require(module.get("capabilityManifest") == "config/echoes-stem-runtime.v1.json", "MANIFEST_REFERENCE_DRIFT", blockers)
    require(module.get("model") == "htdemucs", "MODEL_ID_DRIFT", blockers)
    require(module.get("modelSha256") == manifest.get("model", {}).get("sha256"), "MODEL_SHA_DRIFT", blockers)
    require(module.get("modelSizeBytes") == manifest.get("model", {}).get("sizeBytes"), "MODEL_SIZE_DRIFT", blockers)
    require(module.get("sources") == ["vocals", "drums", "bass", "other"], "SOURCE_ORDER_DRIFT", blockers)
    require(module.get("sampleRate") == 44100, "SAMPLE_RATE_DRIFT", blockers)
    require(module.get("channels") == 2, "CHANNEL_COUNT_DRIFT", blockers)
    require(module.get("outputBitDepth") == 24, "BIT_DEPTH_DRIFT", blockers)
    require(module.get("cpuInferenceProven") is True, "CPU_INFERENCE_NOT_PROVEN", blockers)
    require(module.get("sourcePreservationProven") is True, "SOURCE_PRESERVATION_NOT_PROVEN", blockers)
    require(module.get("sourceUploadDisabled") is True, "SOURCE_UPLOAD_NOT_DISABLED", blockers)
    require(module.get("idempotentControllerProven") is True, "IDEMPOTENCE_NOT_PROVEN", blockers)
    require(module.get("stemGeometryValidationProven") is True, "STEM_GEOMETRY_NOT_PROVEN", blockers)
    require(module.get("stemHashesRecorded") is True, "STEM_HASHES_NOT_RECORDED", blockers)
    require(module.get("controlledFixtureSeparated") is True, "CONTROLLED_FIXTURE_NOT_SEPARATED", blockers)
    require(module.get("requiresOperatorApproval") is True, "OPERATOR_APPROVAL_NOT_REQUIRED", blockers)
    require(module.get("proofCommit") == "71be8384ca8410fea35dcfc3ad3c20f9e81fc386", "PROOF_COMMIT_DRIFT", blockers)
    require(module.get("oneClickPackageSha256") == "bf417072cd839dc64e03c2bb40d7d2ba1f6f4ed325f8d8f8f477917bf49fdfd1", "PACKAGE_SHA_DRIFT", blockers)

    for field in FALSE_BOUNDARIES:
        require(module.get(field) is False, f"FALSE_BOUNDARY_DRIFT:{field}", blockers)

    manifest_boundary = manifest.get("truthBoundary") if isinstance(manifest.get("truthBoundary"), dict) else {}
    for field in ("hpOmenExecutionProven", "userSongSeparated", "gpuInferenceProven", "voiceConversionProven", "autonomousExecutionProven"):
        require(manifest_boundary.get(field) is False, f"MANIFEST_BOUNDARY_DRIFT:{field}", blockers)

    pipelines = {
        str(item.get("id")): item
        for item in registry.get("pipelines", [])
        if isinstance(item, dict)
    }
    pipeline = pipelines.get(PIPELINE_ID)
    require(isinstance(pipeline, dict), "STEM_PIPELINE_MISSING", blockers)
    pipeline = pipeline if isinstance(pipeline, dict) else {}
    require(pipeline.get("missions") == ["stem_separation"], "STEM_MISSION_DRIFT", blockers)
    require(pipeline.get("execution") == "local", "STEM_EXECUTION_NOT_LOCAL", blockers)
    requirements = pipeline.get("requirements") if isinstance(pipeline.get("requirements"), dict) else {}
    require(
        requirements.get("engineModules") == ["audioCore", "songActivityTimeline", MODULE_ID],
        "STEM_MODULE_REQUIREMENTS_DRIFT",
        blockers,
    )
    require(pipeline.get("stages") == EXPECTED_STAGES, "STEM_STAGE_ORDER_DRIFT", blockers)

    inventory = {
        "schema": "echoes.cinema-runtime-inventory.v1",
        "status": "PASS",
        "cuda": {"available": False, "totalMemoryGiB": 0},
    }
    mission = {
        "jobId": "stem-kcore-proof",
        "missionType": "stem_separation",
        "requireIdentity": False,
        "commercialUse": False,
        "cloudAllowed": False,
        "minimumQuality": 80,
    }
    planned = plan_mission(registry, mission, inventory, None)
    require(planned.get("status") == "PLANNED", "STEM_PLAN_NOT_PLANNED", blockers)
    require(planned.get("selectedPipeline") == PIPELINE_ID, "STEM_PIPELINE_NOT_SELECTED", blockers)
    require(planned.get("stages") == EXPECTED_STAGES, "PLANNED_STAGE_ORDER_DRIFT", blockers)
    require(planned.get("mutationsAllowed") is False, "PLANNER_MUTATIONS_ENABLED", blockers)
    require(planned.get("requiresOperatorApproval") is True, "PLANNER_APPROVAL_DISABLED", blockers)

    blocked_registry = copy.deepcopy(registry)
    blocked_registry["engineModules"][MODULE_ID]["status"] = "MISSING"
    blocked = plan_mission(blocked_registry, mission, inventory, None)
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
        "integrationRevision": registry.get("integrationRevision"),
        "moduleId": MODULE_ID,
        "pipelineId": PIPELINE_ID,
        "modelSha256": module.get("modelSha256"),
        "packageSha256": module.get("oneClickPackageSha256"),
        "proofCommit": module.get("proofCommit"),
        "plannedStatus": planned.get("status"),
        "selectedPipeline": planned.get("selectedPipeline"),
        "missingModulePlanStatus": blocked.get("status"),
        "missingModuleBlockers": blocked.get("blockers"),
        "cpuInferenceProven": module.get("cpuInferenceProven"),
        "hpOmenExecutionProven": False,
        "userSongSeparated": False,
        "gpuInferenceProven": False,
        "voiceConversionProven": False,
        "autonomousScheduledExecutionProven": False,
        "mutationsAllowed": False,
        "requiresOperatorApproval": True,
        "executionAuthorized": False,
        "blockers": blockers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, separators=(",", ":")))
    if blockers:
        print("K-Core stem integration blocked: " + ",".join(blockers), file=sys.stderr)
        return 2
    print(
        "KCoreStemIntegration PASS "
        "module=htdemucsStemSeparation pipeline=stem_separation_windows_v1 "
        "cpu=true hp-omen=false user-song=false gpu=false execution=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
