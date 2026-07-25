#!/usr/bin/env python3
"""Stable entrypoint for the self-healing pinned ModelScope proof provider.

The exact model loading and render implementation remains in
``modelscope_low_vram_provider_v2.py``. The resilient provider starts health
immediately, retries failed model loads with bounded backoff, preserves the
existing launcher path used by Windows and external contracts, and returns
HTTP 503 while the real model is still unavailable.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import Any

import modelscope_resilient_provider as resilient
from modelscope_resilient_provider import *  # noqa: F401,F403

_BASE_SELF_TEST = resilient.self_test


def provider_health_with_progress(payload: dict[str, Any]) -> dict[str, Any]:
    """Expose a stable byte counter for progress-aware P0 waiters.

    Older resilient health payloads report the model cache in GiB. P0 uses a
    byte counter in its progress signature. Publishing both prevents a long
    active model download from looking idle without changing the existing GiB
    field consumed by the dashboard.
    """

    result = dict(payload)
    if result.get("modelCacheBytes") is None and result.get("modelCacheGiB") is not None:
        try:
            gib = max(0.0, float(result["modelCacheGiB"]))
            result["modelCacheBytes"] = int(round(gib * (1024**3)))
        except (TypeError, ValueError, OverflowError):
            result["modelCacheBytes"] = None
    return result


def provider_not_ready_payload(engine: Any) -> dict[str, Any]:
    """Return a small, non-secret readiness response for render callers."""

    health = provider_health_with_progress(engine.health())
    load_state = str(health.get("loadState") or "LOADING")
    automatic_retry = bool(health.get("automaticRetry"))
    blocked = load_state == "BLOCKED"
    return {
        "schema": "echoes.render-provider-readiness.v1",
        "status": "BROKEN" if blocked else "PARTIAL",
        "error": "provider has no verified real model loaded",
        "realModelLoaded": False,
        "loadState": load_state,
        "retryable": automatic_retry,
        "nextRetryUtc": health.get("nextRetryUtc"),
        "failureClass": health.get("failureClass"),
        "operatorAction": health.get("operatorAction"),
        "modelCacheBytes": health.get("modelCacheBytes"),
        "modelCacheGiB": health.get("modelCacheGiB"),
    }


class ReadyAwareProviderHandler(resilient.ProviderHandler):
    """Keep health truthful and render requests fail-closed during recovery."""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health" and self._authorized():
            self._send_json(200, provider_health_with_progress(self.engine.health()))
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/v1/render" and self._authorized() and not self.engine.real_model_loaded:
            self._send_json(503, provider_not_ready_payload(self.engine))
            return
        super().do_POST()


def _request_json(url: str, token: str, *, method: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def self_test() -> int:
    assert _BASE_SELF_TEST() == 0

    converted = provider_health_with_progress({"modelCacheGiB": 1.5})
    assert converted["modelCacheBytes"] == int(1.5 * (1024**3))
    assert provider_health_with_progress({"modelCacheGiB": 1.5, "modelCacheBytes": 17})["modelCacheBytes"] == 17
    assert provider_health_with_progress({"modelCacheGiB": "bad"})["modelCacheBytes"] is None

    class DummyEngine:
        real_model_loaded = False

        @staticmethod
        def health() -> dict[str, Any]:
            return {
                "loadState": "RETRY_WAIT",
                "automaticRetry": True,
                "nextRetryUtc": "2099-01-01T00:00:00Z",
                "failureClass": "TRANSIENT_NETWORK",
                "operatorAction": "No operator action is required.",
                "modelCacheGiB": 1.25,
            }

    settings = resilient.Settings(
        token="ready-aware-self-test-token",
        model_id=resilient.DEFAULT_MODEL_ID,
        model_revision=resilient.DEFAULT_MODEL_REVISION,
        host="127.0.0.1",
        port=0,
        device="cuda",
        width=384,
        height=216,
        fps=4,
        inference_steps=15,
        guidance_scale=9.0,
        max_frames=16,
    )
    handler = type(
        "BoundReadyAwareSelfTestHandler",
        (ReadyAwareProviderHandler,),
        {"engine": DummyEngine(), "settings": settings},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(server.server_address[1])
        health_status, health = _request_json(
            f"http://127.0.0.1:{port}/health",
            settings.token,
            method="GET",
        )
        assert health_status == 200
        assert health["modelCacheGiB"] == 1.25
        assert health["modelCacheBytes"] == int(1.25 * (1024**3))

        status, payload = _request_json(
            f"http://127.0.0.1:{port}/v1/render",
            settings.token,
            method="POST",
            payload={
                "schema": "echoes.render-request.v1",
                "task": {"prompt": "self-test", "durationSeconds": 1.0},
            },
        )
        assert status == 503
        assert payload["schema"] == "echoes.render-provider-readiness.v1"
        assert payload["status"] == "PARTIAL"
        assert payload["realModelLoaded"] is False
        assert payload["loadState"] == "RETRY_WAIT"
        assert payload["retryable"] is True
        assert payload["failureClass"] == "TRANSIENT_NETWORK"
        assert payload["modelCacheBytes"] == int(1.25 * (1024**3))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3.0)

    print(
        "ModelScopeReadyAwareProvider PASS not-ready-render=503 "
        "recovery-state=preserved model-cache-bytes=progress-visible"
    )
    return 0


def main() -> int:
    original_handler = resilient.ProviderHandler
    original_self_test = resilient.self_test
    resilient.ProviderHandler = ReadyAwareProviderHandler
    resilient.self_test = self_test
    try:
        return resilient.main()
    finally:
        resilient.ProviderHandler = original_handler
        resilient.self_test = original_self_test


if __name__ == "__main__":
    raise SystemExit(main())
