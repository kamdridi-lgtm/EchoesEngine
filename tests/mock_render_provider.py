#!/usr/bin/env python3
"""Local HTTP render provider used only to verify the network backend contract."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class ProviderConfig:
    token = "ci-token"
    width = 320
    height = 180
    fps = 12
    ffmpeg = "ffmpeg"


def render_mp4(task: dict[str, Any]) -> bytes:
    duration = float(task.get("durationSeconds", 0.0))
    if duration <= 0.0:
        raise ValueError("task duration must be positive")
    seed = int(task.get("seed", 0))
    hue = seed % 360
    with tempfile.TemporaryDirectory(prefix="echoes-provider-") as temp_dir:
        output = Path(temp_dir) / "clip.mp4"
        command = [
            ProviderConfig.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={ProviderConfig.width}x{ProviderConfig.height}:rate={ProviderConfig.fps}",
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"hue=h={hue},format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
        subprocess.run(command, check=True, capture_output=True)
        return output.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "EchoesMockRenderProvider/1.0"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"provider {self.address_string()} {format_string % args}", flush=True)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {ProviderConfig.token}"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self.send_json(
                200,
                {
                    "schema": "echoes.render-provider-health.v1",
                    "status": "PASS",
                    "backend": "mock-contract-provider",
                },
            )
            return
        self.send_json(404, {"status": "FAILED", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/v1/render":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        if not self.authorized():
            self.send_json(401, {"status": "FAILED", "error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if payload.get("schema") != "echoes.render-request.v1":
                raise ValueError("unsupported request schema")
            task = payload.get("task")
            if not isinstance(task, dict):
                raise ValueError("request task must be an object")
            clip = render_mp4(task)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(clip)))
            self.send_header("X-Echoes-Task-Id", str(task.get("id", "")))
            self.end_headers()
            self.wfile.write(clip)
        except Exception as error:  # noqa: BLE001 - fixture must return exact contract failure
            self.send_json(400, {"status": "FAILED", "error": str(error)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--token", default=os.getenv("ECHOES_RENDER_TOKEN", "ci-token"))
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=180)
    parser.add_argument("--fps", type=int, default=12)
    args = parser.parse_args()

    resolved_ffmpeg = shutil.which("ffmpeg")
    if not resolved_ffmpeg:
        raise SystemExit("ffmpeg is required")
    if args.port <= 0 or args.width <= 0 or args.height <= 0 or args.fps <= 0:
        raise SystemExit("invalid provider dimensions, fps, or port")

    ProviderConfig.token = args.token
    ProviderConfig.width = args.width
    ProviderConfig.height = args.height
    ProviderConfig.fps = args.fps
    ProviderConfig.ffmpeg = resolved_ffmpeg

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MockRenderProvider READY http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
