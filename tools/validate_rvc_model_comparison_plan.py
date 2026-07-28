#!/usr/bin/env python3
"""Cross-platform contract proof for the RVC three-model comparison planner."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


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


def build_fixture(root: Path, mismatched_index: bool = False, blocked_input: bool = False) -> dict[str, Any]:
    runtime = root / "Echoes RVC Runtime With Spaces"
    weights = runtime / "source" / "assets" / "weights"
    indices = runtime / "source" / "assets" / "indices"
    vocals_dir = root / "Approved Vocals"
    for directory in (weights, indices, vocals_dir):
        directory.mkdir(parents=True, exist_ok=True)

    vocal = vocals_dir / "comparison vocals.wav"
    vocal.write_bytes(b"RIFF-controlled-approved-vocal-fixture")
    input_manifest = {
        "schema": "echoes.rvc-input-manifest.v1",
        "status": "BLOCKED" if blocked_input else "READY",
        "declaredUserSong": True,
        "vocalInput": {
            "path": str(vocal),
            "sha256": sha256_file(vocal),
            "sampleRate": 40000,
            "channels": 1,
            "bitDepth": 16,
        },
        "truthBoundary": {
            "rvcInputManifestPrepared": not blocked_input,
            "approvedListeningReviewVerified": not blocked_input,
            "sourceAndVocalHashesVerified": not blocked_input,
            "voiceConversionProven": False,
        },
    }
    input_manifest_path = root / "rvc-input-manifest.json"
    write_json(input_manifest_path, input_manifest)

    shared_index = indices / "Kam-Dridi-Voice.index"
    shared_index.write_bytes((b"shared-index-fixture\n" * 200)[:4096])

    manifests: dict[str, Path] = {}
    for label in ("700", "1000", "1500"):
        model = weights / f"Kam-Dridi-Voice-{label}E.pth"
        model.write_bytes((f"model-{label}-fixture\n".encode() * 100_000)[:1_300_000])
        index = shared_index
        if mismatched_index and label == "1500":
            index = indices / "Kam-Dridi-Voice-1500E.index"
            index.write_bytes((b"different-index-fixture\n" * 200)[:4096])
        manifest = {
            "schema": "echoes.rvc-voice-model.v1",
            "version": "1.0.0",
            "status": "VERIFIED",
            "runtimeRoot": str(runtime),
            "voiceOwner": "Kam Dridi",
            "modelName": f"Kam-Dridi-Voice-{label}E",
            "authorization": {
                "userOwnedModelDeclared": True,
                "ownerConsentVerified": True,
                "voiceConversionAuthorized": True,
                "thirdPartyImpersonationAllowed": False,
            },
            "model": {
                "path": str(model),
                "sha256": sha256_file(model),
                "sizeBytes": model.stat().st_size,
            },
            "index": {
                "path": str(index),
                "sha256": sha256_file(index),
                "sizeBytes": index.stat().st_size,
            },
            "truthBoundary": {
                "integrityVerified": True,
                "userOwnedVoice": True,
                "ownerConsentVerified": True,
                "voiceConversionProven": False,
            },
        }
        manifest_path = root / f"voice-model-{label}.json"
        write_json(manifest_path, manifest)
        manifests[label] = manifest_path
    return {
        "input": input_manifest_path,
        "models": manifests,
        "outputDirectory": root / "comparison outputs",
    }


def invoke(tool: Path, fixture: dict[str, Any], output: Path) -> tuple[int, dict[str, Any], str]:
    command = [
        sys.executable,
        str(tool),
        "--input-manifest",
        str(fixture["input"]),
        "--model",
        f"700={fixture['models']['700']}",
        "--model",
        f"1000={fixture['models']['1000']}",
        "--model",
        f"1500={fixture['models']['1500']}",
        "--output-directory",
        str(fixture["outputDirectory"]),
        "--output",
        str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    report = load_json(output) if output.is_file() else {}
    return completed.returncode, report, completed.stdout + completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tool = args.tool.resolve()
    require(tool.is_file(), "Comparison planner is missing")

    cases: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="echoes-rvc-comparison-plan-") as temporary:
        root = Path(temporary)

        valid = build_fixture(root / "valid")
        valid_output = root / "valid-plan.json"
        code, report, log = invoke(tool, valid, valid_output)
        require(code == 0, f"Valid comparison plan failed: {log}")
        require(report.get("status") == "READY", "Valid plan was not READY")
        runs = ((report.get("comparison") or {}).get("runs") or [])
        require([item.get("label") for item in runs] == ["700", "1000", "1500"], "Run order drifted")
        require(len({item.get("modelSha256") for item in runs}) == 3, "Models are not distinct")
        require(len({item.get("indexSha256") for item in runs}) == 1, "Index is not shared")
        require(len({item.get("inputSha256") for item in runs}) == 1, "Input is not shared")
        require(all((item.get("parameters") or {}).get("pitchShiftSemitones") == 0 for item in runs), "Pitch drifted")
        truth = report.get("truthBoundary") or {}
        require(truth.get("threeDistinctModelsVerified") is True, "Three-model proof missing")
        require(truth.get("sharedIndexVerified") is True, "Shared-index proof missing")
        require(truth.get("sharedInputVerified") is True, "Shared-input proof missing")
        require(truth.get("rvcInferenceProven") is False, "Planner falsely proved inference")
        require(truth.get("voiceConversionProven") is False, "Planner falsely proved conversion")
        cases["validComparison"] = {
            "status": report.get("status"),
            "labels": [item.get("label") for item in runs],
            "sharedInputSha256": runs[0].get("inputSha256"),
            "sharedIndexSha256": runs[0].get("indexSha256"),
        }

        mismatch = build_fixture(root / "mismatched-index", mismatched_index=True)
        mismatch_output = root / "mismatched-index-plan.json"
        code, report, log = invoke(tool, mismatch, mismatch_output)
        require(code == 2, f"Mismatched index did not block: {log}")
        require(not report, "Mismatched index unexpectedly emitted a READY plan")
        require("same index bytes" in log, "Mismatched index blocker was not reported")
        cases["mismatchedIndex"] = "BLOCKED"

        blocked = build_fixture(root / "blocked-input", blocked_input=True)
        blocked_output = root / "blocked-input-plan.json"
        code, report, log = invoke(tool, blocked, blocked_output)
        require(code == 2, f"Blocked input did not block: {log}")
        require(not report, "Blocked input unexpectedly emitted a READY plan")
        require("must be READY" in log, "Blocked input reason was not reported")
        cases["blockedInput"] = "BLOCKED"

    proof = {
        "schema": "echoes.rvc-model-comparison-plan-contract-proof.v1",
        "status": "PASS",
        "cases": cases,
        "truthBoundary": {
            "comparisonPlanningContractProven": True,
            "threeDistinctModelsVerifiedOnFixtures": True,
            "sharedInputAndIndexEnforced": True,
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
    print("EchoesRvcModelComparisonPlanContract PASS cases=3 inference=false conversion=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
