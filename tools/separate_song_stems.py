#!/usr/bin/env python3
"""Run HTDemucs separation with CUDA preference, CPU fallback and evidence manifests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_STEMS = ("vocals", "drums", "bass", "other")
MODEL_FILENAME = "955717e8-8726e21a.th"
MODEL_SHA256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_audio(ffprobe: str, path: Path) -> dict[str, Any]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,channels,bits_per_sample,duration:format=duration",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {completed.stderr.strip()}")
    payload = json.loads(completed.stdout)
    streams = payload.get("streams") or []
    if not streams:
        raise RuntimeError(f"No audio stream found in {path}")
    stream = streams[0]
    duration_text = stream.get("duration") or (payload.get("format") or {}).get("duration")
    return {
        "sampleRate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "bitsPerSample": int(stream.get("bits_per_sample") or 0),
        "durationSeconds": round(float(duration_text or 0.0), 6),
    }


def torch_inventory(python: Path) -> dict[str, Any]:
    code = (
        "import json,torch,torchaudio;"
        "print(json.dumps(dict(torch=torch.__version__,torchaudio=torchaudio.__version__,"
        "cudaAvailable=torch.cuda.is_available(),cudaRuntime=torch.version.cuda,"
        "deviceCount=torch.cuda.device_count(),"
        "deviceName=(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None))))"
    )
    completed = subprocess.run([str(python), "-c", code], capture_output=True, text=True, encoding="utf-8")
    if completed.returncode != 0:
        raise RuntimeError(f"Unable to inspect PyTorch runtime: {completed.stderr.strip()}")
    return json.loads(completed.stdout)


def run_demucs(
    python: Path,
    source: Path,
    staging: Path,
    model_repo: Path,
    device: str,
    segment: int,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    command = [
        str(python),
        "-m",
        "demucs.separate",
        "--repo",
        str(model_repo),
        "-n",
        "htdemucs",
        "--device",
        device,
        "--segment",
        str(segment),
        "--shifts",
        "1",
        "--overlap",
        "0.25",
        "--int24",
        "--filename",
        "{stem}.{ext}",
        "--out",
        str(staging),
        str(source),
    ]
    environment = os.environ.copy()
    environment.setdefault("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:128")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
    )
    return completed, command


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-input-sha256", required=True)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--segment", type=int, default=6)
    parser.add_argument("--declare-user-song", action="store_true")
    args = parser.parse_args()

    source = args.input.resolve()
    runtime = args.runtime_root.resolve()
    output = args.output_dir.resolve()
    if not source.is_file():
        raise RuntimeError(f"Input audio is missing: {source}")

    source_sha = sha256_file(source)
    if source_sha != args.expected_input_sha256.lower():
        raise RuntimeError(f"INPUT_SHA256_MISMATCH expected={args.expected_input_sha256} actual={source_sha}")

    python = runtime / ".venv" / "Scripts" / "python.exe"
    model_repo = runtime / "models"
    model_path = model_repo / MODEL_FILENAME
    if not python.is_file() or not model_path.is_file() or not (model_repo / "htdemucs.yaml").is_file():
        raise RuntimeError("Stem runtime is incomplete")
    model_sha = sha256_file(model_path)
    if model_sha != MODEL_SHA256:
        raise RuntimeError(f"MODEL_SHA256_MISMATCH expected={MODEL_SHA256} actual={model_sha}")

    output.mkdir(parents=True, exist_ok=True)
    final_stems = output / "stems"
    if final_stems.exists():
        raise RuntimeError(f"Stem output already exists and will not be overwritten: {final_stems}")
    staging = output / ".staging-demucs"
    if staging.exists():
        shutil.rmtree(staging)

    inventory = torch_inventory(python)
    requested_device = "cuda" if inventory.get("cudaAvailable") is True else "cpu"
    attempts: list[dict[str, Any]] = []
    used_device: str | None = None
    demucs_output: Path | None = None

    for device in ([requested_device, "cpu"] if requested_device == "cuda" else ["cpu"]):
        if staging.exists():
            shutil.rmtree(staging)
        completed, command = run_demucs(python, source, staging, model_repo, device, args.segment)
        attempt = {
            "device": device,
            "command": command,
            "exitCode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        attempts.append(attempt)
        candidate = staging / "htdemucs"
        if completed.returncode == 0 and candidate.is_dir():
            used_device = device
            demucs_output = candidate
            break
        if device == "cpu":
            break

    log_path = output / "stem-separation.log"
    log_path.write_text(
        "\n\n".join(
            f"=== DEVICE {item['device']} EXIT {item['exitCode']} ===\n{item['stdout']}\n{item['stderr']}"
            for item in attempts
        ),
        encoding="utf-8",
    )
    if used_device is None or demucs_output is None:
        raise RuntimeError(f"HTDemucs separation failed; see {log_path}")

    source_probe = probe_audio(args.ffprobe, source)
    final_stems.mkdir(parents=True, exist_ok=False)
    stem_records: list[dict[str, Any]] = []
    for stem_name in EXPECTED_STEMS:
        source_stem = demucs_output / f"{stem_name}.wav"
        if not source_stem.is_file() or source_stem.stat().st_size <= 44:
            raise RuntimeError(f"Expected stem is missing or empty: {source_stem}")
        destination = final_stems / f"{stem_name}.wav"
        shutil.move(str(source_stem), str(destination))
        evidence = probe_audio(args.ffprobe, destination)
        if evidence["sampleRate"] != 44100 or evidence["channels"] != 2:
            raise RuntimeError(f"Unexpected stem format for {destination}: {evidence}")
        if abs(evidence["durationSeconds"] - source_probe["durationSeconds"]) > 0.25:
            raise RuntimeError(f"Stem duration drift exceeded tolerance for {destination}: {evidence}")
        stem_records.append(
            {
                "name": stem_name,
                "path": str(destination),
                "sha256": sha256_file(destination),
                "sizeBytes": destination.stat().st_size,
                **evidence,
            }
        )

    shutil.rmtree(staging, ignore_errors=True)
    finished = utc_now()
    manifest = {
        "schema": "echoes.stem-separation-run.v1",
        "status": "PASS",
        "finishedAtUtc": finished,
        "source": {
            "path": str(source),
            "name": source.name,
            "sha256": source_sha,
            "sizeBytes": source.stat().st_size,
            **source_probe,
        },
        "runtime": {
            "root": str(runtime),
            "python": str(python),
            "torch": inventory,
        },
        "model": {
            "id": "htdemucs",
            "signature": "955717e8",
            "path": str(model_path),
            "sha256": model_sha,
            "sizeBytes": model_path.stat().st_size,
            "sources": list(EXPECTED_STEMS),
        },
        "execution": {
            "requestedDevice": requested_device,
            "usedDevice": used_device,
            "cpuFallbackUsed": requested_device == "cuda" and used_device == "cpu",
            "segmentSeconds": args.segment,
            "shifts": 1,
            "overlap": 0.25,
            "attempts": [
                {
                    "device": item["device"],
                    "exitCode": item["exitCode"],
                    "command": item["command"],
                }
                for item in attempts
            ],
        },
        "stems": stem_records,
        "checks": {
            "sourceShaVerified": True,
            "modelShaVerified": True,
            "fourExpectedStemsPresent": len(stem_records) == 4,
            "allStemHashesRecorded": all(bool(item["sha256"]) for item in stem_records),
            "allStemsStereo44100": all(item["channels"] == 2 and item["sampleRate"] == 44100 for item in stem_records),
            "allStemDurationsMatchSource": all(
                abs(item["durationSeconds"] - source_probe["durationSeconds"]) <= 0.25 for item in stem_records
            ),
            "sourceAudioPreserved": source.is_file() and sha256_file(source) == source_sha,
        },
        "truthBoundary": {
            "currentHostModelInferenceExecuted": True,
            "userSongSeparated": bool(args.declare_user_song),
            "vocalIsolationProven": True,
            "stemSeparationProven": True,
            "gpuInferenceProven": used_device == "cuda",
            "cpuInferenceProven": used_device == "cpu",
            "sourceAudioDeleted": False,
            "sourceAudioUploaded": False,
            "instrumentalMixCreated": False,
            "voiceConversionProven": False,
            "tensorRtInferenceProven": False,
            "hpOmenExecutionProven": False,
        },
    }
    manifest_path = output / "stem-separation-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"EchoesStemSeparation PASS source={source_sha} device={used_device} "
        f"stems={len(stem_records)} manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes stem separation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
