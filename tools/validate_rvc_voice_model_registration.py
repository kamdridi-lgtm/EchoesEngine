#!/usr/bin/env python3
"""Cross-platform proof for fail-closed RVC voice-model registration."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PINNED_REPOSITORY = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
PINNED_COMMIT = "4338f12c3c28c80b3ac015e2d0df66c41592746d"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build_fixture(root: Path) -> dict[str, Path]:
    runtime = root / "Echoes RVC Runtime With Spaces"
    source = runtime / "source"
    weights = source / "assets" / "weights"
    indices = source / "assets" / "indices"
    assets_dir = source / "assets" / "hubert_base"
    rmvpe_dir = source / "assets" / "rmvpe"
    for directory in (weights, indices, assets_dir, rmvpe_dir):
        directory.mkdir(parents=True, exist_ok=True)

    runtime_manifest = {
        "schema": "echoes.rvc-runtime-installation.v1",
        "status": "PASS",
        "installRoot": str(runtime),
        "sourceCheckout": {"root": str(source)},
        "upstream": {
            "repository": PINNED_REPOSITORY,
            "commit": PINNED_COMMIT,
            "license": "MIT",
        },
    }
    runtime_manifest_path = runtime / "rvc-runtime-manifest.json"
    write_json(runtime_manifest_path, runtime_manifest)

    asset_entries = []
    for name, relative, content in (
        ("hubert-config", "assets/hubert_base/config.json", b"{\"fixture\":true}"),
        ("hubert-preprocessor", "assets/hubert_base/preprocessor_config.json", b"{\"fixture\":true}"),
        ("hubert-weights", "assets/hubert_base/pytorch_model.bin", b"fixture-hubert-weights"),
        ("rmvpe-weights", "assets/rmvpe/rmvpe.pt", b"fixture-rmvpe-weights"),
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        asset_entries.append(
            {
                "id": name,
                "path": str(path),
                "targetPath": relative,
                "sha256": sha256_file(path),
                "sizeBytes": path.stat().st_size,
            }
        )
    assets_manifest = {
        "schema": "echoes.rvc-core-assets.v1",
        "status": "VERIFIED",
        "runtimeRoot": str(runtime),
        "sourceRoot": str(source),
        "assetSource": {
            "repository": "lj1995/VoiceConversionWebUI",
            "revision": "5836e9ea8ad6b7852f906acfa440e65a36e72396",
        },
        "assets": asset_entries,
    }
    assets_manifest_path = runtime / "rvc-core-assets-manifest.json"
    write_json(assets_manifest_path, assets_manifest)

    model = root / "Kam Dridi Controlled Voice Model.pth"
    index = root / "Kam Dridi Controlled Voice Model.index"
    model.write_bytes((b"controlled-rvc-model-fixture\n" * 50_000)[:1_200_000])
    index.write_bytes((b"controlled-rvc-index-fixture\n" * 100)[:2_400])
    return {
        "runtime": runtime,
        "runtimeManifest": runtime_manifest_path,
        "assetsManifest": assets_manifest_path,
        "model": model,
        "index": index,
    }


def invoke(tool: Path, fixture: dict[str, Path], output: Path, include_consent: bool) -> tuple[int, dict[str, Any], str]:
    command = [
        sys.executable,
        str(tool),
        "--runtime-root",
        str(fixture["runtime"]),
        "--runtime-manifest",
        str(fixture["runtimeManifest"]),
        "--core-assets-manifest",
        str(fixture["assetsManifest"]),
        "--model-file",
        str(fixture["model"]),
        "--index-file",
        str(fixture["index"]),
        "--voice-owner",
        "Kam Dridi",
        "--model-name",
        "Kam-Dridi-Voice",
        "--output",
        str(output),
    ]
    if include_consent:
        command.extend(
            [
                "--declare-user-owned-model",
                "--confirm-owner-consent",
                "--authorize-voice-conversion",
                "--forbid-third-party-impersonation",
            ]
        )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    report = load_json(output) if output.is_file() else {}
    return completed.returncode, report, completed.stdout + completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tool = args.tool.resolve()
    require(tool.is_file(), "Voice model registrar is missing")

    cases: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="echoes-rvc-model-registration-") as temporary:
        root = Path(temporary)
        fixture = build_fixture(root / "valid")
        valid_output = root / "valid-registration.json"
        code, report, log = invoke(tool, fixture, valid_output, include_consent=True)
        require(code == 0, f"Valid voice model registration failed: {log}")
        require(report.get("status") == "VERIFIED", "Valid model was not VERIFIED")
        require((report.get("authorization") or {}).get("thirdPartyImpersonationAllowed") is False, "Impersonation policy drifted")
        model = Path(str((report.get("model") or {}).get("path") or ""))
        index = Path(str((report.get("index") or {}).get("path") or ""))
        require(model.is_file() and model.parent.name == "weights", "Managed model copy is invalid")
        require(index.is_file() and index.parent.name == "indices", "Managed index copy is invalid")
        require(sha256_file(model) == (report.get("model") or {}).get("sha256"), "Model SHA drifted")
        require(sha256_file(index) == (report.get("index") or {}).get("sha256"), "Index SHA drifted")
        truth = report.get("truthBoundary") or {}
        require(truth.get("modelDeserializationAttempted") is False, "Registrar deserialized the model")
        require(truth.get("modelLoadProven") is False, "Registrar falsely proved model loading")
        require(truth.get("voiceConversionProven") is False, "Registrar falsely proved conversion")
        cases["validRegistration"] = {
            "status": report.get("status"),
            "modelSha256": (report.get("model") or {}).get("sha256"),
            "indexSha256": (report.get("index") or {}).get("sha256"),
        }

        blocked_fixture = build_fixture(root / "missing-consent")
        blocked_output = root / "blocked-registration.json"
        code, report, log = invoke(tool, blocked_fixture, blocked_output, include_consent=False)
        require(code == 2, f"Missing-consent registration did not block: {log}")
        require(not report, "Blocked registration unexpectedly emitted a VERIFIED manifest")
        require("VOICE_OWNER_CONSENT_NOT_CONFIRMED" in log, "Missing consent blocker was not reported")
        cases["missingConsent"] = "BLOCKED"

    proof = {
        "schema": "echoes.rvc-voice-model-registration-contract-proof.v1",
        "status": "PASS",
        "cases": cases,
        "truthBoundary": {
            "registrationContractProven": True,
            "realKamDridiModelRegistered": False,
            "modelDeserializationAttempted": False,
            "voiceModelLoadProven": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "executionAuthorized": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, proof)
    print("EchoesRvcVoiceModelRegistrationContract PASS cases=2 conversion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
