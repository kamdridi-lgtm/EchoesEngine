#!/usr/bin/env python3
"""Load pinned HuBERT/RMVPE assets with synthetic audio only.

This is a component smoke proof, not a voice conversion. It never reads user
audio, never loads a user voice model and never writes audio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PINNED_ASSET_REPOSITORY = "lj1995/VoiceConversionWebUI"
PINNED_ASSET_REVISION = "5836e9ea8ad6b7852f906acfa440e65a36e72396"
REQUIRED_IDS = {
    "hubert-config",
    "hubert-preprocessor",
    "hubert-weights",
    "rmvpe-weights",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_assets(runtime_root: Path, manifest: dict[str, Any]) -> tuple[Path, dict[str, dict[str, Any]]]:
    if manifest.get("schema") != "echoes.rvc-core-assets.v1":
        raise RuntimeError("Core-assets manifest schema is invalid")
    if manifest.get("status") != "VERIFIED":
        raise RuntimeError("Core-assets manifest is not VERIFIED")
    if Path(str(manifest.get("runtimeRoot") or "")).resolve() != runtime_root.resolve():
        raise RuntimeError("Core-assets manifest points to another runtime")
    source = manifest.get("assetSource") if isinstance(manifest.get("assetSource"), dict) else {}
    if source.get("repository") != PINNED_ASSET_REPOSITORY:
        raise RuntimeError("Core asset repository drifted")
    if source.get("revision") != PINNED_ASSET_REVISION:
        raise RuntimeError("Core asset revision drifted")
    source_root = Path(str(manifest.get("sourceRoot") or "")).resolve()
    if not source_root.is_dir():
        raise RuntimeError("RVC source root is missing")
    entries = {
        str(entry.get("id")): entry
        for entry in manifest.get("assets") or []
        if isinstance(entry, dict) and entry.get("id")
    }
    if not REQUIRED_IDS.issubset(entries):
        raise RuntimeError("Core-assets manifest is missing required asset records")
    for asset_id in REQUIRED_IDS:
        entry = entries[asset_id]
        path = Path(str(entry.get("path") or ""))
        expected = str(entry.get("sha256") or "").lower()
        if not path.is_file() or len(expected) != 64 or sha256_file(path) != expected:
            raise RuntimeError(f"Core asset integrity failed: {asset_id}")
    return source_root, entries


def finite_tensor(tensor: Any) -> bool:
    import torch

    return bool(torch.isfinite(tensor).all().item())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--assets-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    assets_manifest_path = args.assets_manifest.resolve()
    output_path = args.output.resolve()
    if not runtime_root.is_dir() or not assets_manifest_path.is_file():
        raise RuntimeError("Runtime root or assets manifest is missing")

    manifest = load_json(assets_manifest_path)
    source_root, entries = verify_assets(runtime_root, manifest)
    sys.path.insert(0, str(source_root))

    import numpy as np
    import torch

    from infer.hubert import extract_hubert_features, load_hubert_model
    from infer.rmvpe import RMVPE

    torch.set_grad_enabled(False)
    hubert = load_hubert_model(torch.device("cpu"), is_half=False)
    synthetic_hubert_audio = torch.zeros((1, 3200), dtype=torch.float32)
    hubert_v1 = extract_hubert_features(hubert, synthetic_hubert_audio, "v1")
    hubert_v2 = extract_hubert_features(hubert, synthetic_hubert_audio, "v2")
    if hubert_v1.ndim != 3 or hubert_v1.shape[-1] != 256 or not finite_tensor(hubert_v1):
        raise RuntimeError(f"HuBERT v1 synthetic feature proof failed: shape={tuple(hubert_v1.shape)}")
    if hubert_v2.ndim != 3 or hubert_v2.shape[-1] != 768 or not finite_tensor(hubert_v2):
        raise RuntimeError(f"HuBERT v2 synthetic feature proof failed: shape={tuple(hubert_v2.shape)}")

    sample_rate = 16000
    duration_samples = sample_rate
    phase = np.arange(duration_samples, dtype=np.float32) / np.float32(sample_rate)
    synthetic_pitch_audio = (0.05 * np.sin(2.0 * math.pi * 220.0 * phase)).astype(np.float32)
    rmvpe_path = Path(str(entries["rmvpe-weights"]["path"]))
    rmvpe = RMVPE(str(rmvpe_path), is_half=False, device="cpu")
    f0 = np.asarray(rmvpe.infer_from_audio(synthetic_pitch_audio, thred=0.03), dtype=np.float32)
    if f0.ndim != 1 or f0.size == 0 or not bool(np.isfinite(f0).all()):
        raise RuntimeError(f"RMVPE synthetic pitch proof failed: shape={f0.shape}")

    windows_tools: dict[str, Any] = {}
    for tool_id in ("ffmpeg-windows", "ffprobe-windows"):
        entry = {
            str(item.get("id")): item
            for item in manifest.get("assets") or []
            if isinstance(item, dict)
        }.get(tool_id)
        if entry:
            executable = Path(str(entry.get("path") or ""))
            completed = subprocess.run(
                [str(executable), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"Windows support executable failed: {tool_id}")
            windows_tools[tool_id] = {"executed": True, "returnCode": completed.returncode}

    result = {
        "schema": "echoes.rvc-core-assets-smoke-proof.v1",
        "version": "1.0.0",
        "status": "PASS",
        "provenAtUtc": utc_now(),
        "runtimeRoot": str(runtime_root),
        "assetsManifest": {
            "path": str(assets_manifest_path),
            "sha256": sha256_file(assets_manifest_path),
        },
        "versions": {
            "python": sys.version.split()[0],
            "torch": torch.__version__,
            "numpy": np.__version__,
        },
        "hubert": {
            "device": "cpu",
            "v1Shape": list(hubert_v1.shape),
            "v2Shape": list(hubert_v2.shape),
            "finite": True,
        },
        "rmvpe": {
            "device": "cpu",
            "syntheticInputSamples": int(synthetic_pitch_audio.size),
            "outputFrames": int(f0.size),
            "finite": True,
            "maximumHz": float(np.max(f0)),
        },
        "windowsTools": windows_tools,
        "truthBoundary": {
            "pinnedCoreAssetIntegrityVerified": True,
            "hubertLoadProven": True,
            "hubertSyntheticFeatureExtractionProven": True,
            "rmvpeLoadProven": True,
            "rmvpeSyntheticPitchExtractionProven": True,
            "syntheticAudioOnly": True,
            "userAudioRead": False,
            "voiceModelLoaded": False,
            "voiceIndexLoaded": False,
            "fullRvcPipelineExecuted": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "audioUploaded": False,
            "executionAuthorized": False,
        },
    }
    write_json_atomic(output_path, result)
    print(
        "EchoesRvcCoreAssetsSmoke PASS "
        f"hubert_v1={tuple(hubert_v1.shape)} hubert_v2={tuple(hubert_v2.shape)} "
        f"rmvpe_frames={f0.size} synthetic_only=true conversion=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"EchoesRvcCoreAssetsSmoke BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
