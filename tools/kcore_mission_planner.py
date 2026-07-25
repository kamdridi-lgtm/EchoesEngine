#!/usr/bin/env python3
"""Deterministic K-Core mission planning over truthful Echoes capabilities."""
from __future__ import annotations
import argparse, json, sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "kcore.execution-plan.v1"
REGISTRY_SCHEMA = "echoes.capability-registry.v1"
ALLOWED_RUNTIME = {"PASS", "PARTIAL", "BLOCKED"}
ALLOWED_EXECUTIONS = {"local", "hybrid", "remote"}
PROVEN_MODULE_STATUSES = {"REAL"}
JOB_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")

class PlanningError(RuntimeError):
    pass

def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PlanningError(f"{label} is unreadable: {path}: {error}") from error
    if not isinstance(value, dict):
        raise PlanningError(f"{label} must be a JSON object: {path}")
    return value

def validate_job_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 80 or any(c not in JOB_ID_CHARS for c in value):
        raise PlanningError("jobId must contain only letters, numbers, hyphen, or underscore")
    return value

def validate_registry(registry: Mapping[str, Any]) -> None:
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise PlanningError("unsupported capability registry schema")
    if not isinstance(registry.get("missionTypes"), list) or not registry["missionTypes"]:
        raise PlanningError("registry missionTypes must be a non-empty list")
    if not isinstance(registry.get("engineModules"), Mapping):
        raise PlanningError("registry engineModules must be an object")
    pipelines = registry.get("pipelines")
    if not isinstance(pipelines, list) or not pipelines:
        raise PlanningError("registry pipelines must be a non-empty list")
    seen: set[str] = set()
    for pipeline in pipelines:
        if not isinstance(pipeline, Mapping):
            raise PlanningError("pipeline must be an object")
        pipeline_id = str(pipeline.get("id") or "")
        if not pipeline_id or pipeline_id in seen:
            raise PlanningError(f"pipeline id is missing or duplicated: {pipeline_id!r}")
        seen.add(pipeline_id)
        if pipeline.get("execution") not in ALLOWED_EXECUTIONS:
            raise PlanningError(f"unsupported execution mode: {pipeline_id}")
        if not isinstance(pipeline.get("requirements"), Mapping):
            raise PlanningError(f"pipeline requirements must be an object: {pipeline_id}")
        if not isinstance(pipeline.get("stages"), list) or not pipeline["stages"]:
            raise PlanningError(f"pipeline stages must be a non-empty list: {pipeline_id}")

@dataclass(frozen=True)
class Mission:
    job_id: str
    mission_type: str
    require_identity: bool
    commercial_use: bool
    cloud_alowed: bool
    minimum_quality: int
    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Mission":
        quality = int(value.get("minimumQuality", 70) or 70)
        if quality < 1 or quality > 100:
            raise PlanningError("minimumQuality must be between 1 and 100")
        return cls(
            validate_job_id(value.get("jobId")),
            str(value.get("missionType") or "").strip(),
            value.get("requireIdentity") is True,
            value.get("commercialUse") is True,
            value.get("cloudAllowed") is True,
            quality,
        )

@dataclass(frozen=True)
class RuntimeContext:
    status: str
    vram_gib: float
    provider_status: str
    real_model_loaded: bool
    commercial_use_allowed: bool
    capabilities: frozenset[str]
    @classmethod
    def from_evidence(cls, inventory: Mapping[str, Any], provider: Mapping[str, Any] | None) -> "RuntimeContext":
        status = str(inventory.get("status") or "PARTIAL")
        if status not in ALLOWED_RUNTIME:
            raise PlanningError(f"unsupported runtime inventory status: {status}")
        cuda = inventory.get("cuda") if isinstance(inventory.get("cuda"), Mapping) else {}
        raw_vram = cuda.get("totalMemoryGiB", inventory.get("gpuMemoryGiB", 0))
        try:
            vram = max(0.0, float(raw_vram or 0.0))
        except (TypeError, ValueError):
            vram = 0.0
        provider = provider or {}
        capabilities = provider.get("capabilities") if isinstance(provider.get("capabilities"), Mapping) else {}
        return cls(
            status,
            vram,
            str(provider.get("status") or "MISSING"),
            provider.get("realModelLoaded") is True,
            provider.get("commercialUseAllowed") is True,
            frozenset(str(name) for name, enabled in capabilities.items() if enabled is True),
        )

