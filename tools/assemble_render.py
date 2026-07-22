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


def probe(ffprobe: str, path: Path, require_audio: bool) -> dict[str, Any]:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,sample_rate,channels:format=duration,size",
            "-of",
            "json",
            str(path),
        ]
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams") or []
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise RuntimeError(f"ffprobe found no video stream: {path}")
    format_info = payload.get("format") or {}
    duration = float(format_info.get("duration", 0.0))
    if video_stream.get("codec_name") != "h264":
        raise RuntimeError(f"unexpected final codec: {video_stream.get('codec_name')}")
    if video_stream.get("pix_fmt") != "yuv420p":
        raise RuntimeError(f"unexpected final pixel format: {video_stream.get('pix_fmt')}")
    if duration <= 0.0:
        raise RuntimeError(f"invalid final duration: {duration}")
    if require_audio and audio_stream is None:
        raise RuntimeError("final MP4 is missing the required audio stream")

    audio = None
    if audio_stream is not None:
        audio = {
            "codec": audio_stream.get("codec_name"),
            "sampleRate": int(audio_stream.get("sample_rate", 0)),
            "channels": int(audio_stream.get("channels", 0)),
        }
        if audio["codec"] != "aac":
            raise RuntimeError(f"unexpected final audio codec: {audio['codec']}")
        if audio["sampleRate"] <= 0 or audio["channels"] <= 0:
            raise RuntimeError("final audio stream has invalid sample rate or channel count")

    return {
        "codec": video_stream.get("codec_name"),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "pixelFormat": video_stream.get("pix_fmt"),
        "averageFrameRate": video_stream.get("avg_frame_rate"),
        "durationSeconds": duration,
        "sizeBytes": int(format_info.get("size", path.stat().st_size)),
        "audio": audio,
    }


def ffconcat_escape(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("render_state", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("final_mp4", type=Path)
    parser.add_argument("--audio", type=Path, default=None)
    parser.add_argument("--audio-bitrate", default="192k")
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
    if args.audio is not None and (not args.audio.is_file() or args.audio.stat().st_size <= 0):
        raise SystemExit(f"audio source is missing or empty: {args.audio}")

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

    command = [
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
    ]
    if args.audio is None:
        command.extend(["-an", "-c:v", "copy"])
    else:
        command.extend(
            [
                "-i",
                str(args.audio),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                args.audio_bitrate,
                "-shortest",
            ]
        )
    command.extend(["-movflags", "+faststart", str(args.final_mp4)])
    run(command)

    qc = {
        "schema": "echoes.video-qc.v1",
        "status": "PASS",
        "backend": payload.get("backend"),
        "jobId": payload.get("jobId"),
        "clipCount": len(clips),
        "outputFile": str(args.final_mp4),
        "audioSource": str(args.audio) if args.audio is not None else None,
        "probe": probe(ffprobe, args.final_mp4, require_audio=args.audio is not None),
    }
    qc_path = args.qc or args.final_mp4.with_suffix(".qc.json")
    qc_path.write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    audio_label = qc["probe"]["audio"]["codec"] if qc["probe"]["audio"] else "none"
    print(
        f"AssembleRender PASS clips={len(clips)} duration={qc['probe']['durationSeconds']:.3f} "
        f"audio={audio_label} output={args.final_mp4}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
