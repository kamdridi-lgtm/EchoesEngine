#!/usr/bin/env python3
"""Validate the Windows Echoes RVC source and Python foundation."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

PINNED_REPOSITORY = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
PINNED_COMMIT = "4338f12c3c28c80b3ac015e2d0df66c41592746d"
REQUIRED_FILES = {
    "README.md",
    "LICENSE",
    "webui.py",
    "requirments_cu118_py312.txt",
    "requirments_cpu_py312.txt",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expect-status", choices=("PARTIAL", "PASS"), required=True)
    parser.add_argument("--expect-dependencies", choices=("SKIPPED", "PASS"), required=True)
    args = parser.parse_args()

    runtime = args.runtime_root.resolve()
    manifest_path = runtime / "rvc-runtime-manifest.json"
    require(runtime.is_dir(), "Runtime root is missing")
    require(manifest_path.is_file(), "Runtime manifest is missing")
    manifest = load_json(manifest_path)

    require(manifest.get("schema") == "echoes.rvc-runtime-installation.v1", "Runtime schema mismatch")
    require(manifest.get("status") == args.expect_status, "Runtime status mismatch")
    require(Path(str(manifest.get("installRoot"))).resolve() == runtime, "Runtime root manifest mismatch")

    upstream = manifest.get("upstream") or {}
    require(upstream.get("repository") == PINNED_REPOSITORY, "Pinned repository drifted")
    require(upstream.get("commit") == PINNED_COMMIT, "Pinned commit drifted")
    require(upstream.get("license") == "MIT", "Licence drifted")

    checkout = Path(str((manifest.get("sourceCheckout") or {}).get("root"))).resolve()
    require(checkout == runtime / "source", "Source checkout path mismatch")
    require(checkout.is_dir(), "Source checkout is missing")
    require((manifest.get("sourceCheckout") or {}).get("head") == PINNED_COMMIT, "Recorded checkout head drifted")

    installed = {
        str(item.get("relativePath")): item
        for item in (manifest.get("installedFiles") or [])
        if isinstance(item, dict)
    }
    require(set(installed) == REQUIRED_FILES, "Required source file inventory mismatch")
    for relative in REQUIRED_FILES:
        item = installed[relative]
        path = checkout / relative
        require(path.is_file(), f"Required source file missing: {relative}")
        require(path.resolve() == Path(str(item.get("path"))).resolve(), f"Recorded path mismatch: {relative}")
        require(sha256_file(path) == item.get("sha256"), f"Source file SHA mismatch: {relative}")
        require(path.stat().st_size == item.get("sizeBytes"), f"Source file size mismatch: {relative}")

    license_text = (checkout / "LICENSE").read_text(encoding="utf-8", errors="replace")
    require("MIT License" in license_text, "Expected MIT licence text missing")

    python_info = manifest.get("python") or {}
    python = Path(str(python_info.get("executable"))).resolve()
    require(python.is_file(), "Virtual-environment Python is missing")
    completed = subprocess.run(
        [str(python), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    require(completed.returncode == 0, "Virtual-environment Python did not execute")
    actual_version = completed.stdout.strip()
    require(actual_version.startswith("3.12."), f"Virtual-environment Python is not 3.12: {actual_version}")
    require(str(python_info.get("version") or "") == actual_version, "Recorded Python version mismatch")
    require(python_info.get("isolatedVirtualEnvironment") is True, "Venv isolation not recorded")

    dependencies = manifest.get("dependencies") or {}
    require(dependencies.get("status") == args.expect_dependencies, "Dependency status mismatch")
    require(bool(dependencies.get("skipped")) == (args.expect_dependencies == "SKIPPED"), "Dependency skip truth mismatch")
    if args.expect_dependencies == "SKIPPED":
        require(manifest.get("provider") == "uninstalled", "Skipped foundation falsely selected a provider")
        require((manifest.get("torch") or {}).get("version") is None, "Skipped foundation falsely recorded Torch")
    else:
        require(manifest.get("provider") in {"cpu", "cuda"}, "Installed runtime provider invalid")
        require(str((manifest.get("torch") or {}).get("version") or "").startswith("2.7.1"), "Torch 2.7.1 not recorded")

    launcher = Path(str(manifest.get("launcher"))).resolve()
    require(launcher.is_file(), "RVC launcher is missing")
    require(launcher == runtime / "Open-Echoes-Rvc.ps1", "RVC launcher path drifted")
    require((runtime / "RVC-RUNTIME-STATUS.txt").is_file(), "Runtime status file is missing")

    truth = manifest.get("truthBoundary") or {}
    require(truth.get("sourceCheckoutVerified") is True, "Source checkout proof missing")
    require(truth.get("pinnedCommitVerified") is True, "Pinned commit proof missing")
    require(truth.get("requiredSourceHashesRecorded") is True, "Source hash proof missing")
    require(truth.get("pythonRuntimeVerified") is True, "Python proof missing")
    require(truth.get("productionDependenciesInstalled") is (args.expect_dependencies == "PASS"), "Dependency truth drifted")
    for field in (
        "hpOmenRuntimeInstalled",
        "kamDridiVoiceModelVerified",
        "cudaInferenceProven",
        "cpuInferenceProven",
        "rvcInferenceProven",
        "voiceConversionProven",
        "convertedAudioGenerated",
        "audioUploaded",
        "executionAuthorized",
    ):
        require(truth.get(field) is False, f"Runtime falsely promoted capability: {field}")
    require(truth.get("requiresOperatorApproval") is True, "Operator approval requirement missing")

    print(
        "EchoesRvcFoundationValidation PASS "
        f"status={manifest.get('status')} dependencies={dependencies.get('status')} "
        f"commit={PINNED_COMMIT} python={actual_version} conversion=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
