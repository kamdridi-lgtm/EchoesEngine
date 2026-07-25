#!/usr/bin/env python3
"""Read-only bridge from Echoes local evidence to K-Core mission plans."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from cinema_runtime_inventory import build_inventory
from kcore_mission_planner import PlanningError, load_object, plan_mission

REQUEST_SCHEMA = "kcore.plan-request.v1"


def validate_plan_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema") != REQUEST_SCHEMA:
        raise PlanningError("unsupported K-Core plan request schema")
    allowed = {
        "schema",
        "jobId",
        "missionType",
        "requireIdentity",
        "commercialUse",
        "cloudAllowed",
        "minimumQuality",
    }
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise PlanningError("unsupported K-Core plan request fields: " + ",".join(unknown))
    return {key: value[key] for key in allowed - {"schema"} if key in value}


def provider_vram_gib(provider: Mapping[str, Any] | None) -> float | None:
    provider = provider or {}
    gpu = provider.get("gpu") if isinstance(provider.get("gpu"), Mapping) else {}
    raw = gpu.get("vramGiB", gpu.get("totalMemoryGiB"))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def enrich_inventory_vram(
    inventory: Mapping[str, Any], provider: Mapping[str, Any] | None
) -> dict[str, Any]:
    result = deepcopy(dict(inventory))
    cuda = result.get("cuda") if isinstance(result.get("cuda"), dict) else {}
    cuda = dict(cuda)
    current = cuda.get("totalMemoryGiB")
    try:
        current_value = float(current or 0)
    except (TypeError, ValueError):
        current_value = 0.0
    if current_value <= 0:
        provider_memory = provider_vram_gib(provider)
        if provider_memory is not None:
            cuda["totalMemoryGiB"] = provider_memory
            cuda["memoryEvidenceSource"] = "provider-health"
    result["cuda"] = cuda
    return result


def plan_with_evidence(
    registry: Mapping[str, Any],
    request: Mapping[str, Any],
    inventory: Mapping[str, Any],
    provider: Mapping[str, Any] | None,
) -> dict[str, Any]:
    mission = validate_plan_request(request)
    truthful_inventory = enrich_inventory_vram(inventory, provider)
    result = plan_mission(registry, mission, truthful_inventory, provider)
    result["requestSchema"] = REQUEST_SCHEMA
    result["readOnly"] = True
    result["jobSubmitted"] = False
    return result


class LocalMissionPlanner:
    """Build plans from local lock/evidence files without executing any stage."""

    def __init__(self, repo_root: Path, workspace: Path, registry_path: Path | None = None) -> None:
        self.repo_root = repo_root.resolve()
        self.workspace = workspace.resolve()
        self.registry_path = (
            registry_path.resolve()
            if registry_path is not None
            else self.repo_root / "config" / "echoes-capability-registry.v1.json"
        )

    def plan(self, request: Mapping[str, Any], provider: Mapping[str, Any] | None) -> dict[str, Any]:
        registry = load_object(self.registry_path, "capability registry")
        inventory = build_inventory(self.repo_root, self.workspace)
        result = plan_with_evidence(registry, request, inventory, provider)
        result["planningEvidence"] = {
            "registry": str(self.registry_path),
            "workspace": str(self.workspace),
            "runtimeInventorySchema": inventory.get("schema"),
            "runtimeInventoryStatus": inventory.get("status"),
            "providerHealthSchema": (provider or {}).get("schema"),
            "providerStatus": (provider or {}).get("status", "MISSING"),
        }
        return result


def self_test() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    registry = load_object(repo_root / "config" / "echoes-capability-registry.v1.json", "registry")
    inventory = {
        "schema": "echoes.cinema-runtime-inventory.v1",
        "status": "PASS",
        "cuda": {"available": True},
    }
    provider = {
        "schema": "echoes.render-provider-health.v1",
        "status": "PASS",
        "realModelLoaded": True,
        "commercialUseAllowed": True,
        "gpu": {"vramGiB": 6.0},
        "capabilities": {
            "textToVideo": True,
            "referenceImage": True,
            "subjectIdentity": True,
        },
    }
    request = {
        "schema": REQUEST_SCHEMA,
        "jobId": "read-only-plan-001",
        "missionType": "music_video",
        "requireIdentity": True,
        "commercialUse": True,
        "cloudAllowed": False,
        "minimumQuality": 85,
    }
    planned = plan_with_evidence(registry, request, inventory, provider)
    assert planned["status"] == "PLANNED"
    assert planned["selectedPipeline"] == "cinema_identity_local_v1"
    assert planned["runtime"]["vramGiB"] == 6.0
    assert planned["readOnly"] is True and planned["jobSubmitted"] is False
    partial = plan_with_evidence(registry, request, inventory, {**provider, "status": "PARTIAL"})
    assert partial["status"] == "BLOCKED"
    assert "PROVIDER_STATUS_NOT_PASS" in partial["blockers"]
    audio = plan_with_evidence(
        registry,
        {
            "schema": REQUEST_SCHEMA,
            "jobId": "read-only-audio-001",
            "missionType": "audio_master",
            "requireIdentity": False,
            "commercialUse": False,
            "cloudAllowed": False,
            "minimumQuality": 80,
        },
        {**inventory, "cuda": {"available": False}},
        None,
    )
    assert audio["status"] == "PLANNED"
    try:
        validate_plan_request({**request, "shell": "whoami"})
    except PlanningError as error:
        assert "unsupported" in str(error)
    else:
        raise AssertionError("unknown plan request field was accepted")
    print("CinemaPlanning PASS endpoint=read-only identity=planned provider-partial=blocked audio=planned")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
