#!/usr/bin/env python3
"""Resume authenticated HTTP renders and retry only transient provider failures.

The worker reuses a prior task only when render_resume validates its SHA-256 and
H.264/yuv420p media evidence and the current provider identity/requirements match
the prior state. State is rewritten atomically after every task and every retry so
an interruption leaves a truthful recovery point instead of a terminal P0 failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

import http_render_worker as base
from render_capabilities import validate_provider_health
from render_resume import build_plan

T = TypeVar("T")
RETRY_HISTORY_LIMIT = 20
TRANSIENT_HTTP_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
PERMANENT_HTTP_CODES = frozenset({400, 401, 403, 404, 405, 409, 410, 413, 415, 422})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


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


def validate_retry_health(
    health: dict[str, Any],
    *,
    tasks: list[dict[str, Any]],
    require_real_model: bool,
    require_commercial_use: bool,
) -> set[str]:
    load_state = str(health.get("loadState") or "").upper()
    if load_state == "BLOCKED" or health.get("operatorRestartRequired") is True:
        detail = str(health.get("lastLoadError") or health.get("loadError") or "provider recovery is blocked")
        action = str(health.get("operatorAction") or "").strip()
        suffix = f" | {action}" if action else ""
        raise RuntimeError(f"permanent provider blocker: {detail}{suffix}")
    if health.get("retryable") is False and health.get("realModelLoaded") is not True:
        detail = str(health.get("lastLoadError") or health.get("loadError") or "provider marked failure non-retryable")
        raise RuntimeError(f"permanent provider blocker: {detail}")
    return validate_provider_health(
        health,
        tasks=tasks,
        require_real_model=require_real_model,
        require_commercial_use=require_commercial_use,
    )


def classify_render_failure(error: BaseException | str) -> dict[str, Any]:
    message = str(error).strip()
    lowered = message.lower()
    http_match = re.search(r"provider http\s+(\d{3})", lowered)
    if http_match:
        code = int(http_match.group(1))
        if code in TRANSIENT_HTTP_CODES:
            return {"retryable": True, "failureClass": f"PROVIDER_HTTP_{code}", "message": message}
        if code in PERMANENT_HTTP_CODES:
            failure_class = "PROVIDER_AUTH_BLOCKED" if code in {401, 403} else f"PROVIDER_HTTP_{code}_PERMANENT"
            return {"retryable": False, "failureClass": failure_class, "message": message}

    permanent_markers = (
        "permanent provider blocker",
        "render provider endpoint is required",
        "timeout must be positive",
        "render manifest not found",
        "unsupported render manifest schema",
        "render manifest contains no tasks",
        "render manifest task must be an object",
        "provider host is not allowlisted",
        "unsupported provider url scheme",
        "plain http is allowed only",
        "render provider host allowlist is empty",
        "provider health schema is unsupported",
        "provider is not approved for commercial renders",
        "provider is missing required capabilities",
        "provider health has no capability contract",
        "required executable not found",
        "unsafe outputfile path",
        "must end in .mp4",
        "provider response exceeds maximum allowed size",
        "provider clip must be h.264",
        "provider clip must use yuv420p",
        "provider clip has invalid duration",
        "provider identity changed during retry",
    )
    if any(marker in lowered for marker in permanent_markers):
        return {"retryable": False, "failureClass": "RENDER_CONTRACT_PERMANENT", "message": message}

    transient_markers = (
        "provider connection failed",
        "connection refused",
        "connection reset",
        "remote end closed",
        "incomplete read",
        "broken pipe",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "provider health is not pass",
        "provider has no verified real model loaded",
        "provider did not return pass",
        "cuda out of memory",
        "out of memory",
        "server disconnected",
        "network is unreachable",
    )
    if any(marker in lowered for marker in transient_markers):
        return {"retryable": True, "failureClass": "PROVIDER_TRANSIENT", "message": message}

    return {"retryable": False, "failureClass": "UNKNOWN_PERMANENT", "message": message}


def retry_delay_seconds(initial: float, maximum: float, consecutive_failures: int) -> float:
    exponent = max(0, min(10, consecutive_failures - 1))
    return min(maximum, initial * (2**exponent))


def execute_with_transient_retries(
    operation: Callable[[], T],
    *,
    state: dict[str, Any],
    state_path: Path,
    task_id: str,
    initial_delay_seconds: float,
    max_delay_seconds: float,
    retry_limit: int,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    consecutive_failures = 0
    last_failure_class = ""
    while True:
        try:
            result = operation()
            state["status"] = "RUNNING"
            state.pop("currentRetry", None)
            state.pop("nextRetryUtc", None)
            atomic_write_json(state_path, state)
            return result
        except Exception as error:  # noqa: BLE001 - exact provider blocker belongs in durable state
            decision = classify_render_failure(error)
            if not decision["retryable"]:
                raise

            failure_class = str(decision["failureClass"])
            consecutive_failures = consecutive_failures + 1 if failure_class == last_failure_class else 1
            last_failure_class = failure_class
            retry_count = int(state.get("retryCount", 0) or 0) + 1
            if retry_limit > 0 and retry_count > retry_limit:
                raise RuntimeError(
                    f"transient render retry limit exhausted after {retry_limit} retries; last error: {error}"
                ) from error

            delay = retry_delay_seconds(initial_delay_seconds, max_delay_seconds, consecutive_failures)
            retry_event = {
                "timestampUtc": utc_now(),
                "taskId": task_id,
                "retryNumber": retry_count,
                "consecutiveFailureCount": consecutive_failures,
                "failureClass": failure_class,
                "error": str(error),
                "delaySeconds": delay,
                "nextRetryUtc": utc_after(delay),
            }
            history = list(state.get("retryHistory") or [])
            history.append(retry_event)
            state["retryHistory"] = history[-RETRY_HISTORY_LIMIT:]
            state["retryCount"] = retry_count
            state["status"] = "RETRY_WAIT"
            state["currentTaskId"] = task_id
            state["currentRetry"] = retry_event
            state["nextRetryUtc"] = retry_event["nextRetryUtc"]
            state["automaticRetry"] = True
            state["retryable"] = True
            atomic_write_json(state_path, state)
            print(
                f"ResumableHttpRenderWorker RETRY_WAIT task={task_id} retry={retry_count} "
                f"class={failure_class} delay={delay:.1f}s error={error}",
                file=sys.stderr,
                flush=True,
            )
            sleep_fn(delay)


def self_test() -> int:
    assert classify_render_failure("provider HTTP 503: loading")["retryable"] is True
    assert classify_render_failure("provider HTTP 429: busy")["retryable"] is True
    assert classify_render_failure("provider connection failed: refused")["retryable"] is True
    assert classify_render_failure("CUDA out of memory")["retryable"] is True
    assert classify_render_failure("provider HTTP 401: unauthorized")["retryable"] is False
    assert classify_render_failure("provider is missing required capabilities: textToVideo")["retryable"] is False
    assert classify_render_failure("unexpected local programming error")["retryable"] is False
    assert retry_delay_seconds(2.0, 30.0, 1) == 2.0
    assert retry_delay_seconds(2.0, 30.0, 4) == 16.0
    assert retry_delay_seconds(2.0, 30.0, 8) == 30.0

    with tempfile.TemporaryDirectory(prefix="echoes-p0-transient-retry-") as temporary:
        state_path = Path(temporary) / "render-state.json"
        state: dict[str, Any] = {"schema": "echoes.render-state.v1", "status": "RUNNING", "tasks": []}
        calls = 0
        sleeps: list[float] = []

        def transient_then_pass() -> dict[str, str]:
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise RuntimeError("provider HTTP 503: model worker restarting")
            return {"status": "PASS"}

        result = execute_with_transient_retries(
            transient_then_pass,
            state=state,
            state_path=state_path,
            task_id="proof-task",
            initial_delay_seconds=1.0,
            max_delay_seconds=8.0,
            retry_limit=0,
            sleep_fn=sleeps.append,
        )
        assert result["status"] == "PASS"
        assert calls == 3
        assert sleeps == [1.0, 2.0]
        durable = read_json_object(state_path, label="self-test state")
        assert durable["status"] == "RUNNING"
        assert durable["retryCount"] == 2
        assert len(durable["retryHistory"]) == 2
        assert "currentRetry" not in durable

        permanent_calls = 0

        def permanent_failure() -> None:
            nonlocal permanent_calls
            permanent_calls += 1
            raise RuntimeError("provider HTTP 403: forbidden")

        try:
            execute_with_transient_retries(
                permanent_failure,
                state={"schema": "echoes.render-state.v1", "status": "RUNNING", "tasks": []},
                state_path=Path(temporary) / "permanent-state.json",
                task_id="blocked-task",
                initial_delay_seconds=1.0,
                max_delay_seconds=8.0,
                retry_limit=0,
                sleep_fn=lambda _: None,
            )
        except RuntimeError as error:
            assert "403" in str(error)
        else:
            raise AssertionError("permanent provider failure was retried")
        assert permanent_calls == 1

    print("ResumableHttpRenderWorker self-test PASS transient=retry resume=durable permanent=fail-closed")
    return 0


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
    parser.add_argument(
        "--retry-initial-seconds",
        type=float,
        default=float(os.getenv("ECHOES_CINEMA_RENDER_RETRY_INITIAL", "15")),
    )
    parser.add_argument(
        "--retry-max-seconds",
        type=float,
        default=float(os.getenv("ECHOES_CINEMA_RENDER_RETRY_MAX", "300")),
    )
    parser.add_argument(
        "--retry-limit",
        type=int,
        default=int(os.getenv("ECHOES_CINEMA_RENDER_RETRY_LIMIT", "0")),
        help="Maximum transient retries per worker; 0 means unlimited.",
    )
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
        "retryCount": 0,
        "retryHistory": [],
        "automaticRetry": True,
        "retryable": True,
    }

    try:
        if not args.endpoint:
            raise ValueError("render provider endpoint is required")
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        if args.retry_initial_seconds <= 0:
            raise ValueError("retry initial delay must be positive")
        if args.retry_max_seconds < args.retry_initial_seconds:
            raise ValueError("retry max delay must be at least the initial delay")
        if args.retry_limit < 0:
            raise ValueError("retry limit cannot be negative")
        if not args.manifest.is_file():
            raise FileNotFoundError(f"render manifest not found: {args.manifest}")
        if state_path.is_file():
            previous_state = read_json_object(state_path, label="prior render state")
            state["retryCount"] = int(previous_state.get("retryCount", 0) or 0)
            state["retryHistory"] = list(previous_state.get("retryHistory") or [])[-RETRY_HISTORY_LIMIT:]

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
        required_capabilities = validate_retry_health(
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
                "retryPolicy": {
                    "mode": "TRANSIENT_ONLY",
                    "initialDelaySeconds": args.retry_initial_seconds,
                    "maxDelaySeconds": args.retry_max_seconds,
                    "retryLimit": args.retry_limit,
                    "retryLimitMeaning": "UNLIMITED" if args.retry_limit == 0 else "FINITE",
                },
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
                def render_operation() -> dict[str, Any]:
                    retry_health = base.fetch_provider_health(
                        health_url,
                        token=token,
                        allowed_hosts=allowed_hosts,
                        timeout_seconds=args.timeout,
                    )
                    validate_retry_health(
                        retry_health,
                        tasks=tasks,
                        require_real_model=args.require_real_model,
                        require_commercial_use=args.require_commercial_use,
                    )
                    retry_identity = provider_identity(retry_health)
                    if retry_identity != current_identity:
                        raise RuntimeError("provider identity changed during retry")
                    state["providerHealth"] = retry_health
                    return base.render_task(
                        manifest=manifest,
                        task=task,
                        endpoint=args.endpoint,
                        token=token,
                        allowed_hosts=allowed_hosts,
                        output_root=args.output_root,
                        timeout_seconds=args.timeout,
                        ffprobe=ffprobe,
                    )

                record = execute_with_transient_retries(
                    render_operation,
                    state=state,
                    state_path=state_path,
                    task_id=task_id,
                    initial_delay_seconds=args.retry_initial_seconds,
                    max_delay_seconds=args.retry_max_seconds,
                    retry_limit=args.retry_limit,
                )
                record["resumeStatus"] = "RENDERED"
                state["tasks"].append(record)
                state["renderedTaskCount"] += 1
            atomic_write_json(state_path, state)

        state["status"] = "PASS"
        state["taskCount"] = len(state["tasks"])
        state["durationSeconds"] = manifest.get("durationSeconds")
        state.pop("currentTaskId", None)
        state.pop("currentRetry", None)
        state.pop("nextRetryUtc", None)
        atomic_write_json(state_path, state)
        print(
            f"ResumableHttpRenderWorker PASS tasks={len(state['tasks'])} "
            f"reused={state['reusedTaskCount']} rendered={state['renderedTaskCount']} "
            f"retries={state['retryCount']} endpoint={args.endpoint}"
        )
        return_code = 0
    except Exception as error:  # noqa: BLE001 - exact recovery blocker belongs in state
        decision = classify_render_failure(error)
        state["status"] = "FAILED"
        state["error"] = str(error)
        state["retryable"] = bool(decision["retryable"])
        state["failureClass"] = decision["failureClass"]
        state["automaticRetry"] = False
        atomic_write_json(state_path, state)
        print(
            f"ResumableHttpRenderWorker ERROR class={decision['failureClass']} retryable={decision['retryable']}: {error}",
            file=sys.stderr,
        )
        return_code = 1

    return return_code


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        raise SystemExit(self_test())
    raise SystemExit(main())
