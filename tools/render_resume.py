#!/usr/bin/env python3
"""Build a fail-closed resume plan from an existing Echoes render state.

A task is reusable only when its prior state is PASS, its MP4 is inside the
configured output root, its SHA-256 still matches, and ffprobe confirms H.264,
yuv420p, and a positive duration. Everything else is scheduled again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(raw: str) -> Path:
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe output path: {raw}")
    if candidate.suffix.lower() != ".mp4":
        raise ValueError(f"resume candidate must be MP4: {raw}")
    return Path(*candidate.parts)


def probe_mp4(ffprobe: str, path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt,width,height:format=duration,size",
            "-of",
            "json",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout).strip() or "ffprobe failed")
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError("no video stream")
    stream = streams[0]
    fmt = payload.get("format") or {}
    duration = float(fmt.get("duration", 0.0))
    if stream.get("codec_name") != "h264":
        raise RuntimeError("codec is not H.264")
    if stream.get("pix_fmt") != "yuv420p":
        raise RuntimeError("pixel format is not yuv420p")
    if duration <= 0.0:
        raise RuntimeError("duration is not positive")
    return {
        "codec": stream.get("codec_name"),
        "pixelFormat": stream.get("pix_fmt"),
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "durationSeconds": duration,
        "sizeBytes": int(fmt.get("size", path.stat().st_size)),
    }


def build_plan(manifest: dict[str, Any], state: dict[str, Any], output_root: Path, ffprobe: str) -> dict[str, Any]:
    tasks = manifest.get("tasks")
    if manifest.get("schema") != "echoes.render-manifest.v1" or not isinstance(tasks, list):
        raise ValueError("unsupported or empty render manifest")

    previous_by_id: dict[str, dict[str, Any]] = {}
    if state.get("schema") == "echoes.render-state.v1" and isinstance(state.get("tasks"), list):
        for item in state["tasks"]:
            if isinstance(item, dict) and item.get("taskId"):
                previous_by_id[str(item["taskId"])] = item

    reusable: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            pending.append({"taskId": None, "reason": "manifest task is not an object"})
            continue
        task_id = str(task.get("id", ""))
        relative = safe_relative(str(task.get("outputFile", "")))
        candidate = (output_root / relative).resolve()
        try:
            candidate.relative_to(output_root.resolve())
        except ValueError:
            pending.append({"taskId": task_id, "reason": "output path escapes root"})
            continue

        previous = previous_by_id.get(task_id)
        if not previous or previous.get("status") != "PASS":
            pending.append({"taskId": task_id, "outputFile": relative.as_posix(), "reason": "no prior PASS evidence"})
            continue
        if not candidate.is_file() or candidate.stat().st_size <= 0:
            pending.append({"taskId": task_id, "outputFile": relative.as_posix(), "reason": "prior MP4 missing or empty"})
            continue
        expected_hash = str(previous.get("sha256", ""))
        actual_hash = sha256_file(candidate)
        if not expected_hash or actual_hash.lower() != expected_hash.lower():
            pending.append({"taskId": task_id, "outputFile": relative.as_posix(), "reason": "SHA-256 evidence missing or changed"})
            continue
        try:
            qc = probe_mp4(ffprobe, candidate)
        except Exception as error:  # noqa: BLE001 - exact blocker belongs in plan
            pending.append({"taskId": task_id, "outputFile": relative.as_posix(), "reason": f"media QC failed: {error}"})
            continue
        reusable.append(
            {
                "taskId": task_id,
                "outputFile": relative.as_posix(),
                "sha256": actual_hash,
                "qc": qc,
            }
        )

    return {
        "schema": "echoes.render-resume-plan.v1",
        "status": "PASS",
        "jobId": manifest.get("jobId"),
        "classification": "EVIDENCE_VALIDATED",
        "reusableTasks": reusable,
        "pendingTasks": pending,
        "reusableCount": len(reusable),
        "pendingCount": len(pending),
        "complete": len(pending) == 0,
    }


def self_test() -> int:
    assert safe_relative("clips/a.mp4") == Path("clips/a.mp4")
    try:
        safe_relative("../escape.mp4")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal was not rejected")

    with tempfile.TemporaryDirectory(prefix="echoes-resume-self-test-") as temp_dir:
        sample = Path(temp_dir) / "sample.bin"
        sample.write_bytes(b"echoes-resume")
        assert len(sha256_file(sample)) == 64
    print("RenderResume PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path, nargs="?")
    parser.add_argument("output_root", type=Path, nargs="?")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.manifest is None or args.output_root is None:
        raise SystemExit("manifest and output_root are required")
    if not args.manifest.is_file():
        raise SystemExit(f"manifest not found: {args.manifest}")

    state_path = args.state or (args.output_root / "render-state.json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit("ffprobe is required")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    plan = build_plan(manifest, state, args.output_root, ffprobe)
    plan_path = args.plan or (args.output_root / "resume-plan.json")
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"RenderResume PASS reusable={plan['reusableCount']} pending={plan['pendingCount']} plan={plan_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
