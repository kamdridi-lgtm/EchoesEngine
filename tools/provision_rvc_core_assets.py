#!/usr/bin/env python3
"""Provision pinned HuBERT/RMVPE assets without running RVC inference.

The downloader is fail-closed: the local runtime must already be PASS, the RVC
source checkout must match the pinned GitHub commit, every target must remain
inside that checkout, and every downloaded file is size/type checked and hashed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PINNED_RUNTIME_REPOSITORY = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
PINNED_RUNTIME_COMMIT = "4338f12c3c28c80b3ac015e2d0df66c41592746d"
PINNED_ASSET_REPOSITORY = "lj1995/VoiceConversionWebUI"
PINNED_ASSET_REVISION = "5836e9ea8ad6b7852f906acfa440e65a36e72396"
OUTPUT_SCHEMA = "echoes.rvc-core-assets.v1"

REQUIRED_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "id": "hubert-config",
        "remotePath": "hubert_base/config.json",
        "targetPath": "assets/hubert_base/config.json",
        "minimumBytes": 512,
        "kind": "json",
    },
    {
        "id": "hubert-preprocessor",
        "remotePath": "hubert_base/preprocessor_config.json",
        "targetPath": "assets/hubert_base/preprocessor_config.json",
        "minimumBytes": 100,
        "kind": "json",
    },
    {
        "id": "hubert-weights",
        "remotePath": "hubert_base/pytorch_model.bin",
        "targetPath": "assets/hubert_base/pytorch_model.bin",
        "minimumBytes": 100_000_000,
        "kind": "pytorch",
    },
    {
        "id": "rmvpe-weights",
        "remotePath": "rmvpe.pt",
        "targetPath": "assets/rmvpe/rmvpe.pt",
        "minimumBytes": 100_000_000,
        "kind": "pytorch",
    },
)

WINDOWS_SUPPORT_ASSETS: tuple[dict[str, Any], ...] = (
    {
        "id": "rmvpe-directml",
        "remotePath": "rmvpe.onnx",
        "targetPath": "assets/rmvpe/rmvpe.onnx",
        "minimumBytes": 100_000_000,
        "kind": "onnx",
    },
    {
        "id": "ffmpeg-windows",
        "remotePath": "ffmpeg.exe",
        "targetPath": "ffmpeg.exe",
        "minimumBytes": 10_000_000,
        "kind": "pe",
    },
    {
        "id": "ffprobe-windows",
        "remotePath": "ffprobe.exe",
        "targetPath": "ffprobe.exe",
        "minimumBytes": 10_000_000,
        "kind": "pe",
    },
)


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


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_runtime(runtime_root: Path, manifest: dict[str, Any]) -> Path:
    if manifest.get("schema") != "echoes.rvc-runtime-installation.v1":
        raise RuntimeError("RVC runtime manifest schema is invalid")
    if manifest.get("status") != "PASS":
        raise RuntimeError("RVC runtime must be PASS before assets are provisioned")
    recorded_root = Path(str(manifest.get("installRoot") or "")).resolve()
    if recorded_root != runtime_root.resolve():
        raise RuntimeError("RVC runtime root does not match the runtime manifest")
    upstream = manifest.get("upstream") if isinstance(manifest.get("upstream"), dict) else {}
    if upstream.get("repository") != PINNED_RUNTIME_REPOSITORY:
        raise RuntimeError("RVC runtime repository drifted")
    if upstream.get("commit") != PINNED_RUNTIME_COMMIT:
        raise RuntimeError("RVC runtime commit drifted")
    source = Path(str((manifest.get("sourceCheckout") or {}).get("root") or "")).resolve()
    if not source.is_dir() or not is_within(source, runtime_root):
        raise RuntimeError("RVC source checkout is missing or outside the managed runtime")
    return source


def asset_url(remote_path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in remote_path.split("/"))
    return (
        f"https://huggingface.co/{PINNED_ASSET_REPOSITORY}/resolve/"
        f"{PINNED_ASSET_REVISION}/{encoded}?download=true"
    )


def download_to(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.download-{os.getpid()}")
    if temporary.exists():
        temporary.unlink()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "EchoesEngine-RVC-Asset-Provisioner/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def validate_asset(path: Path, spec: dict[str, Any]) -> None:
    minimum = int(spec["minimumBytes"])
    if not path.is_file():
        raise RuntimeError(f"Downloaded asset is missing: {path}")
    if path.stat().st_size < minimum:
        raise RuntimeError(
            f"Downloaded asset is too small: {spec['id']} size={path.stat().st_size} minimum={minimum}"
        )
    kind = str(spec["kind"])
    if kind == "json":
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise RuntimeError(f"Downloaded JSON asset is not an object: {path}")
    elif kind == "pe":
        with path.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise RuntimeError(f"Downloaded Windows executable has no MZ header: {path}")
    elif kind in {"pytorch", "onnx"}:
        with path.open("rb") as handle:
            if not handle.read(16):
                raise RuntimeError(f"Downloaded model asset is empty: {path}")
    else:
        raise RuntimeError(f"Unsupported asset kind: {kind}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-windows-support", action="store_true")
    parser.add_argument("--reuse-verified-files", action="store_true")
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    runtime_manifest_path = args.runtime_manifest.resolve()
    output_path = args.output.resolve()
    if not runtime_root.is_dir():
        raise RuntimeError(f"RVC runtime root is missing: {runtime_root}")
    if not runtime_manifest_path.is_file():
        raise RuntimeError(f"RVC runtime manifest is missing: {runtime_manifest_path}")

    runtime_manifest = load_json(runtime_manifest_path)
    source_root = validate_runtime(runtime_root, runtime_manifest)
    specs = list(REQUIRED_ASSETS)
    if args.include_windows_support:
        specs.extend(WINDOWS_SUPPORT_ASSETS)

    assets: list[dict[str, Any]] = []
    for spec in specs:
        target = (source_root / str(spec["targetPath"])).resolve()
        if not is_within(target, source_root):
            raise RuntimeError(f"Asset target escaped the RVC source root: {target}")
        reused = False
        if args.reuse_verified_files and target.is_file():
            try:
                validate_asset(target, spec)
                reused = True
            except Exception:
                target.unlink(missing_ok=True)
        if not reused:
            download_to(asset_url(str(spec["remotePath"])), target)
            validate_asset(target, spec)
        assets.append(
            {
                "id": spec["id"],
                "remotePath": spec["remotePath"],
                "targetPath": spec["targetPath"],
                "path": str(target),
                "kind": spec["kind"],
                "sizeBytes": target.stat().st_size,
                "sha256": sha256_file(target),
                "reused": reused,
            }
        )

    required_ids = {str(item["id"]) for item in REQUIRED_ASSETS}
    provisioned_ids = {str(item["id"]) for item in assets}
    required_complete = required_ids.issubset(provisioned_ids)
    result = {
        "schema": OUTPUT_SCHEMA,
        "version": "1.0.0",
        "status": "VERIFIED" if required_complete else "BLOCKED",
        "provisionedAtUtc": utc_now(),
        "runtimeRoot": str(runtime_root),
        "sourceRoot": str(source_root),
        "runtimeManifest": {
            "path": str(runtime_manifest_path),
            "sha256": sha256_file(runtime_manifest_path),
            "status": runtime_manifest.get("status"),
        },
        "runtimeSource": {
            "repository": PINNED_RUNTIME_REPOSITORY,
            "commit": PINNED_RUNTIME_COMMIT,
        },
        "assetSource": {
            "repository": PINNED_ASSET_REPOSITORY,
            "revision": PINNED_ASSET_REVISION,
            "license": "MIT",
        },
        "profile": "windows-full" if args.include_windows_support else "inference-core",
        "assets": assets,
        "truthBoundary": {
            "pinnedAssetRevisionVerified": required_complete,
            "allProvisionedFileHashesRecorded": required_complete,
            "hubertFilesProvisioned": required_complete,
            "rmvpePytorchProvisioned": required_complete,
            "windowsSupportProvisioned": bool(args.include_windows_support),
            "hubertLoadProven": False,
            "rmvpeLoadProven": False,
            "syntheticModelSmokeProven": False,
            "userAudioRead": False,
            "voiceModelLoaded": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "audioUploaded": False,
            "executionAuthorized": False,
        },
    }
    write_json_atomic(output_path, result)
    print(
        "EchoesRvcCoreAssets VERIFIED "
        f"assets={len(assets)} revision={PINNED_ASSET_REVISION} "
        "user_audio=false voice_model=false inference=false conversion=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"EchoesRvcCoreAssets BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
