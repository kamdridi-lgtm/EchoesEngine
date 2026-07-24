#!/usr/bin/env python3
"""Stdlib-only authenticated provider health fixture for Windows stack smoke tests."""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Config:
    token = "ci-token"


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "EchoesMockHealthProvider/1.0"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"mock-health-provider {self.address_string()} {format_string % args}", flush=True)

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {Config.token}"

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self.authorized():
            self.send_json(401, {"status": "FAILED", "error": "unauthorized"})
            return
        if self.path != "/health":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        self.send_json(
            200,
            {
                "schema": "echoes.render-provider-health.v1",
                "status": "PASS",
                "backend": "mock-health-contract-provider",
                "realModelLoaded": False,
                "modelId": None,
                "modelRevision": None,
                "commercialUseAllowed": False,
                "license": None,
                "capabilities": {
                    "textToVideo": True,
                    "referenceImage": False,
                    "subjectIdentity": False,
                    "directMp4": False,
                },
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self.authorized():
            self.send_json(401, {"status": "FAILED", "error": "unauthorized"})
            return
        self.send_json(
            503,
            {
                "status": "FAILED",
                "error": "health-only contract provider cannot render",
            },
        )


def self_test() -> int:
    payload = json_bytes({"status": "PASS"})
    assert payload == b'{"status": "PASS"}'
    print("MockHealthProvider PASS stdlib-only authenticated-health=enabled")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--token", default=os.getenv("ECHOES_RENDER_TOKEN", "ci-token"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.port <= 0 or args.port > 65535:
        raise SystemExit("invalid provider port")
    if not args.token:
        raise SystemExit("provider token is required")

    Config.token = args.token
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MockHealthProvider READY http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
