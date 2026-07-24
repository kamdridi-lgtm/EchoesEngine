#!/usr/bin/env python3
"""Verify that the local Diffusers provider never claims a missing model is real."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "providers" / "diffusers_video_provider.py"
PORT = 18083
TOKEN = "diffusers-contract-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, *, token: str = TOKEN, body: dict | None = None) -> tuple[int, str, bytes]:
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    method = "GET"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, response.headers.get_content_type(), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get_content_type(), error.read()


def wait_for_health() -> dict:
    for _ in range(80):
        try:
            status, content_type, body = request("/health")
            if status == 200 and content_type == "application/json":
                return json.loads(body.decode("utf-8"))
        except Exception:
            pass
        time.sleep(0.1)
    raise RuntimeError("provider did not become healthy")


def main() -> int:
    env = os.environ.copy()
    env.pop("ECHOES_DIFFUSERS_MODEL_ID", None)
    env["ECHOES_RENDER_TOKEN"] = TOKEN
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROVIDER),
            "--host",
            "127.0.0.1",
            "--port",
            str(PORT),
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        health = wait_for_health()
        assert health["schema"] == "echoes.render-provider-health.v1"
        assert health["status"] == "PASS"
        assert health["backend"] == "diffusers-local"
        assert health["realModelLoaded"] is False
        assert "not configured" in health["loadError"]

        unauthorized_status, _, _ = request("/health", token="wrong-token")
        assert unauthorized_status == 401

        render_request = {
            "schema": "echoes.render-request.v1",
            "jobId": "diffusers-fail-closed-test",
            "task": {
                "id": "task-1",
                "shotId": "shot-1",
                "durationSeconds": 2.0,
                "seed": 42,
                "prompt": "A cinematic test shot",
                "outputFile": "clips/shot-1.mp4",
            },
        }
        status, content_type, body = request("/v1/render", body=render_request)
        assert status == 503
        assert content_type == "application/json"
        failure = json.loads(body.decode("utf-8"))
        assert failure["status"] == "FAILED"
        assert "no verified real model loaded" in failure["error"]
        assert failure["health"]["realModelLoaded"] is False

        print("DiffusersProviderFailClosed PASS")
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


if __name__ == "__main__":
    raise SystemExit(main())
