#!/usr/bin/env python3
"""Windows contract proof for the recovered 700/1000/1500 RVC orchestrator."""
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
MODEL_NAMES = {
    "700": "model_2_700e_63700s.pth",
    "1000": "model_2_1000e_91000s.pth",
    "1500": "model_2_1500e_136500s.pth",
}


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


def build_fixture(root: Path, *, omit_label: str | None = None) -> dict[str, Path]:
    runtime = root / "Echoes RVC Runtime With Spaces"
    source = runtime / "source"
    recovered = root / "Recovered Models With Spaces" / "model_2"
    control = runtime / "control"
    for directory in (
        source / "assets" / "weights",
        source / "assets" / "indices",
        source / "assets" / "hubert_base",
        source / "assets" / "rmvpe",
        recovered,
        control,
    ):
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
    write_json(runtime / "rvc-runtime-manifest.json", runtime_manifest)

    assets = []
    for asset_id, relative, payload in (
        ("hubert-config", "assets/hubert_base/config.json", b'{"fixture":true}'),
        ("hubert-preprocessor", "assets/hubert_base/preprocessor_config.json", b'{"fixture":true}'),
        ("hubert-weights", "assets/hubert_base/pytorch_model.bin", b"fixture-hubert-weights"),
        ("rmvpe-weights", "assets/rmvpe/rmvpe.pt", b"fixture-rmvpe-weights"),
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        assets.append(
            {
                "id": asset_id,
                "path": str(path),
                "targetPath": relative,
                "sha256": sha256_file(path),
                "sizeBytes": path.stat().st_size,
            }
        )
    write_json(
        control / "rvc-core-assets-manifest.json",
        {
            "schema": "echoes.rvc-core-assets.v1",
            "status": "VERIFIED",
            "runtimeRoot": str(runtime),
            "sourceRoot": str(source),
            "assets": assets,
        },
    )

    for label, filename in MODEL_NAMES.items():
        if label == omit_label:
            continue
        model = recovered / filename
        payload = (f"controlled-model-{label}\n".encode("utf-8") * 100_000)[:1_300_000]
        model.write_bytes(payload)
    index = recovered / "model_2.index"
    index.write_bytes((b"controlled-shared-index\n" * 300)[:6_000])

    vocal = root / "Approved Vocals" / "comparison-vocals.wav"
    vocal.parent.mkdir(parents=True, exist_ok=True)
    vocal.write_bytes(b"RIFF-controlled-approved-vocal-fixture")
    input_manifest = control / "rvc-input-manifest.json"
    write_json(
        input_manifest,
        {
            "schema": "echoes.rvc-input-manifest.v1",
            "status": "READY",
            "declaredUserSong": True,
            "vocalInput": {
                "path": str(vocal),
                "sha256": sha256_file(vocal),
                "sampleRate": 40000,
                "channels": 1,
                "bitDepth": 16,
            },
            "truthBoundary": {
                "rvcInputManifestPrepared": True,
                "approvedListeningReviewVerified": True,
                "sourceAndVocalHashesVerified": True,
                "voiceConversionProven": False,
            },
        },
    )

    return {
        "runtime": runtime,
        "source": source,
        "recovered": recovered,
        "inputManifest": input_manifest,
        "comparison": root / "Comparison Control With Spaces",
    }


def invoke(script: Path, source_root: Path, fixture: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-SourceRoot",
        str(source_root),
        "-RvcRuntimeRoot",
        str(fixture["runtime"]),
        "-RecoveredModelRoot",
        str(fixture["recovered"]),
        "-RvcInputManifest",
        str(fixture["inputManifest"]),
        "-ComparisonOutputRoot",
        str(fixture["comparison"]),
        "-PythonExecutable",
        sys.executable,
        "-VoiceOwner",
        "Kam Dridi",
        "-DeclareUserOwnedModel",
        "-ConfirmOwnerConsent",
        "-AuthorizeVoiceConversion",
        "-ForbidThirdPartyImpersonation",
        "-AllowNonDDrive",
        "-NoOpen",
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def verify_valid_fixture(fixture: dict[str, Path]) -> dict[str, Any]:
    comparison = fixture["comparison"]
    summary_path = comparison / "rvc-model-comparison-summary.json"
    plan_path = comparison / "rvc-model-comparison-plan.json"
    require(summary_path.is_file(), "Comparison summary was not created")
    require(plan_path.is_file(), "Comparison plan was not created")

    summary = load_json(summary_path)
    plan = load_json(plan_path)
    require(summary.get("status") == "READY", "Comparison summary was not READY")
    require(plan.get("status") == "READY", "Comparison plan was not READY")
    runs = ((plan.get("comparison") or {}).get("runs") or [])
    require([item.get("label") for item in runs] == ["700", "1000", "1500"], "Comparison order drifted")
    require(len({item.get("modelSha256") for item in runs}) == 3, "Registered model hashes are not distinct")
    require(len({item.get("indexSha256") for item in runs}) == 1, "Registered indices do not share one hash")
    require(len({item.get("inputSha256") for item in runs}) == 1, "Comparison input is not shared")
    require(all((item.get("parameters") or {}).get("pitchShiftSemitones") == 0 for item in runs), "Pitch shift drifted")
    require(all((item.get("parameters") or {}).get("f0Method") == "rmvpe" for item in runs), "F0 method drifted")

    for label in ("700", "1000", "1500"):
        manifest_path = comparison / "models" / f"rvc-voice-model-{label}.json"
        require(manifest_path.is_file(), f"Registered manifest is missing for {label}")
        manifest = load_json(manifest_path)
        require(manifest.get("status") == "VERIFIED", f"Registered model {label} was not VERIFIED")
        model_path = Path(str((manifest.get("model") or {}).get("path") or ""))
        index_path = Path(str((manifest.get("index") or {}).get("path") or ""))
        require(model_path.is_file() and model_path.parent.name == "weights", f"Managed model copy is invalid for {label}")
        require(index_path.is_file() and index_path.parent.name == "indices", f"Managed index copy is invalid for {label}")
        require(sha256_file(model_path) == (manifest.get("model") or {}).get("sha256"), f"Managed model hash drifted for {label}")
        require(sha256_file(index_path) == (manifest.get("index") or {}).get("sha256"), f"Managed index hash drifted for {label}")

    output_paths = [Path(str(item.get("outputPath") or "")) for item in runs]
    require(all(not path.exists() for path in output_paths), "Orchestrator unexpectedly generated converted audio")
    truth = summary.get("truthBoundary") or {}
    require(truth.get("recoveredModelsCopiedIntoManagedRuntime") is True, "Managed registration proof missing")
    require(truth.get("threeModelComparisonPlanReady") is True, "Comparison readiness proof missing")
    require(truth.get("rvcInferenceProven") is False, "Orchestrator falsely proved inference")
    require(truth.get("voiceConversionProven") is False, "Orchestrator falsely proved conversion")
    require(truth.get("convertedAudioGenerated") is False, "Orchestrator falsely claimed audio generation")

    return {
        "status": summary.get("status"),
        "labels": [item.get("label") for item in runs],
        "sharedIndexSha256": runs[0].get("indexSha256"),
        "sharedInputSha256": runs[0].get("inputSha256"),
        "plannedOutputs": [str(path) for path in output_paths],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    script = args.script.resolve()
    source_root = args.source_root.resolve()
    require(script.is_file(), "Windows orchestrator is missing")
    require((source_root / "tools" / "register_rvc_voice_model.py").is_file(), "Voice model registrar is missing")
    require((source_root / "tools" / "plan_rvc_model_comparison.py").is_file(), "Comparison planner is missing")

    cases: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="echoes-rvc-three-model-windows-") as temporary:
        root = Path(temporary)

        valid = build_fixture(root / "valid")
        completed = invoke(script, source_root, valid)
        require(completed.returncode == 0, f"Valid Windows orchestration failed:\n{completed.stdout}\n{completed.stderr}")
        cases["validPreparation"] = verify_valid_fixture(valid)

        missing = build_fixture(root / "missing-1000", omit_label="1000")
        completed = invoke(script, source_root, missing)
        combined = completed.stdout + completed.stderr
        require(completed.returncode != 0, "Missing 1000 checkpoint did not block")
        require("Recovered checkpoint is missing for 1000" in combined, "Missing checkpoint blocker was not reported")
        require(not (missing["comparison"] / "rvc-model-comparison-summary.json").exists(), "Blocked fixture emitted a READY summary")
        cases["missing1000Checkpoint"] = "BLOCKED"

    proof = {
        "schema": "echoes.rvc-three-model-windows-orchestrator-proof.v1",
        "status": "PASS",
        "cases": cases,
        "truthBoundary": {
            "windowsOrchestratorContractProven": True,
            "threeModelRegistrationAndPlanningProvenOnFixtures": True,
            "realKamDridiModelsRead": False,
            "userAudioRead": False,
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
    print("EchoesRvcThreeModelWindowsOrchestrator PASS cases=2 inference=false conversion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
