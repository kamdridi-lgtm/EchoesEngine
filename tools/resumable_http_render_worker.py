#!/usr/bin/env python3
"""Resume an authenticated HTTP render without trusting stale or changed clips.

The worker reuses a prior task only when render_resume validates its SHA-256 and
H.264/yuv420p media evidence and the current provider identity/requirements match
the prior state. State is rewritten atomically after every task so another
interruption leaves a useful, truthful recovery point.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import http_render_worker as base
from render_capabilities import validate_provider_health
from render_resume import build_plan


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is unreadable or corrupt: {path}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return payload


def provider_identity(health: dict[str, Any]) -> dict[str, Any]:
    capabilities = health.get("capabilities") if isinstance(health.get("capabilities"), dict) else {}
    return {
        "backend": health.get("backend"),
        "modelId": health.get("modelId"),
        "modelRevision": health.get("modelRevision"),
        "realModelLoaded": health.get("realModelLoaded") is True,
        "commercialUseAllowed": health.get("commercialUseAllowed") is True,
        "license": health.get("license"),
        "capabilities": {key: capabilities[key] for key in sorted(capabilities)},
    }


def previous_tasks_by_id(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    tasks = state.get("tasks")
    if isinstance(tasks, list):
        for task in tasks:
            if isinstance(task, dict) and task.get("taskId"):
                indexed[str(task["taskId"])] = task
    return indexed


def compatibility_reason(
    previous: dict[str, Any],
    *,
    job_id: Any,
    current_identity: dict[str, Any],
    required_capabilities: set[str],
    require_real_model: bool,
    require_commercial_use: bool,
) -> str | None:
    if not previous:
        return "no prior render state"
    if previous.get("schema") != "echoes.render-state.v1":
        return "prior render-state schema is unsupported"
    if previous.get("jobId") != job_id:
        return "prior render-state jobId does not match the manifest"
    previous_health = previous.get("providerHealth")
    if not isinstance(previous_health, dict):
        return "prior render state has no provider health evidence"
    if provider_identity(previous_health) != current_identity:
        return "provider identity, model revision, license, or capabilities changed"
    if bool(previous.get("realModelRequired")) != require_real_model:
        return "real-model requirement changed"
    if bool(previous.get("commercialUseRequired")) != require_commercial_use:
        return "commercial-use requirement changed"
    if set(previous.get("requiredCapabilities") or []) != required_capabilities:
        return "required capability set changed"
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--endpoint", default=os.getenv("ECHOES_RENDER_ENDPOINT", ""))
    parser.add_argument("--health-url", default=os.getenv("ECHOES_RENDER_HEALTH_URL", ""))
    parser.add_argument("--token-env", default="ECHOES_RENDER_TOKEN")
    parser.add_argument(
        "--allow-hosts",
        default=os.getenv("ECHOES_RENDER_HOST_ALLOWLIST", "127.0.0.1,localhost"),
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--require-real-model", action="store_true")
    parser.add_argument("--require-commercial-use", action="store_true")
    parser.add_argument("--state", type=Path, default=None)
    args = parser.parse_args()

    state_path = args.state or (args.output_root / "render-state.json")
    previous_state: dict[str, Any] = {}
    state: dict[str, Any] = {
        "schema": "echoes.render-state.v1",
        "backend": "http-provider",
        "status": "RUNNING",
        "resumeRequested": True,
        "tasks": [],
        "reusedTaskCount": 0,
        "renderedTaskCount": 0,
    }

    try:
        if not args.endpoint:
            raise ValueError("render provider endpoint is required")
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        if not args.manifest.is_file():
            raise FileNotFoundError(f"render manifest not found: {args.manifest}")
        if state_path.is_file():
            previous_state = read_json_object(state_path, label="prior render state")

        manifest = read_json_object(args.manifest, label="render manifest")
        if manifest.get("schema") != "echoes.render-manifest.v1":
            raise ValueError("unsupported render manifest schema")
        tasks = manifest.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("render manifest contains no tasks")
        if not all(isinstance(task, dict) for task in tasks):
            raise ValueError("render manifest task must be an object")

        allowed_hosts = base.parse_allowlist(args.allow_hosts)
        base.validate_url(args.endpoint, allowed_hosts)
        health_url = args.health_url or base.default_health_url(args.endpoint)
        base.validate_url(health_url, allowed_hosts)
        token = os.getenv(args.token_env, "")
        health = base.fetch_provider_health(
            health_url,
            token=token,
            allowed_hosts=allowed_hosts,
            timeout_seconds=args.timeout,
        )
        required_capabilities = validate_provider_health(
            health,
            tasks=tasks,
            require_real_model=args.require_real_model,
            require_commercial_use=args.require_commercial_use,
        )

        ffprobe = base.require_tool("ffprobe")
        args.output_root.mkdir(parents=True, exist_ok=True)
        current_identity = provider_identity(health)
        incompatibility = compatibility_reason(
            previous_state,
            job_id=manifest.get("jobId"),
            current_identity=current_identity,
            required_capabilities=required_capabilities,
            require_real_model=args.require_real_model,
            require_commercial_use=args.require_commercial_use,
        )
        evidence_state = previous_state if incompatibility is None else {}
        plan = build_plan(manifest, evidence_state, args.output_root, ffprobe)
        reusable_ids = {str(item.get("taskId")) for item in plan["reusableTasks"]}
        prior_by_id = previous_tasks_by_id(previous_state)

        state.update(
            {
                "jobId": manifest.get("jobId"),
                "providerEndpoint": args.endpoint,
                "providerHealthUrl": health_url,
                "providerHealth": health,
                "providerIdentity": current_identity,
                "providerProtocol": "echoes.render-request.v1",
                "realModelRequired": args.require_real_model,
                "commercialUseRequired": args.require_commercial_use,
                "requiredCapabilities": sorted(required_capabilities),
                "priorStateFound": bool(previous_state),
                "reuseCompatible": incompatibility is None,
                "reuseBlocker": incompatibility,
                "resumePlan": plan,
            }
        )
        atomic_write_json(state_path, state)

        for task in tasks:
            task_id = str(task.get("id", ""))
            if task_id in reusable_ids:
                prior = prior_by_id.get(task_id)
                if not prior:
                    raise RuntimeError(f"validated reusable task has no prior state record: {task_id}")
                record = dict(prior)
                record["resumeStatus"] = "REUSED_VALIDATED"
                record["evidenceClassification"] = "SHA256_AND_MEDIA_QC_VALIDATED"
                state["tasks"].append(record)
                state["reusedTaskCount"] += 1
            else:
                record = base.render_task(
                    manifest=manifest,
                    task=task,
                    endpoint=args.endpoint,
                    token=token,
                    allowed_hosts=allowed_hosts,
                    output_root=args.output_root,
                    timeout_seconds=args.timeout,
                    ffprobe=ffprobe,
                )
                record["resumeStatus"] = "RENDERED"
                state["tasks"].append(record)
                state["renderedTaskCount"] += 1
            atomic_write_json(state_path, state)

        state["status"] = "PASS"
        state["taskCount"] = len(state["tasks"])
        state["durationSeconds"] = manifest.get("durationSeconds")
        atomic_write_json(state_path, state)
        print(
            f"ResumableHttpRenderWorker PASS tasks={len(state['tasks'])} "
            f"reused={state['reusedTaskCount']} rendered={state['renderedTaskCount']} "
            f"endpoint={args.endpoint}"
        )
        return_code = 0
    except Exception as error:  # noqa: BLE001 - exact recovery blocker belongs in state
        state["status"] = "FAILED"
        state["error"] = str(error)
        atomic_write_json(state_path, state)
        print(f"ResumableHttpRenderWorker ERROR: {error}", file=sys.stderr)
        return_code = 1

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
