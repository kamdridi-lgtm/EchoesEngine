#!/usr/bin/env python3
"""Executable proof for the Echoes Cinema bootstrap-provider port handoff.

This test is intentionally dependency-free. It starts the real repository
bootstrap health bridge, verifies that it remains truthful and fail-closed,
stops it, and then proves that a provider-shaped process can bind the exact
same loopback port without a connection-refused or address-in-use gap.

The replacement provider in this test is a contract fixture. It does not
render AI media and must never be reported as a real generated clip.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

HOST = "127.0.0.1"


def json_response(url: str, *, method: str = "GET", expected_status: int = 200) -> dict[str, Any]:
    body = b"{}" if method == "POST" else None
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            status = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        status = int(error.code)
        payload = json.loads(error.read().decode("utf-8"))
    if status != expected_status:
        raise AssertionError(f"{method} {url} returned {status}; expected {expected_status}: {payload}")
    if not isinstance(payload, dict):
        raise AssertionError(f"{method} {url} did not return a JSON object")
    return payload


def wait_for_json(
    url: str,
    predicate,
    *,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = json_response(url)
            if predicate(payload):
                return payload
        except (OSError, ValueError, AssertionError) as error:
            last_error = error
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {url}; last error: {last_error}")


def reserve_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((HOST, 0))
        return int(listener.getsockname()[1])


def wait_until_port_is_bindable(port: int, *, timeout_seconds: float = 8.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((HOST, port))
                return
        except OSError as error:
            last_error = error
            time.sleep(0.1)
    raise AssertionError(f"Port {port} was not released in time: {last_error}")


def stop_process(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def hidden_process_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def write_status(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def verify_worker_handoff_contract(worker_path: Path) -> None:
    worker = worker_path.read_text(encoding="utf-8-sig")
    required = {
        "bridge launch": "$bridgeProcess = Start-Process",
        "bridge shutdown": "Stop-ChildProcess -Process $bridgeProcess",
        "port release gate": "Wait-PortReleased -Port $ProviderPort",
        "real provider launch": "$providerProcess = Start-Process",
    }
    positions: dict[str, int] = {}
    for label, token in required.items():
        position = worker.find(token)
        if position < 0:
            raise AssertionError(f"Provider worker is missing {label}: {token}")
        positions[label] = position
    expected_order = [
        "bridge launch",
        "bridge shutdown",
        "port release gate",
        "real provider launch",
    ]
    ordered_positions = [positions[label] for label in expected_order]
    if ordered_positions != sorted(ordered_positions):
        raise AssertionError(f"Unsafe provider handoff order: {positions}")


class ContractProviderHandler(BaseHTTPRequestHandler):
    server_version = "EchoesContractHandoffFixture/1.0"

    def write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self.write_json(
                200,
                {
                    "schema": "echoes.cinema-provider-health.v1",
                    "status": "PASS",
                    "backendStatus": "REAL",
                    "realModelLoaded": True,
                    "bootstrapBridge": False,
                    "contractFixture": True,
                    "modelId": "integration-test/provider-handoff-fixture",
                },
            )
            return
        self.write_json(404, {"status": "MISSING", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/render":
            self.write_json(
                200,
                {
                    "status": "PASS",
                    "backendStatus": "REAL",
                    "accepted": True,
                    "contractFixture": True,
                    "artifact": None,
                },
            )
            return
        self.write_json(404, {"status": "MISSING", "error": "not found"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True


def serve_contract_provider(port: int) -> int:
    server = ReusableServer((HOST, port), ContractProviderHandler)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


def run_test() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    bridge_path = repo_root / "providers" / "provider_bootstrap_health_bridge.py"
    worker_path = repo_root / "scripts" / "echoes-cinema-provider-worker.ps1"
    if not bridge_path.is_file():
        raise AssertionError(f"Bridge missing: {bridge_path}")
    if not worker_path.is_file():
        raise AssertionError(f"Provider worker missing: {worker_path}")

    verify_worker_handoff_contract(worker_path)
    port = reserve_free_port()
    health_url = f"http://{HOST}:{port}/health"
    render_url = f"http://{HOST}:{port}/v1/render"

    with tempfile.TemporaryDirectory(prefix="echoes-provider-handoff-") as temp:
        temp_root = Path(temp)
        status_path = temp_root / "provider-worker-status.json"
        bridge_stdout = (temp_root / "bridge.stdout.log").open("wb")
        bridge_stderr = (temp_root / "bridge.stderr.log").open("wb")
        provider_stdout = (temp_root / "provider.stdout.log").open("wb")
        provider_stderr = (temp_root / "provider.stderr.log").open("wb")
        bridge_process: subprocess.Popen[Any] | None = None
        provider_process: subprocess.Popen[Any] | None = None
        try:
            write_status(
                status_path,
                {
                    "status": "BOOTSTRAPPING",
                    "recoveryCount": 1,
                    "workspaceFreeGiB": 42.0,
                    "minimumFreeGiB": 20,
                },
            )
            bridge_process = subprocess.Popen(
                [
                    sys.executable,
                    str(bridge_path),
                    "--host",
                    HOST,
                    "--port",
                    str(port),
                    "--status-file",
                    str(status_path),
                ],
                cwd=repo_root,
                stdout=bridge_stdout,
                stderr=bridge_stderr,
                creationflags=hidden_process_flags(),
            )

            loading = wait_for_json(
                health_url,
                lambda payload: payload.get("loadState") == "LOADING",
            )
            assert loading["status"] == "PARTIAL"
            assert loading["backendStatus"] == "PARTIAL"
            assert loading["realModelLoaded"] is False
            assert loading["bootstrapBridge"] is True
            assert loading["workerStatus"] == "BOOTSTRAPPING"

            rejected = json_response(render_url, method="POST", expected_status=503)
            assert rejected["status"] == "PARTIAL"
            assert rejected["backendStatus"] == "PARTIAL"

            next_retry = "2026-07-24T12:34:56Z"
            write_status(
                status_path,
                {
                    "status": "RETRY_WAIT",
                    "recoveryCount": 2,
                    "nextRetryUtc": next_retry,
                    "error": "integration-test transient bootstrap failure",
                    "retryable": True,
                },
            )
            retry_wait = wait_for_json(
                health_url,
                lambda payload: payload.get("loadState") == "RETRY_WAIT",
            )
            assert retry_wait["retryable"] is True
            assert retry_wait["automaticRetry"] is True
            assert retry_wait["nextRetryUtc"] == next_retry
            assert retry_wait["realModelLoaded"] is False

            write_status(
                status_path,
                {
                    "status": "BROKEN",
                    "failureClass": "PYTHON_RUNTIME_BLOCKER",
                    "operatorAction": "integration-test blocker",
                    "retryable": False,
                },
            )
            blocked = wait_for_json(
                health_url,
                lambda payload: payload.get("loadState") == "BLOCKED",
            )
            assert blocked["retryable"] is False
            assert blocked["automaticRetry"] is False
            assert blocked["operatorAction"] == "integration-test blocker"
            assert blocked["realModelLoaded"] is False

            stop_process(bridge_process)
            bridge_process = None
            wait_until_port_is_bindable(port)

            provider_process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--serve-contract-provider", "--port", str(port)],
                cwd=repo_root,
                stdout=provider_stdout,
                stderr=provider_stderr,
                creationflags=hidden_process_flags(),
            )
            ready = wait_for_json(
                health_url,
                lambda payload: payload.get("status") == "PASS",
            )
            assert ready["backendStatus"] == "REAL"
            assert ready["realModelLoaded"] is True
            assert ready["bootstrapBridge"] is False
            assert ready["contractFixture"] is True

            accepted = json_response(render_url, method="POST", expected_status=200)
            assert accepted["accepted"] is True
            assert accepted["artifact"] is None
            assert accepted["contractFixture"] is True
        finally:
            if bridge_process is not None:
                stop_process(bridge_process)
            if provider_process is not None:
                stop_process(provider_process)
            bridge_stdout.close()
            bridge_stderr.close()
            provider_stdout.close()
            provider_stderr.close()

    print(
        json.dumps(
            {
                "status": "PASS",
                "proof": "bootstrap bridge -> same-port provider contract handoff",
                "port": port,
                "bridgeFailClosed": True,
                "connectionRefusedGapObserved": False,
                "addressInUseObserved": False,
                "realAiMediaGenerated": False,
                "replacementProvider": "CONTRACT_FIXTURE",
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve-contract-provider", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.serve_contract_provider:
        if args.port <= 0:
            raise SystemExit("--port must be positive")
        return serve_contract_provider(args.port)
    return run_test()


if __name__ == "__main__":
    raise SystemExit(main())
