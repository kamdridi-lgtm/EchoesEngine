#!/usr/bin/env python3
"""Provision the pinned HTDemucs checkpoint with full SHA-256 verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MODEL_ID = "htdemucs"
MODEL_SIGNATURE = "955717e8"
MODEL_FILENAME = "955717e8-8726e21a.th"
MODEL_URL = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/955717e8-8726e21a.th"
MODEL_SHA256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, attempts: int = 4) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "EchoesEngine/HTDemucs-Provisioner"})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
            temporary.replace(destination)
            return
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            if attempt == attempts:
                raise
            time.sleep(attempt * 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--url", default=MODEL_URL)
    parser.add_argument("--expected-sha256", default=MODEL_SHA256)
    args = parser.parse_args()

    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    model_path = root / MODEL_FILENAME
    yaml_path = root / "htdemucs.yaml"

    if not model_path.is_file() or sha256_file(model_path) != args.expected_sha256.lower():
        download(args.url, model_path)

    actual_sha = sha256_file(model_path)
    if actual_sha != args.expected_sha256.lower():
        try:
            model_path.unlink()
        finally:
            raise RuntimeError(f"HTDemucs SHA-256 mismatch: expected {args.expected_sha256}, got {actual_sha}")

    yaml_path.write_text("models: ['955717e8']\n", encoding="utf-8")
    manifest = {
        "schema": "echoes.demucs-model-provisioning.v1",
        "status": "PASS",
        "provisionedAtUtc": utc_now(),
        "model": {
            "id": MODEL_ID,
            "signature": MODEL_SIGNATURE,
            "filename": MODEL_FILENAME,
            "url": args.url,
            "path": str(model_path),
            "sha256": actual_sha,
            "sizeBytes": model_path.stat().st_size,
            "checksumPrefixVerified": actual_sha.startswith("8726e21a"),
            "sources": ["drums", "bass", "other", "vocals"],
            "sampleRate": 44100,
            "license": "MIT",
        },
        "bag": {
            "path": str(yaml_path),
            "sha256": sha256_file(yaml_path),
        },
        "truthBoundary": {
            "modelBytesDownloaded": True,
            "fullSha256Verified": True,
            "modelLoadedForInference": False,
            "userSongSeparated": False,
            "hpOmenExecutionProven": False,
        },
    }
    manifest_path = root / "model-provisioning-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"EchoesDemucsProvision PASS model={MODEL_ID} sha256={actual_sha} "
        f"bytes={model_path.stat().st_size} output={root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