def evaluate(pipeline: Mapping[str, Any], mission: Mission, runtime: RuntimeContext, modules: Mapping[str, str]) -> tuple[bool, list[str], int]:
    blockers: list[str] = []
    req = pipeline["requirements"]
    if mission.mission_type not in {str(x) for x in pipeline.get("missions") or []}:
        blockers.append("MISSION_NOT_SUPPORTED")
    if runtime.status not in {str(x) for x in req.get("runtimeStatus") or []}:
        blockers.append(f"RUNTIME_{runtime.status}_NOT_ALLOWED")
    minimum_vram = float(req.get("minimumVramGiB", 0) or 0)
    if runtime.vram_gib < minimum_vram:
        blockers.append(f"VRAM_{runtime.vram_gib:.1f}_BELOW_{minimum_vram:.1f}_GIB")
    execution = str(pipeline.get("execution"))
    if execution in {"hybrid", "remote"} and not mission.cloud_allowed:
        blockers.append("CLOUD_NOT_ALLOWED")
    required_caps = {str(x) for x in req.get("providerCapabilities") or []}
    if mission.require_identity:
        required_caps.update({"referenceImage", "subjectIdentity"})
    missing_caps = sorted(required_caps - set(runtime.capabilities))
    if missing_caps: blockers.append("MISSING_PROVIDER_CAPABILITIES:" + ",".join(missing_caps))
    if required_caps and not runtime.real_model_loaded: blockers.append("REAL_MODEL_NOT_LOADED")
    if mission.commercial_use and not runtime.commercial_use_allowed: blockers.append("COMMERCIAL_USE_NOT_APPROVED")
    missing_modules = sorted(str(x) for x in req.get("engineModules") or [] if modules.get(str(x), "MISSING") not in PROVEN_MODULE_STATUSES)
    if missing_modules: blockers.append("UNPROVEN_ENGINE_MODULES:" + ",".join(missing_modules))
    score = int(pipeline.get("priority", 0) or 0) + (10 if execution == "local" else 0)
    if mission.require_identity and {"referenceImage", "subjectIdentity"} <= required_caps: score += 20
    score += min(10, max(0, int(runtime.vram_gib - minimum_vram)))
    return not blockers, blockers, score

def plan_mission(registry: Mapping[str, Any], mission_value: Mapping[str, Any], inventory: Mapping[str, Any], provider: Mapping[str, Any] | None) -> dict[str, Any]:
    validate_registry(registry)
    mission = Mission.from_mapping(mission_value)
    if mission.mission_type not in {str(x) for x in registry["missionTypes"]}: raise PlanningError(f"unsupported missionType: {mission.mission_type}")
    runtime = RuntimeContext.from_evidence(inventory, provider)
    modules = {str(k): str(v.get("status") or "MISSING") for k, v in registry["engineModules"].items() if isinstance(v, Mapping)}
    evaluations: list[dict[str, Any]] = []
    for pipeline in registry["pipelines"]:
        eligible, blockers, score = evaluate(pipeline, mission, runtime, modules)
        evaluations.append({"pipelineId": pipeline["id"], "execution": pipeline["execution"], "eligible": eligible, "score": score, "blockers": blockers})
    eligible = sorted((x for x in evaluations if x["eligible"]), key=lambda x: (-x["score"], x["pipelineId"]))
    common = {"schema": SCHEMA, "jobId": mission.job_id, "missionType": mission.mission_type, "runtime": {"status": runtime.status, "vramGiB": runtime.vram_gib, "providerStatus": runtime.provider_status, "realModelLoaded": runtime.real_model_loaded, "capabilities": sorted(runtime.capabilities)}, "evaluations": evaluations, "mutationsAllowed": False, "requiresOperatorApproval": True, "secretsPersisted": False}
    if not eligible: return {**common, "status": "BLOCKED", "selectedPipeline": None, "execution": None, "stages": [], "blockers": sorted({b for e in evaluations for b in e["blockers"]})}
    selected = eligible[0]
    definition = next(p for p in registry["pipelines"] if p["id"] == selected["pipelineId"])
    return {**common, "status": "PLANNED", "selectedPipeline": selected["pipelineId"], "execution": selected["execution"], "stages": list(definition["stages"]), "requirements": definition["requirements"], "idempotencyKey": f"{mission.job_id}:{selected['pipelineId']}"}

