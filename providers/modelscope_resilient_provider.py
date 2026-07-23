#!/usr/bin/env python3
"""Self-healing ModelScope provider for the first real Echoes Cinema proof.

The HTTP health endpoint comes online immediately. Model loading runs in a
background recovery loop. Transient failures retry with bounded exponential
backoff, storage pressure pauses without hammering the disk, and permanent
CUDA/package/model blockers stop cleanly instead of looping forever.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from modelscope_low_vram_provider_v2 import (  # noqa: F401
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_REVISION,
    NON_COMMERCIAL_LICENSE,
    LowVramModelScopeEngine,
    ProviderHandler,
    Settings,
    choose_offload_strategy,
    clamp_int,
    compact_error,
    is_cuda_oom,
    json_bytes,
    run_checked,
    safe_load_attempts,
)
from modelscope_low_vram_provider_v2 import self_test as base_self_test

RECOVERY_SCHEMA = "echoes.modelscope-provider-recovery.v1"
MIN_RETRY_SECONDS = 15.0
MAX_RETRY_SECONDS = 900.0
DEFAULT_MINIMUM_FREE_GIB = 20.0
STORAGE_RECHECK_SECONDS = 60.0


@dataclass(frozen=True)
class FailureDecision:
    failure_class: str
    retryable: bool
    operator_action: str
    maximum_same_failures: int | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retry_delay_seconds(failure_count: int, base_seconds: float = MIN_RETRY_SECONDS) -> float:
    count = max(1, int(failure_count))
    base = max(0.01, float(base_seconds))
    return min(MAX_RETRY_SECONDS, base * (2 ** min(count - 1, 10)))


def classify_load_error(error: str) -> FailureDecision:
    text = " ".join(str(error).strip().lower().split())
    network_tokens = (
        "connection reset",
        "connection aborted",
        "connection refused",
        "temporary failure",
        "name resolution",
        "timed out",
        "timeout",
        "incomplete read",
        "chunkedencodingerror",
        "remote end closed",
        "502 bad gateway",
        "503 service unavailable",
        "504 gateway timeout",
        "too many requests",
        "rate limit",
        "proxyerror",
        "sslerror",
    )
    storage_tokens = ("no space left", "errno 28", "disk quota", "not enough space")
    cuda_tokens = (
        "cuda was requested but torch.cuda.is_available() is false",
        "cuda driver version is insufficient",
        "no cuda gpus are available",
        "could not load library libcudart",
        "cudnn",
        "nvidia driver",
    )
    package_tokens = (
        "modulenotfounderror",
        "no module named",
        "importerror",
        "cannot import name",
        "unexpected keyword argument",
        "requires accelerate",
    )
    model_tokens = (
        "revision not found",
        "repository not found",
        "gated repo",
        "401 client error",
        "403 client error",
        "unauthorized",
        "forbidden",
        "safetensors weights not found",
        "no file named diffusion_pytorch_model",
        "variant=fp16",
    )
    memory_tokens = ("cuda out of memory", "out of memory", "cannot allocate memory")

    if any(token in text for token in storage_tokens):
        return FailureDecision(
            "STORAGE_EXHAUSTED",
            True,
            "Free space on drive D:. Recovery will recheck automatically without restarting Echoes Cinema.",
            None,
        )
    if any(token in text for token in network_tokens):
        return FailureDecision(
            "TRANSIENT_NETWORK",
            True,
            "No operator action is required. The download/load will resume automatically.",
            None,
        )
    if any(token in text for token in cuda_tokens):
        return FailureDecision(
            "CUDA_RUNTIME_BLOCKER",
            False,
            "Repair the NVIDIA driver/CUDA runtime, then restart Echoes Cinema once.",
            1,
        )
    if any(token in text for token in package_tokens):
        return FailureDecision(
            "PYTHON_RUNTIME_BLOCKER",
            False,
            "Repair the D-drive Cinema environment, then restart Echoes Cinema once.",
            1,
        )
    if any(token in text for token in model_tokens):
        return FailureDecision(
            "MODEL_CONTRACT_BLOCKER",
            False,
            "The pinned model revision or safetensors contract must be corrected before another launch.",
            1,
        )
    if any(token in text for token in memory_tokens):
        return FailureDecision(
            "MEMORY_CAPACITY_BLOCKER",
            False,
            "This model cannot load within the available GPU/system memory profile; use a smaller proof model or remote GPU.",
            1,
        )
    return FailureDecision(
        "UNKNOWN_LOAD_BLOCKER",
        True,
        "Echoes Cinema will retry this unknown blocker three times, then stop safely with full evidence.",
        3,
    )


def directory_size_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def model_cache_root() -> Path:
    explicit = os.getenv("HF_HUB_CACHE") or os.getenv("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return Path(explicit)
    home = os.getenv("HF_HOME")
    if home:
        return Path(home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def existing_anchor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def free_space_gib(path: Path) -> float:
    anchor = existing_anchor(path)
    try:
        return round(shutil.disk_usage(anchor).free / (1024**3), 2)
    except OSError:
        return 0.0


class ResilientModelScopeEngine(LowVramModelScopeEngine):
    def __init__(
        self,
        settings: Settings,
        *,
        retry_base_seconds: float = MIN_RETRY_SECONDS,
        minimum_free_gib: float = DEFAULT_MINIMUM_FREE_GIB,
    ) -> None:
        super().__init__(settings)
        self.retry_base_seconds = max(0.01, float(retry_base_seconds))
        self.minimum_free_gib = max(0.0, float(minimum_free_gib))
        self.load_state = "IDLE"
        self.recovery_count = 0
        self.consecutive_same_failure = 0
        self.failure_class: str | None = None
        self.retryable: bool | None = None
        self.operator_action = ""
        self.last_attempt_utc: str | None = None
        self.next_retry_utc: str | None = None
        self.ready_utc: str | None = None
        self.blocked_since_utc: str | None = None
        self.last_load_error = ""
        self.cache_root = model_cache_root()
        self.cache_bytes = 0
        self.cache_measured_utc: str | None = None
        self.workspace_free_gib = 0.0
        self._last_failure_signature = ""
        self._recovery_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._loader_thread: threading.Thread | None = None
        self._cache_thread: threading.Thread | None = None

    def _set_recovery(self, **updates: Any) -> None:
        with self._recovery_lock:
            for name, value in updates.items():
                setattr(self, name, value)

    def _measure_storage(self) -> None:
        self._set_recovery(
            cache_bytes=directory_size_bytes(self.cache_root),
            cache_measured_utc=utc_now(),
            workspace_free_gib=free_space_gib(self.cache_root),
        )

    def _cache_loop(self) -> None:
        while not self._stop_event.is_set():
            self._measure_storage()
            self._stop_event.wait(15.0)

    def _wait_for_storage(self) -> bool:
        self._measure_storage()
        if self.workspace_free_gib >= self.minimum_free_gib:
            return True
        self._set_recovery(
            load_state="WAITING_STORAGE",
            failure_class="STORAGE_PRESSURE",
            retryable=True,
            operator_action=(
                f"Free space on drive D:. {self.workspace_free_gib:.2f} GiB is available; "
                f"at least {self.minimum_free_gib:.2f} GiB is required. Recovery will recheck automatically."
            ),
            last_load_error="insufficient free space for safe model loading",
            next_retry_utc=(datetime.now(timezone.utc) + timedelta(seconds=STORAGE_RECHECK_SECONDS))
            .isoformat()
            .replace("+00:00", "Z"),
        )
        return False

    def _load_loop(self) -> None:
        while not self._stop_event.is_set() and not self.real_model_loaded:
            if not self._wait_for_storage():
                if self._stop_event.wait(STORAGE_RECHECK_SECONDS):
                    return
                continue

            self._set_recovery(
                load_state="LOADING",
                last_attempt_utc=utc_now(),
                next_retry_utc=None,
                blocked_since_utc=None,
            )
            self.load()
            self._measure_storage()
            if self.real_model_loaded:
                self._set_recovery(
                    load_state="READY",
                    ready_utc=utc_now(),
                    next_retry_utc=None,
                    last_load_error="",
                    failure_class=None,
                    retryable=None,
                    operator_action="",
                    blocked_since_utc=None,
                )
                return

            error = self.load_error or "model load ended without realModelLoaded=true"
            decision = classify_load_error(error)
            signature = f"{decision.failure_class}:{error}"
            same_count = self.consecutive_same_failure + 1 if signature == self._last_failure_signature else 1
            self._last_failure_signature = signature
            count = self.recovery_count + 1

            budget_exhausted = (
                decision.maximum_same_failures is not None and same_count >= decision.maximum_same_failures
            )
            if not decision.retryable or budget_exhausted:
                action = decision.operator_action
                if decision.retryable and budget_exhausted:
                    action = (
                        f"{action} Retry budget exhausted after {same_count} identical failures; "
                        "the provider stopped safely instead of looping forever."
                    )
                self._set_recovery(
                    load_state="BLOCKED",
                    recovery_count=count,
                    consecutive_same_failure=same_count,
                    failure_class=decision.failure_class,
                    retryable=False,
                    operator_action=action,
                    last_load_error=error,
                    next_retry_utc=None,
                    blocked_since_utc=utc_now(),
                )
                return

            delay = retry_delay_seconds(count, self.retry_base_seconds)
            next_retry = datetime.now(timezone.utc) + timedelta(seconds=delay)
            self._set_recovery(
                load_state="RETRY_WAIT",
                recovery_count=count,
                consecutive_same_failure=same_count,
                failure_class=decision.failure_class,
                retryable=True,
                operator_action=decision.operator_action,
                last_load_error=error,
                next_retry_utc=next_retry.isoformat().replace("+00:00", "Z"),
            )
            if self._stop_event.wait(delay):
                return

    def start_recovery(self) -> None:
        if self._loader_thread and self._loader_thread.is_alive():
            return
        self._stop_event.clear()
        self._loader_thread = threading.Thread(target=self._load_loop, name="echoes-model-loader", daemon=True)
        self._cache_thread = threading.Thread(target=self._cache_loop, name="echoes-model-cache-monitor", daemon=True)
        self._loader_thread.start()
        self._cache_thread.start()

    def stop_recovery(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        for thread in (self._loader_thread, self._cache_thread):
            if thread and thread.is_alive():
                thread.join(timeout=timeout)

    def health(self) -> dict[str, Any]:
        payload = super().health()
        with self._recovery_lock:
            automatic_retry = self.load_state in {"IDLE", "LOADING", "RETRY_WAIT", "WAITING_STORAGE"}
            recovery = {
                "recoverySchema": RECOVERY_SCHEMA,
                "loadState": self.load_state,
                "recoveryCount": self.recovery_count,
                "consecutiveSameFailure": self.consecutive_same_failure,
                "failureClass": self.failure_class,
                "retryable": self.retryable,
                "lastAttemptUtc": self.last_attempt_utc,
                "nextRetryUtc": self.next_retry_utc,
                "readyUtc": self.ready_utc,
                "blockedSinceUtc": self.blocked_since_utc,
                "lastLoadError": self.last_load_error or None,
                "operatorAction": self.operator_action or None,
                "retryBaseSeconds": self.retry_base_seconds,
                "maxRetrySeconds": MAX_RETRY_SECONDS,
                "minimumFreeGiB": self.minimum_free_gib,
                "workspaceFreeGiB": self.workspace_free_gib,
                "modelCacheRoot": str(self.cache_root),
                "modelCacheBytes": int(self.cache_bytes),
                "modelCacheGiB": round(self.cache_bytes / (1024**3), 2),
                "cacheMeasuredUtc": self.cache_measured_utc,
                "automaticRetry": automatic_retry,
                "operatorRestartRequired": self.load_state == "BLOCKED",
            }
        payload.update(recovery)
        if not self.real_model_loaded and recovery["automaticRetry"]:
            payload["loadError"] = None
        return payload


def wait_until(predicate: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("timed out waiting for resilient provider self-test state")


def self_test() -> int:
    assert base_self_test() == 0
    assert retry_delay_seconds(1) == 15.0
    assert retry_delay_seconds(2) == 30.0
    assert retry_delay_seconds(20) == MAX_RETRY_SECONDS
    assert classify_load_error("Connection reset by peer").failure_class == "TRANSIENT_NETWORK"
    assert classify_load_error("CUDA driver version is insufficient").retryable is False
    assert classify_load_error("No space left on device").failure_class == "STORAGE_EXHAUSTED"
    assert classify_load_error("unexpected mystery").maximum_same_failures == 3

    settings = Settings(
        token="self-test-token",
        model_id=DEFAULT_MODEL_ID,
        model_revision=DEFAULT_MODEL_REVISION,
        host="127.0.0.1",
        port=18081,
        device="cuda",
        width=384,
        height=216,
        fps=4,
        inference_steps=15,
        guidance_scale=9.0,
        max_frames=16,
    )

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeTorch:
        cuda = FakeCuda()

    recovering = ResilientModelScopeEngine(settings, retry_base_seconds=0.01, minimum_free_gib=0)
    attempts = {"count": 0}

    def transient_then_success() -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            recovering.pipeline = None
            recovering.export_to_video = None
            recovering.load_error = "Connection reset by peer"
            return
        recovering.pipeline = object()
        recovering.torch = FakeTorch()
        recovering.export_to_video = object()
        recovering.load_error = ""

    recovering.load = transient_then_success  # type: ignore[method-assign]
    recovering.start_recovery()
    wait_until(lambda: recovering.real_model_loaded)
    recovering.stop_recovery()
    assert recovering.load_state == "READY"
    assert recovering.recovery_count == 2
    health = recovering.health()
    assert health["schema"] == "echoes.render-provider-health.v1"
    assert health["recoverySchema"] == RECOVERY_SCHEMA
    assert health["realModelLoaded"] is True
    assert health["operatorRestartRequired"] is False

    blocked = ResilientModelScopeEngine(settings, retry_base_seconds=0.01, minimum_free_gib=0)

    def permanent_failure() -> None:
        blocked.pipeline = None
        blocked.export_to_video = None
        blocked.load_error = "CUDA driver version is insufficient for CUDA runtime version"

    blocked.load = permanent_failure  # type: ignore[method-assign]
    blocked.start_recovery()
    wait_until(lambda: blocked.load_state == "BLOCKED")
    blocked.stop_recovery()
    blocked_health = blocked.health()
    assert blocked_health["failureClass"] == "CUDA_RUNTIME_BLOCKER"
    assert blocked_health["automaticRetry"] is False
    assert blocked_health["operatorRestartRequired"] is True
    assert blocked_health["loadError"]
    print(
        "ModelScopeResilientProvider PASS transient-retry=validated permanent-block=validated "
        "storage-pause=enabled cache-progress=enabled"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--token", default="")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--height", type=int, default=216)
    parser.add_argument("--fps", type=int, default=4)
    parser.add_argument("--inference-steps", type=int, default=15)
    parser.add_argument("--guidance-scale", type=float, default=9.0)
    parser.add_argument("--max-frames", type=int, default=16)
    parser.add_argument("--retry-base-seconds", type=float, default=float(os.getenv("ECHOES_MODEL_RETRY_BASE_SECONDS", "15")))
    parser.add_argument("--minimum-free-gib", type=float, default=float(os.getenv("ECHOES_MODEL_MIN_FREE_GIB", "20")))
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()

    settings = Settings.from_args(args)
    engine = ResilientModelScopeEngine(
        settings,
        retry_base_seconds=args.retry_base_seconds,
        minimum_free_gib=args.minimum_free_gib,
    )
    handler = type("BoundResilientProviderHandler", (ProviderHandler,), {"engine": engine, "settings": settings})
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    engine.start_recovery()
    print(
        json.dumps(
            {
                "event": "provider-started",
                "host": settings.host,
                "port": settings.port,
                "realModelLoaded": False,
                "modelId": settings.model_id,
                "modelRevision": settings.model_revision,
                "loadState": "LOADING",
                "automaticRetry": True,
                "safeTensorOnly": True,
                "commercialUseAllowed": False,
                "minimumFreeGiB": args.minimum_free_gib,
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop_recovery()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
