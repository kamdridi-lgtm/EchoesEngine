#!/usr/bin/env python3
"""Self-healing ModelScope provider for the first real Echoes Cinema proof.

The HTTP health endpoint comes online immediately. Model loading runs in a
background recovery loop with bounded exponential backoff, so a temporary
network/download/driver failure does not require the operator to restart the
whole stack. The underlying pinned/safetensors-only provider remains the source
of truth for loading and rendering.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def retry_delay_seconds(failure_count: int, base_seconds: float = MIN_RETRY_SECONDS) -> float:
    count = max(1, int(failure_count))
    base = max(0.01, float(base_seconds))
    return min(MAX_RETRY_SECONDS, base * (2 ** min(count - 1, 10)))


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


class ResilientModelScopeEngine(LowVramModelScopeEngine):
    def __init__(self, settings: Settings, *, retry_base_seconds: float = MIN_RETRY_SECONDS) -> None:
        super().__init__(settings)
        self.retry_base_seconds = max(0.01, float(retry_base_seconds))
        self.load_state = "IDLE"
        self.recovery_count = 0
        self.last_attempt_utc: str | None = None
        self.next_retry_utc: str | None = None
        self.ready_utc: str | None = None
        self.last_load_error = ""
        self.cache_root = model_cache_root()
        self.cache_bytes = 0
        self.cache_measured_utc: str | None = None
        self._recovery_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._loader_thread: threading.Thread | None = None
        self._cache_thread: threading.Thread | None = None

    def _set_recovery(self, **updates: Any) -> None:
        with self._recovery_lock:
            for name, value in updates.items():
                setattr(self, name, value)

    def _measure_cache(self) -> None:
        self._set_recovery(
            cache_bytes=directory_size_bytes(self.cache_root),
            cache_measured_utc=utc_now(),
        )

    def _cache_loop(self) -> None:
        while not self._stop_event.is_set():
            self._measure_cache()
            self._stop_event.wait(15.0)

    def _load_loop(self) -> None:
        while not self._stop_event.is_set() and not self.real_model_loaded:
            self._set_recovery(
                load_state="LOADING",
                last_attempt_utc=utc_now(),
                next_retry_utc=None,
            )
            self.load()
            self._measure_cache()
            if self.real_model_loaded:
                self._set_recovery(
                    load_state="READY",
                    ready_utc=utc_now(),
                    next_retry_utc=None,
                    last_load_error="",
                )
                return

            error = self.load_error or "model load ended without realModelLoaded=true"
            count = self.recovery_count + 1
            delay = retry_delay_seconds(count, self.retry_base_seconds)
            next_retry = datetime.now(timezone.utc) + timedelta(seconds=delay)
            self._set_recovery(
                load_state="RETRY_WAIT",
                recovery_count=count,
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
            recovery = {
                "schema": RECOVERY_SCHEMA,
                "loadState": self.load_state,
                "recoveryCount": self.recovery_count,
                "lastAttemptUtc": self.last_attempt_utc,
                "nextRetryUtc": self.next_retry_utc,
                "readyUtc": self.ready_utc,
                "lastLoadError": self.last_load_error or None,
                "retryBaseSeconds": self.retry_base_seconds,
                "maxRetrySeconds": MAX_RETRY_SECONDS,
                "modelCacheRoot": str(self.cache_root),
                "modelCacheBytes": int(self.cache_bytes),
                "modelCacheGiB": round(self.cache_bytes / (1024**3), 2),
                "cacheMeasuredUtc": self.cache_measured_utc,
                "automaticRetry": True,
                "operatorRestartRequired": False,
            }
        payload.update(recovery)
        if not self.real_model_loaded and recovery["loadState"] in {"LOADING", "RETRY_WAIT", "IDLE"}:
            payload["loadError"] = None
        return payload


def self_test() -> int:
    assert base_self_test() == 0
    assert retry_delay_seconds(1) == 15.0
    assert retry_delay_seconds(2) == 30.0
    assert retry_delay_seconds(20) == MAX_RETRY_SECONDS

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
    engine = ResilientModelScopeEngine(settings, retry_base_seconds=0.01)
    attempts = {"count": 0}

    class FakeCuda:
        @staticmethod
        def is_available() -> bool:
            return False

    class FakeTorch:
        cuda = FakeCuda()

    def fake_load() -> None:
        attempts["count"] += 1
        if attempts["count"] < 3:
            engine.pipeline = None
            engine.export_to_video = None
            engine.load_error = f"temporary self-test failure {attempts['count']}"
            return
        engine.pipeline = object()
        engine.torch = FakeTorch()
        engine.export_to_video = object()
        engine.load_error = ""

    engine.load = fake_load  # type: ignore[method-assign]
    engine.start_recovery()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not engine.real_model_loaded:
        time.sleep(0.01)
    engine.stop_recovery()
    assert engine.real_model_loaded
    assert engine.load_state == "READY"
    assert engine.recovery_count == 2
    assert engine.next_retry_utc is None
    health = engine.health()
    assert health["automaticRetry"] is True
    assert health["operatorRestartRequired"] is False
    assert health["loadState"] == "READY"
    assert health["realModelLoaded"] is True
    assert health["loadError"] is None
    print("ModelScopeResilientProvider PASS async-health=enabled retry=bounded cache-progress=enabled")
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
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()

    settings = Settings.from_args(args)
    engine = ResilientModelScopeEngine(settings, retry_base_seconds=args.retry_base_seconds)
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
