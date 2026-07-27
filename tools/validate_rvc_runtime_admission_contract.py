#!/usr/bin/env python3
"""Cross-platform contract proof for the fail-closed Echoes RVC admission gate."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PINNED_REPOSITORY = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
PINNED_COMMIT = "4338f12c3c28c80b3ac015e2d0df66c41592746d"
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "webui.py",
    "requirments_cu118_py312.txt",
    "requirments_cpu_py312.txt",
)


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
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build_fixture(root: Path, provider: str = "cpu") -> dict[str, Path]:
    runtime = root / "Echoes RVC Runtime With Spaces"
    source_checkout = runtime / "source"
    weights = source_checkout / "assets" / "weights"
    indices = source_checkout / "assets" / "indices"
    job = root / "Reviewed Stem Job With Spaces"
    source_checkout.mkdir(parents=True, exist_ok=True)
    weights.mkdir(parents=True, exist_ok=True)
    indices.mkdir(parents=True, exist_ok=True)
    job.mkdir(parents=True, exist_ok=True)

    installed_files: list[dict[str, Any]] = []
    for relative in REQUIRED_FILES:
        path = source_checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"controlled fixture for {relative}\n", encoding="utf-8")
        installed_files.append(
            {"relativePath": relative, "path": str(path), "sha256": sha256_file(path)}
        )

    source_audio = job / "source.wav"
    vocal_audio = job / "vocals.wav"
    source_audio.write_bytes(b"RIFF-controlled-source-audio")
    vocal_audio.write_bytes(b"RIFF-controlled-approved-vocal")
    source_sha = sha256_file(source_audio)
    vocal_sha = sha256_file(vocal_audio)

    input_manifest = {
        "schema": "echoes.rvc-input-manifest.v1",
        "status": "READY",
        "declaredUserSong": False,
        "source": {"path": str(source_audio), "name": source_audio.name, "sha256": source_sha},
        "vocalInput": {
            "path": str(vocal_audio),
            "name": vocal_audio.name,
            "sha256": vocal_sha,
            "sampleRate": 44100,
            "channels": 2,
            "bitDepth": 24,
        },
        "truthBoundary": {
            "rvcInputManifestPrepared": True,
            "approvedListeningReviewVerified": True,
            "sourceAndVocalHashesVerified": True,
            "rvcRuntimeProven": False,
            "voiceModelProvisioned": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "executionAuthorized": False,
        },
    }

    runtime_manifest = {
        "schema": "echoes.rvc-runtime-installation.v1",
        "status": "PASS",
        "installRoot": str(runtime),
        "sourceCheckout": {"root": str(source_checkout)},
        "upstream": {
            "repository": PINNED_REPOSITORY,
            "commit": PINNED_COMMIT,
            "license": "MIT",
        },
        "python": {"version": "3.12.7", "executable": str(runtime / ".venv" / "python")},
        "torch": {
            "version": "2.7.1+cu118" if provider == "cuda" else "2.4.1+cpu",
            "cudaAvailable": provider == "cuda",
            "cudaVersion": "11.8" if provider == "cuda" else None,
        },
        "provider": provider,
        "cpuFallback": True,
        "installedFiles": installed_files,
        "truthBoundary": {
            "sourceCheckoutVerified": True,
            "pythonRuntimeVerified": True,
            "torchImportVerified": True,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "executionAuthorized": False,
        },
    }

    model_file = weights / "Kam-Dridi-Voice.pth"
    index_file = indices / "Kam-Dridi-Voice.index"
    model_file.write_bytes(b"controlled-kam-dridi-rvc-model")
    index_file.write_bytes(b"controlled-kam-dridi-rvc-index")
    model_manifest = {
        "schema": "echoes.rvc-voice-model.v1",
        "status": "VERIFIED",
        "voiceOwner": "Kam Dridi",
        "authorization": {
            "ownerConsentVerified": True,
            "voiceConversionAuthorized": True,
            "thirdPartyImpersonationAllowed": False,
        },
        "model": {
            "path": str(model_file),
            "sha256": sha256_file(model_file),
            "sizeBytes": model_file.stat().st_size,
        },
        "index": {
            "path": str(index_file),
            "sha256": sha256_file(index_file),
            "sizeBytes": index_file.stat().st_size,
        },
        "truthBoundary": {
            "integrityVerified": True,
            "userOwnedVoice": True,
            "inferenceProven": False,
            "voiceConversionProven": False,
        },
    }

    input_path = root / "rvc-input-manifest.json"
    runtime_path = root / "rvc-runtime-manifest.json"
    model_path = root / "rvc-voice-model-manifest.json"
    write_json(input_path, input_manifest)
    write_json(runtime_path, runtime_manifest)
    write_json(model_path, model_manifest)
    return {
        "runtime": runtime,
        "sourceCheckout": source_checkout,
        "sourceAudio": source_audio,
        "vocalAudio": vocal_audio,
        "modelFile": model_file,
        "indexFile": index_file,
        "inputManifest": input_path,
        "runtimeManifest": runtime_path,
        "modelManifest": model_path,
    }


def invoke(tool: Path, fixture: dict[str, Path], output: Path) -> tuple[int, dict[str, Any], str]:
    source_sha = load_json(fixture["inputManifest"])["source"]["sha256"]
    completed = subprocess.run(
        [
            sys.executable,
            str(tool),
            "--input-manifest",
            str(fixture["inputManifest"]),
            "--runtime-manifest",
            str(fixture["runtimeManifest"]),
            "--voice-model-manifest",
            str(fixture["modelManifest"]),
            "--expected-source-sha256",
            source_sha,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    report = load_json(output) if output.is_file() else {}
    return completed.returncode, report, completed.stdout + completed.stderr


def cloned_fixture(base: Path, name: str, provider: str = "cpu") -> dict[str, Path]:
    target = base / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return build_fixture(target, provider=provider)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tool = args.tool.resolve()
    require(tool.is_file(), "Admission tool is missing")

    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="echoes-rvc-admission-") as temporary:
        root = Path(temporary)

        cpu = cloned_fixture(root, "valid cpu fixture", provider="cpu")
        code, report, log = invoke(tool, cpu, root / "cpu-report.json")
        require(code == 0, f"Valid CPU fixture failed: {log}")
        require(report.get("status") == "ADMITTED", "Valid CPU fixture was not admitted")
        runtime_report = report.get("runtime") or {}
        require(runtime_report.get("provider") == "cpu", "CPU provider drifted")
        require(str(runtime_report.get("torchVersion") or "").startswith("2.4.1"), "CPU Torch contract drifted")
        truth = report.get("truthBoundary") or {}
        require(truth.get("conversionAdmitted") is True, "CPU admission not recorded")
        require(truth.get("conversionCommandIssued") is False, "Gate issued a conversion command")
        require(truth.get("rvcInferenceProven") is False, "Gate falsely proved inference")
        require(truth.get("voiceConversionProven") is False, "Gate falsely proved conversion")
        require(truth.get("convertedAudioGenerated") is False, "Gate falsely claimed audio output")
        require(truth.get("executionAuthorized") is False, "Gate authorized execution")
        results["validCpu"] = {"status": report.get("status"), "blockers": report.get("blockers")}

        cuda = cloned_fixture(root, "valid cuda fixture", provider="cuda")
        code, report, log = invoke(tool, cuda, root / "cuda-report.json")
        require(code == 0, f"Valid CUDA evidence fixture failed: {log}")
        require(report.get("status") == "ADMITTED", "Valid CUDA evidence fixture was not admitted")
        runtime_report = report.get("runtime") or {}
        require(runtime_report.get("cudaAvailable") is True, "CUDA evidence not retained")
        require(str(runtime_report.get("torchVersion") or "").startswith("2.7.1"), "CUDA Torch contract drifted")
        results["validCudaEvidence"] = {"status": report.get("status"), "blockers": report.get("blockers")}

        cpu_wrong_torch = cloned_fixture(root, "cpu wrong torch", provider="cpu")
        manifest = load_json(cpu_wrong_torch["runtimeManifest"])
        manifest["torch"]["version"] = "2.7.1+cpu"
        write_json(cpu_wrong_torch["runtimeManifest"], manifest)
        code, report, _ = invoke(tool, cpu_wrong_torch, root / "cpu-wrong-torch-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "CPU wrong Torch did not block")
        require(
            "RVC_PROVIDER_TORCH_VERSION_MISMATCH" in (report.get("blockers") or []),
            "Missing CPU Torch version blocker",
        )
        results["cpuTorchVersionMismatch"] = report.get("blockers")

        cuda_wrong_torch = cloned_fixture(root, "cuda wrong torch", provider="cuda")
        manifest = load_json(cuda_wrong_torch["runtimeManifest"])
        manifest["torch"]["version"] = "2.4.1+cpu"
        manifest["torch"]["cudaAvailable"] = False
        manifest["torch"]["cudaVersion"] = None
        write_json(cuda_wrong_torch["runtimeManifest"], manifest)
        code, report, _ = invoke(tool, cuda_wrong_torch, root / "cuda-wrong-torch-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "CUDA wrong Torch did not block")
        blockers = report.get("blockers") or []
        require("RVC_PROVIDER_TORCH_VERSION_MISMATCH" in blockers, "Missing CUDA Torch version blocker")
        require("RVC_CUDA_TRUTH_INCONSISTENT" in blockers, "Missing CUDA truth blocker")
        results["cudaTorchVersionMismatch"] = blockers

        not_ready = cloned_fixture(root, "input not ready")
        manifest = load_json(not_ready["inputManifest"])
        manifest["status"] = "BLOCKED"
        write_json(not_ready["inputManifest"], manifest)
        code, report, _ = invoke(tool, not_ready, root / "not-ready-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "Non-ready input did not block")
        require("RVC_INPUT_NOT_READY" in (report.get("blockers") or []), "Missing non-ready blocker")
        results["inputNotReady"] = report.get("blockers")

        vocal_tamper = cloned_fixture(root, "tampered vocal")
        vocal_tamper["vocalAudio"].write_bytes(b"tampered-vocal-after-approval")
        code, report, _ = invoke(tool, vocal_tamper, root / "vocal-tamper-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "Tampered vocal did not block")
        require("VOCALINPUT_SHA256_MISMATCH" in (report.get("blockers") or []), "Missing vocal tamper blocker")
        results["tamperedVocal"] = report.get("blockers")

        commit_drift = cloned_fixture(root, "upstream commit drift")
        manifest = load_json(commit_drift["runtimeManifest"])
        manifest["upstream"]["commit"] = "0" * 40
        write_json(commit_drift["runtimeManifest"], manifest)
        code, report, _ = invoke(tool, commit_drift, root / "commit-drift-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "Commit drift did not block")
        require("RVC_UPSTREAM_COMMIT_DRIFT" in (report.get("blockers") or []), "Missing commit drift blocker")
        results["commitDrift"] = report.get("blockers")

        model_tamper = cloned_fixture(root, "tampered model")
        model_tamper["modelFile"].write_bytes(b"tampered-model-after-registration")
        code, report, _ = invoke(tool, model_tamper, root / "model-tamper-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "Tampered model did not block")
        require("VOICEMODEL_SHA256_MISMATCH" in (report.get("blockers") or []), "Missing model tamper blocker")
        results["tamperedModel"] = report.get("blockers")

        unauthorized = cloned_fixture(root, "unauthorized voice")
        manifest = load_json(unauthorized["modelManifest"])
        manifest["authorization"]["ownerConsentVerified"] = False
        write_json(unauthorized["modelManifest"], manifest)
        code, report, _ = invoke(tool, unauthorized, root / "unauthorized-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "Unauthorized voice did not block")
        require("VOICE_OWNER_CONSENT_NOT_VERIFIED" in (report.get("blockers") or []), "Missing consent blocker")
        results["unauthorizedVoice"] = report.get("blockers")

        source_file_tamper = cloned_fixture(root, "runtime source file tamper")
        (source_file_tamper["sourceCheckout"] / "webui.py").write_text("tampered runtime source\n", encoding="utf-8")
        code, report, _ = invoke(tool, source_file_tamper, root / "source-file-tamper-report.json")
        require(code == 2 and report.get("status") == "BLOCKED", "Runtime source tamper did not block")
        require(
            any(str(item).endswith("_SHA256_MISMATCH") and "RUNTIMEFILE" in str(item) for item in report.get("blockers") or []),
            "Missing runtime source tamper blocker",
        )
        results["runtimeSourceTamper"] = report.get("blockers")

    proof = {
        "schema": "echoes.rvc-runtime-admission-contract-proof.v1",
        "status": "PASS",
        "pinnedRepository": PINNED_REPOSITORY,
        "pinnedCommit": PINNED_COMMIT,
        "cases": results,
        "truthBoundary": {
            "admissionContractProven": True,
            "productionRuntimeInstalled": False,
            "hpOmenRuntimeInstalled": False,
            "kamDridiVoiceModelVerified": False,
            "cudaInferenceProven": False,
            "cpuInferenceProven": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "executionAuthorized": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, proof)
    print(
        "EchoesRvcRuntimeAdmissionContract PASS "
        f"cases={len(results)} commit={PINNED_COMMIT} inference=false conversion=false execution=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
