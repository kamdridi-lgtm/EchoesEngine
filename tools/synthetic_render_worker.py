#!/usr/bin/env python3
"""Generate real H.264 MP4 proof clips from an Echoes render manifest.

This backend is intentionally synthetic and CI-only. It proves manifest execution,
file production, encoding, and QC. It is not an AI video generator.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


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


def validate_continuity(task: dict[str, Any]) -> dict[str, Any]:
    continuity = task.get("continuity") or {}
    if not isinstance(continuity, dict):
        raise ValueError(f"task continuity must be an object: {task.get('id')}")
    strength = float(continuity.get("strength", 0.85))
    if strength < 0.0 or strength > 1.0:
        raise ValueError(f"task continuity strength must be 0..1: {task.get('id')}")
    return {
        "subjectId": str(continuity.get("subjectId", "")),
        "styleId": str(continuity.get("styleId", "")),
        "referenceAsset": str(continuity.get("referenceAsset", "")),
        "strength": strength,
    }


def probe_clip(ffprobe: str, path: Path) -> dict[str, Any]:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt:format=duration,size",
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
        raise RuntimeError(f"unexpected codec for {path}: {stream.get('codec_name')}")
    if duration <= 0.0:
        raise RuntimeError(f"invalid duration for {path}: {duration}")
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "pixelFormat": stream.get("pix_fmt"),
        "durationSeconds": duration,
        "sizeBytes": int(format_info.get("size", path.stat().st_size)),
    }


def render_task(
    ffmpeg: str,
    ffprobe: str,
    task: dict[str, Any],
    output_root: Path,
    width: int,
    height: int,
    fps: int,
) -> dict[str, Any]:
    duration = float(task.get("durationSeconds", 0.0))
    if duration <= 0.0:
        raise ValueError(f"task has invalid duration: {task.get('id')}")
    seed = int(task.get("seed", 0))
    continuity = validate_continuity(task)
    relative_output = safe_relative_path(str(task.get("outputFile", "")))
    output_path = output_root / relative_output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    hue = seed % 360
    source = f"testsrc2=size={width}x{height}:rate={fps}"
    filters = f"hue=h={hue},format=yuv420p"
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            f"{duration:.3f}",
            "-vf",
            filters,
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
            str(output_path),
        ]
    )

    qc = probe_clip(ffprobe, output_path)
    return {
        "taskId": task.get("id"),
        "shotId": task.get("shotId"),
        "status": "PASS",
        "backend": "synthetic-ci",
        "outputFile": relative_output.as_posix(),
        "seed": seed,
        "continuity": continuity,
        "qc": qc,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=24)
    args = parser.parse_args()

    state_path = args.state or (args.output_root / "render-state.json")
    state: dict[str, Any] = {
        "schema": "echoes.render-state.v1",
        "backend": "synthetic-ci",
        "status": "RUNNING",
        "manifest": str(args.manifest),
        "tasks": [],
    }

    try:
        if args.width <= 0 or args.height <= 0 or args.fps <= 0:
            raise ValueError("width, height, and fps must be positive")
        payload = json.loads(args.manifest.read_text(encoding="utf-8"))
        if payload.get("schema") != "echoes.render-manifest.v1":
            raise ValueError("unsupported render manifest schema")
        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("render manifest contains no tasks")

        ffmpeg = require_tool("ffmpeg")
        ffprobe = require_tool("ffprobe")
        args.output_root.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            if not isinstance(task, dict):
                raise ValueError("render manifest task must be an object")
            state["tasks"].append(
                render_task(ffmpeg, ffprobe, task, args.output_root, args.width, args.height, args.fps)
            )

        state["status"] = "PASS"
        state["jobId"] = payload.get("jobId")
        state["taskCount"] = len(state["tasks"])
        state["durationSeconds"] = payload.get("durationSeconds")
        print(f"SyntheticRenderWorker PASS tasks={len(state['tasks'])} output={args.output_root}")
        return_code = 0
    except Exception as error:  # noqa: BLE001 - state file must capture exact failure
        state["status"] = "FAILED"
        state["error"] = str(error)
        print(f"SyntheticRenderWorker ERROR: {error}", file=sys.stderr)
        return_code = 1
    finally:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
