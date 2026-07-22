#!/usr/bin/env python3
"""Assemble rendered task clips into one MP4 and emit machine-readable QC."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
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
        raise ValueError(f"unsafe rendered clip path: {raw}")
    if candidate.suffix.lower() != ".mp4":
        raise ValueError(f"rendered clip path must end in .mp4: {raw}")
    return Path(*candidate.parts)


def probe(ffprobe: str, path: Path) -> dict[str, Any]:
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
        raise RuntimeError(f"unexpected final codec: {stream.get('codec_name')}")
    if duration <= 0.0:
        raise RuntimeError(f"invalid final duration: {duration}")
    return {
        "codec": stream.get("codec_name"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "pixelFormat": stream.get("pix_fmt"),
        "averageFrameRate": stream.get("avg_frame_rate"),
        "durationSeconds": duration,
        "sizeBytes": int(format_info.get("size", path.stat().st_size)),
    }


def ffconcat_escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("render_state", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("final_mp4", type=Path)
    parser.add_argument("--qc", type=Path, default=None)
    args = parser.parse_args()

    payload = json.loads(args.render_state.read_text(encoding="utf-8"))
    if payload.get("schema") != "echoes.render-state.v1":
        raise SystemExit("unsupported render state schema")
    if payload.get("status") != "PASS":
        raise SystemExit("render state is not PASS")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise SystemExit("render state contains no completed tasks")

    ffmpeg = require_tool("ffmpeg")
    ffprobe = require_tool("ffprobe")
    clips: list[Path] = []
    for index, task in enumerate(tasks):
        if task.get("status") != "PASS":
            raise SystemExit(f"render task {index} is not PASS")
        relative = safe_relative_path(str(task.get("outputFile", "")))
        clip = args.output_root / relative
        if not clip.is_file() or clip.stat().st_size <= 0:
            raise SystemExit(f"missing rendered clip: {clip}")
        clips.append(clip)

    args.final_mp4.parent.mkdir(parents=True, exist_ok=True)
    concat_path = args.final_mp4.with_suffix(".ffconcat")
    concat_lines = ["ffconcat version 1.0"]
    concat_lines.extend(f"file '{ffconcat_escape(clip)}'" for clip in clips)
    concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-an",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(args.final_mp4),
        ]
    )

    qc = {
        "schema": "echoes.video-qc.v1",
        "status": "PASS",
        "backend": payload.get("backend"),
        "jobId": payload.get("jobId"),
        "clipCount": len(clips),
        "outputFile": str(args.final_mp4),
        "probe": probe(ffprobe, args.final_mp4),
    }
    qc_path = args.qc or args.final_mp4.with_suffix(".qc.json")
    qc_path.write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    print(
        f"AssembleRender PASS clips={len(clips)} duration={qc['probe']['durationSeconds']:.3f} "
        f"output={args.final_mp4}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