def self_test() -> int:
    registry = load_object(Path(__file__).resolve().parents[1] / "config" / "echoes-capability-registry.v1.json", "registry")
    inventory = {"schema": "echoes.cinema-runtime-inventory.v1", "status": "PASS", "cuda": {"available": True, "totalMemoryGiB": 6.0}}
    provider = {"schema": "echoes.render-provider-health.v1", "status": "PASS", "realModelLoaded": True, "commercialUseAllowed": True, "capabilities": {"textToVideo": True, "referenceImage": True, "subjectIdentity": True}}
    identity = plan_mission(registry, {"jobId": "artist-video-001", "missionType": "music_video", "requireIdentity": True, "commercialUse": True, "cloudAllowed": False}, inventory, provider)
    assert identity["status"] == "PLANNED" and identity["selectedPipeline"] == "cinema_identity_local_v1"
    low_vram = {**inventory, "cuda": {"available": True, "totalMemoryGiB": 4.0}}
    blocked = plan_mission(registry, {"jobId": "artist-video-002", "missionType": "music_video", "requireIdentity": True, "commercialUse": False, "cloudAllowed": False}, low_vram, provider)
    assert blocked["status"] == "BLOCKED" and any("VRAM_" in b for b in blocked["blockers"])
    hybrid = plan_mission(registry, {"jobId": "artist-video-003", "missionType": "music_video", "requireIdentity": True, "commercialUse": False, "cloudAllowed": True}, low_vram, provider)
    assert hybrid["status"] == "PLANNED" and hybrid["selectedPipeline"] == "cinema_hybrid_identity_v1"
    audio = plan_mission(registry, {"jobId": "master-001", "missionType": "audio_master", "requireIdentity": False, "commercialUse": False, "cloudAllowed": False}, inventory, None)
    assert audio["status"] == "PLANNED" and audio["selectedPipeline"] == "audio_master_local_v1"
    drifted = {**inventory, "status": "BLOCKED"}
    fail_closed = plan_mission(registry, {"jobId": "master-002", "missionType": "audio_master", "requireIdentity": False, "commercialUse": False, "cloudAllowed": False}, drifted, None)
    assert fail_closed["status"] == "BLOCKED"
    print("KCoreMissionPlanner PASS identity=local low-vram=blocked hybrid=selected audio=local runtime-drift=blocked")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--registry", type=Path, default=root / "config" / "echoes-capability-registry.v1.json")
    parser.add_argument("--mission", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--provider-health", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.mission or not args.inventory:
        parser.error("--mission and --inventory are required unless --self-test is used")
    try:
        result = plan_mission(load_object(args.registry, "registry"), load_object(args.mission, "mission"), load_object(args.inventory, "runtime inventory"), load_object(args.provider_health, "provider health") if args.provider_health else None)
    except PlanningError as error:
        print(json.dumps({"schema": SCHEMA, "status": "BLOCKED", "failureClass": "INVALID_PLANNING_INPUT", "error": str(error), "mutationsAllowed": False, "secretsPersisted": False}, indent=2), file=sys.stderr)
        return 2
    rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["status"] == "PLANNED" else 3

if __name__ == "__main__":
    raise SystemExit(main())
