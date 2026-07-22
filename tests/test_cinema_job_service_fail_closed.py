#!/usr/bin/env python3
"""Verify that the Cinema job service rejects jobs when no real model is loaded."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "tools" / "cinema_job_service.py"
PROVIDER_PORT = 18084
SERVICE_PORT = 18085
PROVIDER_TOKEN = "provider-token"
SERVICE_TOKEN = "service-token"


class ProviderHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != f"Bearer {PROVIDER_TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps(
            {
                "schema": "echoes.render-provider-health.v1",
                "status": "PASS",
                "backend": "diffusers-local",
                "realModelLoaded": False,
                "modelId": None,
                "loadError": "model not configured",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def http_json(url: str, *, token: str, body: dict | None = None) -> tuple[int, dict]:
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_service() -> dict:
    url = f"http://127.0.0.1:{SERVICE_PORT}/health"
    for _ in range(80):
        try:
            status, payload = http_json(url, token=SERVICE_TOKEN)
            if status == 200:
                return payload
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("Cinema job service did not become healthy")


def main() -> int:
    provider = ThreadingHTTPServer(("127.0.0.1", PROVIDER_PORT), ProviderHandler)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()

    with tempfile.TemporaryDirectory(prefix="echoes-cinema-service-test-") as temp_dir:
        temp = Path(temp_dir)
        sections_root = temp / "sections"
        output_root = temp / "output"
        sections_root.mkdir()
        output_root.mkdir()
        (sections_root / "song.csv").write_text("intro,0,2,0.2,0.4,0.2,0.3,80,false\n", encoding="utf-8")
        manifest_cli = temp / "RenderManifestCli"
        manifest_cli.write_text("contract placeholder\n", encoding="utf-8")

        env = os.environ.copy()
        env["ECHOES_RENDER_TOKEN"] = PROVIDER_TOKEN
        env["ECHOES_RENDER_ENDPOINT"] = f"http://127.0.0.1:{PROVIDER_PORT}/v1/render"
        env["ECHOES_RENDER_HEALTH_URL"] = f"http://127.0.0.1:{PROVIDER_PORT}/health"
        process = subprocess.Popen(
            [
                sys.executable,
                str(SERVICE),
                "--host",
                "127.0.0.1",
                "--port",
                str(SERVICE_PORT),
                "--token",
                SERVICE_TOKEN,
                "--manifest-cli",
                str(manifest_cli),
                "--sections-root",
                str(sections_root),
                "--output-root",
                str(output_root),
                "--provider-timeout",
                "3",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            health = wait_for_service()
            assert health["schema"] == "echoes.cinema-service-health.v1"
            assert health["status"] == "PASS"
            assert health["realModelLoaded"] is False
            assert health["acceptingRealJobs"] is False

            unauthorized_status, _ = http_json(
                f"http://127.0.0.1:{SERVICE_PORT}/health",
                token="wrong-token",
            )
            assert unauthorized_status == 401

            request = {
                "schema": "echoes.cinema-job-request.v1",
                "jobId": "blocked-real-job",
                "sectionsCsv": "song.csv",
                "seed": 42,
            }
            status, payload = http_json(
                f"http://127.0.0.1:{SERVICE_PORT}/v1/cinema/jobs",
                token=SERVICE_TOKEN,
                body=request,
            )
            assert status == 503
            assert payload["status"] == "FAILED"
            assert "no verified real model loaded" in payload["error"]
            assert not (output_root / "blocked-real-job").exists()

            traversal_request = {
                "schema": "echoes.cinema-job-request.v1",
                "jobId": "traversal-job",
                "sectionsCsv": "../song.csv",
            }
            traversal_status, traversal = http_json(
                f"http://127.0.0.1:{SERVICE_PORT}/v1/cinema/jobs",
                token=SERVICE_TOKEN,
                body=traversal_request,
            )
            assert traversal_status == 400
            assert "safe relative path" in traversal["error"]

            print("CinemaJobServiceFailClosed PASS")
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout:
                output = process.stdout.read()
                if output:
                    print(output)
            provider.shutdown()
            provider.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
