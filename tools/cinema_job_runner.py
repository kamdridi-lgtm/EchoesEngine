#!/usr/bin/env python3
"""Run one complete Echoes Cinema job with machine-readable stage evidence.

The runner creates a render manifest, invokes an explicit backend, assembles the
returned clips, optionally muxes source audio, validates QC, and writes
``echoes.cinema-job-result.v1``. It does not use a shell, does not place provider
tokens in command arguments, and defaults HTTP jobs to the real-model gate.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_stage(name: str, command: list[str], log_path: Path) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    elapsed = time.monotonic() - started
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "$ " + " ".join(command) + "\n\nSTDOUT\n" + completed.stdout + "\nSTDERR\n" + completed.stderr,
        encoding="utf-8",
    )
    stage = {
        "name": name,
        "status": "PASS" if completed.returncode == 0 else "FAILED",
        "returnCode": completed.returncode,
        "durationSeconds": round(elapsed, 3),
        "logFile": str(log_path),
    }
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        stage["error"] = detail[-4000:] if detail else f"{name} exited with {completed.returncode}"
    return stage


def require_pass(stage: dict[str, Any]) -> None:
    if stage.get("status") != "PASS":
        raise RuntimeError(str(stage.get("error") or f"stage failed: {stage.get('name')}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sections_csv", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument(
        "--manifest-cli",
        type=Path,
        default=None,
        help="Optional native RenderManifestCli. When omitted, the compiler-free Python generator is used.",
    )
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--backend", choices=("synthetic", "http"), required=True)
    parser.add_argument("--audio", type=Path, default=None)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--fps", type=int, default=24)
    parser.add_argument("--provider-timeout", type=float, default=180.0)
    parser.add_argument(
        "--allow-unverified-provider",
        action="store_true",
        help="HTTP contract/testing only: skip the real-model requirement.",
    )
    args = parser.parse_args()

    root = args.output_root.resolve()
    result_path = root / "job-result.json"
    manifest_path = root / "render-manifest.json"
    render_root = root / "render-output"
    render_state_path = root / "render-state.json"
    final_mp4 = root / f"{args.job_id}.mp4"
    qc_path = root / "video-qc.json"
    script_root = Path(__file__).resolve().parent

    result: dict[str, Any] = {
        "schema": "echoes.cinema-job-result.v1",
        "jobId": args.job_id,
        "status": "RUNNING",
        "backendRequested": args.backend,
        "backendStatus": "MISSING",
        "audioStatus": "MISSING" if args.audio is None else "PENDING",
        "stages": [],
        "artifacts": {
            "manifest": str(manifest_path),
            "renderState": str(render_state_path),
            "videoQc": str(qc_path),
            "finalMp4": str(final_mp4),
            "sourceAudio": str(args.audio.resolve()) if args.audio is not None else None,
        },
    }

    try:
        if not JOB_ID_PATTERN.fullmatch(args.job_id):
            raise ValueError("job id must contain only letters, digits, dot, underscore, or hyphen")
        if args.seed < 0 or args.seed > 0xFFFFFFFF:
            raise ValueError("seed must fit in an unsigned 32-bit integer")
        if args.width <= 0 or args.height <= 0 or args.fps <= 0:
            raise ValueError("width, height, and fps must be positive")
        if args.provider_timeout <= 0:
            raise ValueError("provider timeout must be positive")
        if not args.sections_csv.is_file():
            raise FileNotFoundError(f"sections CSV not found: {args.sections_csv}")
        if args.manifest_cli is not None and not args.manifest_cli.is_file():
            raise FileNotFoundError(f"RenderManifestCli not found: {args.manifest_cli}")
        if args.audio is not None and (not args.audio.is_file() or args.audio.stat().st_size <= 0):
            raise FileNotFoundError(f"source audio not found or empty: {args.audio}")

        root.mkdir(parents=True, exist_ok=True)
        if args.manifest_cli is not None:
            manifest_command = [
                str(args.manifest_cli.resolve()),
                str(args.sections_csv.resolve()),
                str(manifest_path),
                args.job_id,
                str(args.seed),
                "clips",
            ]
            result["manifestGenerator"] = "native-render-manifest-cli"
        else:
            python_generator = script_root / "python_render_manifest.py"
            if not python_generator.is_file():
                raise FileNotFoundError(f"Python manifest generator not found: {python_generator}")
            manifest_command = [
                sys.executable,
                str(python_generator),
                str(args.sections_csv.resolve()),
                str(manifest_path),
                args.job_id,
                str(args.seed),
                "clips",
            ]
            result["manifestGenerator"] = "python-render-manifest-v1"

        manifest_stage = run_stage("manifest", manifest_command, root / "manifest.log")
        result["stages"].append(manifest_stage)
        require_pass(manifest_stage)

        if args.backend == "synthetic":
            render_command = [
                sys.executable,
                str(script_root / "synthetic_render_worker.py"),
                str(manifest_path),
                str(render_root),
                "--state",
                str(render_state_path),
                "--width",
                str(args.width),
                "--height",
                str(args.height),
                "--fps",
                str(args.fps),
            ]
        else:
            render_command = [
                sys.executable,
                str(script_root / "http_render_worker.py"),
                str(manifest_path),
                str(render_root),
                "--state",
                str(render_state_path),
                "--timeout",
                str(args.provider_timeout),
            ]
            if not args.allow_unverified_provider:
                render_command.append("--require-real-model")

        render_stage = run_stage("render", render_command, root / "render.log")
        result["stages"].append(render_stage)
        require_pass(render_stage)

        assemble_command = [
            sys.executable,
            str(script_root / "assemble_render.py"),
            str(render_state_path),
            str(render_root),
            str(final_mp4),
            "--qc",
            str(qc_path),
        ]
        if args.audio is not None:
            assemble_command.extend(["--audio", str(args.audio.resolve())])
        assemble_stage = run_stage("assemble", assemble_command, root / "assemble.log")
        result["stages"].append(assemble_stage)
        require_pass(assemble_stage)

        render_state = json.loads(render_state_path.read_text(encoding="utf-8"))
        qc = json.loads(qc_path.read_text(encoding="utf-8"))
        if render_state.get("status") != "PASS":
            raise RuntimeError("render state is not PASS after successful process exit")
        if qc.get("status") != "PASS":
            raise RuntimeError("video QC is not PASS after successful assembly")
        if not final_mp4.is_file() or final_mp4.stat().st_size <= 0:
            raise RuntimeError("final MP4 is missing or empty")

        probe = qc.get("probe") or {}
        audio_probe = probe.get("audio")
        if args.audio is not None and not isinstance(audio_probe, dict):
            raise RuntimeError("source audio was requested but final QC contains no audio stream")
        if args.audio is None and audio_probe is not None:
            raise RuntimeError("final QC unexpectedly contains audio without a source audio request")

        backend_name = str(render_state.get("backend") or "")
        real_model_loaded = bool((render_state.get("providerHealth") or {}).get("realModelLoaded"))
        result["backendUsed"] = backend_name
        result["backendStatus"] = "REAL" if backend_name == "http-provider" and real_model_loaded else "MOCK"
        result["audioStatus"] = "REAL" if audio_probe is not None else "MISSING"
        result["status"] = "PASS"
        result["taskCount"] = int(render_state.get("taskCount") or len(render_state.get("tasks") or []))
        result["durationSeconds"] = probe.get("durationSeconds")
        result["qc"] = probe
        result["sizeBytes"] = final_mp4.stat().st_size
        print(
            f"CinemaJobRunner PASS job={args.job_id} backend={backend_name} "
            f"classification={result['backendStatus']} audio={result['audioStatus']} output={final_mp4}"
        )
        return_code = 0
    except Exception as error:  # noqa: BLE001 - the result must retain the exact blocker
        result["status"] = "FAILED"
        result["error"] = str(error)
        print(f"CinemaJobRunner ERROR: {error}", file=sys.stderr)
        return_code = 1
    finally:
        write_json(result_path, result)

    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
