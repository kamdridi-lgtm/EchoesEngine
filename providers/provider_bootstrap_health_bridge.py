#!/usr/bin/env python3
"""Loopback-only health bridge used while the real AI provider is preparing.

The bridge prevents connection-refused gaps during virtual-environment repair,
package verification, and model-provider handoff. It never accepts render work
and never reports a real model as loaded.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MODEL_ID = "ali-vilab/text-to-video-ms-1.7b"
MODEL_REVISION = "0951da43c60d797968ddbdb157bdf1d9d38704bc"
LICENSE = "CC-BY-NC-4.0"


def read_worker_status(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def health_payload(status_path: Path) -> dict[str, Any]:
    worker = read_worker_status(status_path)
    worker_status = str(worker.get("status") or "PREPARING").upper()
    if worker_status in {"BROKEN", "BLOCKED"}:
        load_state = "BLOCKED"
        failure_class = str(worker.get("failureClass") or "PYTHON_RUNTIME_BLOCKER")
        operator_action = str(worker.get("operatorAction") or worker.get("error") or "Inspect the provider-worker error log.")
        retryable = bool(worker.get("retryable", False))
    elif worker_status == "RETRY_WAIT":
        load_state = "RETRY_WAIT"
        failure_class = str(worker.get("failureClass") or "TRANSIENT_BOOTSTRAP")
        operator_action = str(worker.get("operatorAction") or "No action is required; preparation will retry automatically.")
        retryable = True
    else:
        load_state = "LOADING"
        failure_class = None
        operator_action = "No action is required. Echoes Cinema is preparing the D-drive AI environment."
        retryable = True

    return {
        "schema": "echoes.cinema-provider-health.v1",
        "status": "PARTIAL",
        "backendStatus": "PARTIAL",
        "realModelLoaded": False,
        "modelId": MODEL_ID,
        "modelRevision": MODEL_REVISION,
        "license": LICENSE,
        "commercialUseAllowed": False,
        "loadState": load_state,
        "failureClass": failure_class,
        "retryable": retryable,
        "operatorAction": operator_action,
        "automaticRetry": retryable,
        "operatorRestartRequired": bool(worker.get("operatorRestartRequired", False)),
        "recoveryCount": int(worker.get("recoveryCount", 0) or 0),
        "nextRetryUtc": worker.get("nextRetryUtc"),
        "lastAttemptUtc": worker.get("timestampUtc"),
        "modelCacheGiB": worker.get("modelCacheGiB"),
        "workspaceFreeGiB": worker.get("workspaceFreeGiB"),
        "minimumFreeGiB": worker.get("minimumFreeGiB"),
        "lastLoadError": worker.get("error"),
        "torchVersion": worker.get("torchVersion"),
        "torchCudaVersion": worker.get("torchCudaVersion"),
        "bootstrapBridge": True,
        "workerStatus": worker_status,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "EchoesProviderBootstrapBridge/1.1"

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._write_json(200, health_payload(self.server.status_path))
            return
        self._write_json(404, {"status": "MISSING", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/render":
            health = health_payload(self.server.status_path)
            self._write_json(
                503,
                {
                    "schema": "echoes.render-provider-readiness.v1",
                    "status": "BROKEN" if health.get("loadState") == "BLOCKED" else "PARTIAL",
                    "backendStatus": "PARTIAL",
                    "error": "The real provider is not ready. Render work was not accepted.",
                    "realModelLoaded": False,
                    "loadState": health.get("loadState"),
                    "failureClass": health.get("failureClass"),
                    "retryable": health.get("retryable"),
                    "operatorAction": health.get("operatorAction"),
                    "operatorRestartRequired": health.get("operatorRestartRequired"),
                },
            )
            return
        self._write_json(404, {"status": "MISSING", "error": "not found"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


class Server(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], status_path: Path):
        super().__init__(address, Handler)
        self.status_path = status_path


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as temp:
        status_path = Path(temp) / "provider-worker-status.json"
        status_path.write_text(json.dumps({"status": "BOOTSTRAPPING", "recoveryCount": 0}), encoding="utf-8")
        loading = health_payload(status_path)
        assert loading["realModelLoaded"] is False
        assert loading["loadState"] == "LOADING"
        assert loading["bootstrapBridge"] is True
        assert loading["operatorRestartRequired"] is False

        status_path.write_text(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "failureClass": "CUDA_RUNTIME_UNAVAILABLE",
                    "retryable": False,
                    "operatorRestartRequired": True,
                    "operatorAction": "Restart after updating the NVIDIA driver.",
                    "error": "torch.cuda.is_available() is false",
                    "torchVersion": "2.x+cu128",
                    "torchCudaVersion": "12.8",
                }
            ),
            encoding="utf-8",
        )
        blocked = health_payload(status_path)
        assert blocked["loadState"] == "BLOCKED"
        assert blocked["automaticRetry"] is False
        assert blocked["operatorRestartRequired"] is True
        assert blocked["failureClass"] == "CUDA_RUNTIME_UNAVAILABLE"
        assert blocked["torchCudaVersion"] == "12.8"

        server = Server(("127.0.0.1", 0), status_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = int(server.server_address[1])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["loadState"] == "BLOCKED"
            assert payload["operatorRestartRequired"] is True
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    print("provider bootstrap health bridge self-test PASS loading=online blocked=truthful render=fail-closed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("The bootstrap health bridge is loopback-only.")
    if args.status_file is None:
        raise SystemExit("--status-file is required")
    args.status_file.parent.mkdir(parents=True, exist_ok=True)
    server = Server((args.host, args.port), args.status_file)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
