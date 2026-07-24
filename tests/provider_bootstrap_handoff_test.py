#!/usr/bin/env python3
"""Executable proof for the Echoes Cinema bootstrap-provider port handoff.

The test starts the repository's real bootstrap health bridge, verifies its
truthful fail-closed states, stops it, and starts a contract fixture on the
same loopback port. The fixture never loads a model or creates media.
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
from typing import Any, Callable

HOST = "127.0.0.1"


def request_json(url: str, *, method: str = "GET", expected: int = 200) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=b"{}" if method == "POST" else None,
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
    if status != expected:
        raise AssertionError(f"{method} {url}: expected HTTP {expected}, got {status}: {payload}")
    if not isinstance(payload, dict):
        raise AssertionError(f"{method} {url}: expected a JSON object")
    return payload


def wait_for_json(
    url: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = request_json(url)
            if predicate(payload):
                return payload
        except (OSError, ValueError, AssertionError) as error:
            last_error = error
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {url}; last error: {last_error}")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind((HOST, 0))
        return int(listener.getsockname()[1])


def wait_port_released(port: int, *, timeout_seconds: float = 8.0) -> None:
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
    raise AssertionError(f"Port {port} stayed occupied: {last_error}")


def stop_process(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def write_status(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def verify_worker_handoff_order(worker_path: Path) -> None:
    worker = worker_path.read_text(encoding="utf-8-sig")
    positions = {
        "bridge launch": worker.find("$bridgeProcess = Start-Process"),
        "bridge shutdown": worker.find("Stop-ChildProcess -Process $bridgeProcess"),
        "port release gate": worker.find("Wait-PortReleased -Port $ProviderPort"),
        "real provider launch": worker.rfind("$providerProcess = Start-Process -FilePath $venvPython"),
    }
    missing = [label for label, position in positions.items() if position < 0]
    if missing:
        raise AssertionError(f"Provider worker is missing handoff stages: {missing}")
    order = [
        positions["bridge launch"],
        positions["bridge shutdown"],
        positions["port release gate"],
        positions["real provider launch"],
    ]
    if order != sorted(order):
        raise AssertionError(f"Unsafe provider handoff order: {positions}")


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "EchoesProviderHandoffFixture/1.0"

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self.send_json(
                200,
                {
                    "schema": "echoes.cinema-provider-health.v1",
                    "status": "MOCK",
                    "backendStatus": "MOCK",
                    "realModelLoaded": False,
                    "bootstrapBridge": False,
                    "contractFixture": True,
                },
            )
            return
        self.send_json(404, {"status": "MISSING", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/render":
            self.send_json(
                200,
                {
                    "status": "MOCK",
                    "backendStatus": "MOCK",
                    "accepted": True,
                    "contractFixture": True,
                    "artifact": None,
                },
            )
            return
        self.send_json(404, {"status": "MISSING", "error": "not found"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


class ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True


def serve_fixture(port: int) -> int:
    server = ReusableServer((HOST, port), FixtureHandler)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


def run_test() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    bridge_path = repo_root / "providers" / "provider_bootstrap_health_bridge.py"
    worker_path = repo_root / "scripts" / "echoes-cinema-provider-worker.ps1"
    if not bridge_path.is_file() or not worker_path.is_file():
        raise AssertionError("Required provider bridge or worker file is missing")
    verify_worker_handoff_order(worker_path)

    port = free_port()
    health_url = f"http://{HOST}:{port}/health"
    render_url = f"http://{HOST}:{port}/v1/render"

    with tempfile.TemporaryDirectory(prefix="echoes-provider-handoff-") as temp:
        root = Path(temp)
        status_path = root / "provider-worker-status.json"
        logs = [
            (root / "bridge.stdout.log").open("wb"),
            (root / "bridge.stderr.log").open("wb"),
            (root / "fixture.stdout.log").open("wb"),
            (root / "fixture.stderr.log").open("wb"),
        ]
        bridge_process: subprocess.Popen[Any] | None = None
        fixture_process: subprocess.Popen[Any] | None = None
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
                stdout=logs[0],
                stderr=logs[1],
                creationflags=creation_flags(),
            )

            loading = wait_for_json(health_url, lambda value: value.get("loadState") == "LOADING")
            assert loading["status"] == "PARTIAL"
            assert loading["backendStatus"] == "PARTIAL"
            assert loading["realModelLoaded"] is False
            assert loading["bootstrapBridge"] is True
            assert loading["workerStatus"] == "BOOTSTRAPPING"

            rejected = request_json(render_url, method="POST", expected=503)
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
                lambda value: value.get("loadState") == "RETRY_WAIT",
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
            blocked = wait_for_json(health_url, lambda value: value.get("loadState") == "BLOCKED")
            assert blocked["retryable"] is False
            assert blocked["automaticRetry"] is False
            assert blocked["operatorAction"] == "integration-test blocker"
            assert blocked["realModelLoaded"] is False

            stop_process(bridge_process)
            bridge_process = None
            wait_port_released(port)

            fixture_process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--serve-fixture", "--port", str(port)],
                cwd=repo_root,
                stdout=logs[2],
                stderr=logs[3],
                creationflags=creation_flags(),
            )
            fixture = wait_for_json(health_url, lambda value: value.get("contractFixture") is True)
            assert fixture["status"] == "MOCK"
            assert fixture["backendStatus"] == "MOCK"
            assert fixture["realModelLoaded"] is False
            assert fixture["bootstrapBridge"] is False

            accepted = request_json(render_url, method="POST", expected=200)
            assert accepted["accepted"] is True
            assert accepted["artifact"] is None
            assert accepted["contractFixture"] is True
        finally:
            stop_process(bridge_process)
            stop_process(fixture_process)
            for log in logs:
                log.close()

    print(
        json.dumps(
            {
                "status": "PASS",
                "proof": "bootstrap bridge -> same-port provider contract fixture",
                "bridgeFailClosed": True,
                "portReleasedBeforeReplacement": True,
                "realAiMediaGenerated": False,
                "replacementProvider": "MOCK",
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serve-fixture", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.serve_fixture:
        if args.port <= 0:
            raise SystemExit("--port must be positive")
        return serve_fixture(args.port)
    return run_test()


if __name__ == "__main__":
    raise SystemExit(main())
