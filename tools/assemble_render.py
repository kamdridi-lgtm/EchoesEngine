#!/usr/bin/env python3
"""Assemble rendered task clips into one MP4 and emit machine-readable QC."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any


MAX_AV_DRIFT_SECONDS = 0.35
DEFAULT_MEDIA_TOOL_WAIT_SECONDS = 900.0


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def media_tool_wait_seconds() -> float:
    raw = os.environ.get("ECHOES_CINEMA_MEDIA_TOOL_WAIT_SECONDS", "")
    try:
        return max(0.0, float(raw)) if raw else DEFAULT_MEDIA_TOOL_WAIT_SECONDS
    except ValueError:
        return DEFAULT_MEDIA_TOOL_WAIT_SECONDS


def require_tool(name: str) -> str:
    """Wait for the nonblocking D-drive media worker instead of failing early."""

    timeout = media_tool_wait_seconds()
    deadline = time.monotonic() + timeout
    runtime_root_raw = os.environ.get("ECHOES_CINEMA_RUNTIME_ROOT", "").strip()
    status_path = Path(runtime_root_raw) / "ffmpeg-worker-status.json" if runtime_root_raw else None
    last_status: dict[str, Any] = {}

    while True:
        resolved = shutil.which(name)
        if resolved:
            return resolved

        if status_path is not None:
            last_status = read_json(status_path)
            if str(last_status.get("status") or "").upper() == "BLOCKED":
                detail = str(last_status.get("error") or last_status.get("operatorAction") or "unknown integrity blocker")
                raise RuntimeError(f"required executable {name} is blocked by pinned FFmpeg provisioning: {detail}")

        if time.monotonic() >= deadline:
            state = str(last_status.get("status") or "MISSING")
            detail = str(last_status.get("error") or last_status.get("operatorAction") or "no worker status")
            raise RuntimeError(
                f"required executable not found in PATH after {int(timeout)} seconds: {name}; "
                f"FFmpeg worker status={state}; detail={detail}"
            )
        time.sleep(2.0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative_path(raw: str) -> Path:
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe rendered clip path: {raw}")
    if candidate.suffix.lower() != ".mp4":
        raise ValueError(f"rendered clip path must end in .mp4: {raw}")
    return Path(*candidate.parts)


def parse_duration(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0.0 else fallback


def probe(ffprobe: str, path: Path, require_audio: bool) -> dict[str, Any]:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,avg_frame_rate,sample_rate,channels,duration:format=duration,size",
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

    video_duration = parse_duration(video_stream.get("duration"), duration)
    audio = None
    av_drift = None
    if audio_stream is not None:
        audio_duration = parse_duration(audio_stream.get("duration"), duration)
        audio = {
            "codec": audio_stream.get("codec_name"),
            "sampleRate": int(audio_stream.get("sample_rate", 0)),
            "channels": int(audio_stream.get("channels", 0)),
            "durationSeconds": audio_duration,
        }
        if audio["codec"] != "aac":
            raise RuntimeError(f"unexpected final audio codec: {audio['codec']}")
        if audio["sampleRate"] <= 0 or audio["channels"] <= 0:
            raise RuntimeError("final audio stream has invalid sample rate or channel count")
        av_drift = abs(video_duration - audio_duration)
        if require_audio and av_drift > MAX_AV_DRIFT_SECONDS:
            raise RuntimeError(
                f"audio/video drift exceeds {MAX_AV_DRIFT_SECONDS:.2f}s: {av_drift:.3f}s"
            )

    return {
        "codec": video_stream.get("codec_name"),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "pixelFormat": video_stream.get("pix_fmt"),
        "averageFrameRate": video_stream.get("avg_frame_rate"),
        "durationSeconds": duration,
        "videoDurationSeconds": video_duration,
        "sizeBytes": int(format_info.get("size", path.stat().st_size)),
        "sha256": sha256_file(path),
        "audio": audio,
        "avDriftSeconds": av_drift,
        "maxAllowedAvDriftSeconds": MAX_AV_DRIFT_SECONDS if audio is not None else None,
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
        expected_sha = str(task.get("sha256") or "")
        if expected_sha and sha256_file(clip) != expected_sha:
            raise SystemExit(f"rendered clip SHA-256 mismatch: {clip}")
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
    try:
        run(command)
    finally:
        concat_path.unlink(missing_ok=True)

    final_probe = probe(ffprobe, args.final_mp4, require_audio=args.audio is not None)
    qc = {
        "schema": "echoes.video-qc.v1",
        "status": "PASS",
        "backend": payload.get("backend"),
        "jobId": payload.get("jobId"),
        "clipCount": len(clips),
        "outputFile": str(args.final_mp4),
        "audioSource": str(args.audio) if args.audio is not None else None,
        "probe": final_probe,
    }
    qc_path = args.qc or args.final_mp4.with_suffix(".qc.json")
    qc_path.write_text(json.dumps(qc, indent=2) + "\n", encoding="utf-8")
    audio_label = final_probe["audio"]["codec"] if final_probe["audio"] else "none"
    drift_label = f"{final_probe['avDriftSeconds']:.3f}s" if final_probe["avDriftSeconds"] is not None else "n/a"
    print(
        f"AssembleRender PASS clips={len(clips)} duration={final_probe['durationSeconds']:.3f} "
        f"audio={audio_label} avDrift={drift_label} sha256={final_probe['sha256']} output={args.final_mp4}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
