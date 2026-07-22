#!/usr/bin/env python3
"""Execute an Echoes render manifest against an authenticated HTTP provider.

The worker supports two provider response modes:
- direct ``video/mp4`` response;
- JSON response containing ``status=PASS`` and an ``outputUrl`` to download.

It never executes provider-supplied commands. Output paths are constrained to the
selected output root, provider hosts are allowlisted, and every clip is verified
with ffprobe before the render state can become PASS.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any


MAX_RESPONSE_BYTES = 250 * 1024 * 1024


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def require_tool(name: str) -> str:
    resolved = shutil.which(name)
    if not resolved:
        raise RuntimeError(f"required executable not found in PATH: {name}")
    return resolved


def safe_relative_path(raw: str) -> Path:
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe outputFile path: {raw}")
    if candidate.suffix.lower() != ".mp4":
        raise ValueError(f"render task outputFile must end in .mp4: {raw}")
    return Path(*candidate.parts)


def parse_allowlist(raw: str) -> set[str]:
    hosts = {item.strip().lower() for item in raw.split(",") if item.strip()}
    if not hosts:
        raise ValueError("render provider host allowlist is empty")
    return hosts


def validate_url(raw: str, allowed_hosts: set[str]) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported provider URL scheme: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if host not in allowed_hosts:
        raise ValueError(f"provider host is not allowlisted: {host}")
    if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("plain HTTP is allowed only for local providers")
    return parsed


def default_health_url(endpoint: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))


def read_limited(response: Any) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > MAX_RESPONSE_BYTES:
        raise RuntimeError("provider response exceeds maximum allowed size")
    payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError("provider response exceeds maximum allowed size")
    return payload


def request(
    url: str,
    *,
    method: str,
    token: str,
    allowed_hosts: set[str],
    timeout_seconds: float,
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[str, bytes]:
    validate_url(url, allowed_hosts)
    headers = {
        "Accept": "video/mp4, application/json",
        "User-Agent": "EchoesEngine-HttpRenderWorker/1.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            return response.headers.get_content_type(), read_limited(response)
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"provider HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"provider connection failed: {error.reason}") from error


def provider_health(
    health_url: str,
    *,
    token: str,
    allowed_hosts: set[str],
    timeout_seconds: float,
    require_real_model: bool,
) -> dict[str, Any]:
    content_type, body = request(
        health_url,
        method="GET",
        token=token,
        allowed_hosts=allowed_hosts,
        timeout_seconds=timeout_seconds,
    )
    if content_type != "application/json":
        raise RuntimeError(f"provider health did not return JSON: {content_type}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("provider health returned invalid JSON") from error
    if payload.get("schema") != "echoes.render-provider-health.v1":
        raise RuntimeError("provider health schema is unsupported")
    if payload.get("status") != "PASS":
        raise RuntimeError(f"provider health is not PASS: {payload}")
    real_model_loaded = payload.get("realModelLoaded") is True
    if require_real_model and not real_model_loaded:
        raise RuntimeError("provider has no verified real model loaded")
    return payload


def probe_clip(ffprobe: str, path: Path) -> dict[str, Any]:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,avg_frame_rate:format=duration,size",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(f"ffprobe found no video stream: {path}")
    stream = streams[0]
    format_info = payload.get("format") or {}
    duration = float(format_info.get("duration", 0.0))
    if stream.get("codec_name") != "h264":
        raise RuntimeError(f"provider clip must be H.264: {path}")
    if stream.get("pix_fmt") != "yuv420p":
        raise RuntimeError(f"provider clip must use yuv420p: {path}")
    if duration <= 0.0:
        raise RuntimeError(f"provider clip has invalid duration: {path}")
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "pixelFormat": stream.get("pix_fmt"),
        "averageFrameRate": stream.get("avg_frame_rate"),
        "durationSeconds": duration,
        "sizeBytes": int(format_info.get("size", path.stat().st_size)),
    }


def provider_payload(manifest: dict[str, Any], task: dict[str, Any]) -> bytes:
    request_body = {
        "schema": "echoes.render-request.v1",
        "jobId": manifest.get("jobId"),
        "task": task,
    }
    return json.dumps(request_body, ensure_ascii=False).encode("utf-8")


def render_task(
    *,
    manifest: dict[str, Any],
    task: dict[str, Any],
    endpoint: str,
    token: str,
    allowed_hosts: set[str],
    output_root: Path,
    timeout_seconds: float,
    ffprobe: str,
) -> dict[str, Any]:
    relative_output = safe_relative_path(str(task.get("outputFile", "")))
    output_path = output_root / relative_output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content_type, response_body = request(
        endpoint,
        method="POST",
        token=token,
        allowed_hosts=allowed_hosts,
        timeout_seconds=timeout_seconds,
        body=provider_payload(manifest, task),
        content_type="application/json",
    )

    provider_mode: str
    if content_type in {"video/mp4", "application/octet-stream"}:
        output_path.write_bytes(response_body)
        provider_mode = "direct-mp4"
    else:
        try:
            response_json = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"provider returned unsupported content type: {content_type}") from error
        if response_json.get("status") != "PASS":
            raise RuntimeError(f"provider did not return PASS: {response_json}")
        output_url = str(response_json.get("outputUrl", ""))
        if not output_url:
            raise RuntimeError("provider PASS response has no outputUrl")
        download_type, download_body = request(
            output_url,
            method="GET",
            token=token,
            allowed_hosts=allowed_hosts,
            timeout_seconds=timeout_seconds,
        )
        if download_type not in {"video/mp4", "application/octet-stream"}:
            raise RuntimeError(f"provider outputUrl did not return MP4: {download_type}")
        output_path.write_bytes(download_body)
        provider_mode = "json-output-url"

    if output_path.stat().st_size <= 0:
        raise RuntimeError(f"provider wrote an empty clip: {output_path}")
    qc = probe_clip(ffprobe, output_path)
    return {
        "taskId": task.get("id"),
        "shotId": task.get("shotId"),
        "status": "PASS",
        "backend": "http-provider",
        "providerMode": provider_mode,
        "outputFile": relative_output.as_posix(),
        "seed": task.get("seed"),
        "qc": qc,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--endpoint", default=os.getenv("ECHOES_RENDER_ENDPOINT", ""))
    parser.add_argument("--health-url", default=os.getenv("ECHOES_RENDER_HEALTH_URL", ""))
    parser.add_argument("--token-env", default="ECHOES_RENDER_TOKEN")
    parser.add_argument("--allow-hosts", default=os.getenv("ECHOES_RENDER_HOST_ALLOWLIST", "127.0.0.1,localhost"))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--require-real-model", action="store_true")
    parser.add_argument("--state", type=Path, default=None)
    args = parser.parse_args()

    state_path = args.state or (args.output_root / "render-state.json")
    state: dict[str, Any] = {
        "schema": "echoes.render-state.v1",
        "backend": "http-provider",
        "status": "RUNNING",
        "tasks": [],
    }

    try:
        if not args.endpoint:
            raise ValueError("render provider endpoint is required")
        if args.timeout <= 0:
            raise ValueError("timeout must be positive")
        allowed_hosts = parse_allowlist(args.allow_hosts)
        validate_url(args.endpoint, allowed_hosts)
        health_url = args.health_url or default_health_url(args.endpoint)
        validate_url(health_url, allowed_hosts)
        token = os.getenv(args.token_env, "")
        health = provider_health(
            health_url,
            token=token,
            allowed_hosts=allowed_hosts,
            timeout_seconds=args.timeout,
            require_real_model=args.require_real_model,
        )

        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if manifest.get("schema") != "echoes.render-manifest.v1":
            raise ValueError("unsupported render manifest schema")
        tasks = manifest.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("render manifest contains no tasks")

        ffprobe = require_tool("ffprobe")
        args.output_root.mkdir(parents=True, exist_ok=True)
        state["jobId"] = manifest.get("jobId")
        state["providerEndpoint"] = args.endpoint
        state["providerHealthUrl"] = health_url
        state["providerHealth"] = health
        state["providerProtocol"] = "echoes.render-request.v1"
        state["realModelRequired"] = args.require_real_model
        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError("render manifest task must be an object")
            state["tasks"].append(
                render_task(
                    manifest=manifest,
                    task=task,
                    endpoint=args.endpoint,
                    token=token,
                    allowed_hosts=allowed_hosts,
                    output_root=args.output_root,
                    timeout_seconds=args.timeout,
                    ffprobe=ffprobe,
                )
            )

        state["status"] = "PASS"
        state["taskCount"] = len(state["tasks"])
        state["durationSeconds"] = manifest.get("durationSeconds")
        print(f"HttpRenderWorker PASS tasks={len(state['tasks'])} endpoint={args.endpoint}")
        return_code = 0
    except Exception as error:  # noqa: BLE001 - exact failure belongs in state
        state["status"] = "FAILED"
        state["error"] = str(error)
        print(f"HttpRenderWorker ERROR: {error}", file=sys.stderr)
        return_code = 1
    finally:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
