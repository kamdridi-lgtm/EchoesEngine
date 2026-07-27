#!/usr/bin/env python3
"""Admit, but never execute, an Echoes RVC conversion.

The gate verifies a READY vocal-input manifest, a pinned local RVC runtime
installation and an explicitly authorized voice model. It emits evidence only:
it does not import RVC, start a server, issue a conversion command or write audio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OUTPUT_SCHEMA = "echoes.rvc-conversion-admission.v1"
PINNED_REPOSITORY = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
PINNED_COMMIT = "4338f12c3c28c80b3ac015e2d0df66c41592746d"
REQUIRED_SOURCE_FILES = (
    "README.md",
    "LICENSE",
    "webui.py",
    "requirments_cu118_py312.txt",
    "requirments_cpu_py312.txt",
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


def normalized_sha(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if SHA256_PATTERN.fullmatch(candidate) else ""


def add_blocker(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def verify_hashed_file(
    checks: dict[str, bool],
    blockers: list[str],
    prefix: str,
    path: Path,
    expected_sha: str,
) -> None:
    checks[f"{prefix}Present"] = path.is_file()
    checks[f"{prefix}ShaRecorded"] = bool(expected_sha)
    checks[f"{prefix}ShaVerified"] = path.is_file() and bool(expected_sha) and sha256_file(path) == expected_sha
    if not checks[f"{prefix}Present"]:
        add_blocker(blockers, f"{prefix.upper()}_MISSING")
    if not checks[f"{prefix}ShaRecorded"]:
        add_blocker(blockers, f"{prefix.upper()}_SHA256_NOT_RECORDED")
    if not checks[f"{prefix}ShaVerified"]:
        add_blocker(blockers, f"{prefix.upper()}_SHA256_MISMATCH")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--voice-model-manifest", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declare-user-song", action="store_true")
    args = parser.parse_args()

    input_path = args.input_manifest.resolve()
    runtime_path = args.runtime_manifest.resolve()
    model_manifest_path = args.voice_model_manifest.resolve()
    output_path = args.output.resolve()
    expected_source_sha = normalized_sha(args.expected_source_sha256)
    blockers: list[str] = []
    checks: dict[str, bool] = {}

    for path, blocker in (
        (input_path, "RVC_INPUT_MANIFEST_MISSING"),
        (runtime_path, "RVC_RUNTIME_MANIFEST_MISSING"),
        (model_manifest_path, "RVC_VOICE_MODEL_MANIFEST_MISSING"),
    ):
        if not path.is_file():
            add_blocker(blockers, blocker)
    if not expected_source_sha:
        add_blocker(blockers, "EXPECTED_SOURCE_SHA256_INVALID")

    input_manifest = load_json(input_path) if input_path.is_file() else {}
    runtime_manifest = load_json(runtime_path) if runtime_path.is_file() else {}
    model_manifest = load_json(model_manifest_path) if model_manifest_path.is_file() else {}

    checks["inputSchemaValid"] = input_manifest.get("schema") == "echoes.rvc-input-manifest.v1"
    checks["inputReady"] = input_manifest.get("status") == "READY"
    input_truth = input_manifest.get("truthBoundary") if isinstance(input_manifest.get("truthBoundary"), dict) else {}
    checks["inputPrepared"] = input_truth.get("rvcInputManifestPrepared") is True
    checks["inputReviewVerified"] = input_truth.get("approvedListeningReviewVerified") is True
    checks["inputHashesVerified"] = input_truth.get("sourceAndVocalHashesVerified") is True
    checks["inputDoesNotClaimRuntime"] = input_truth.get("rvcRuntimeProven") is False
    checks["inputDoesNotClaimConversion"] = input_truth.get("voiceConversionProven") is False
    for blocker, condition in {
        "RVC_INPUT_SCHEMA_INVALID": checks["inputSchemaValid"],
        "RVC_INPUT_NOT_READY": checks["inputReady"],
        "RVC_INPUT_NOT_PREPARED": checks["inputPrepared"],
        "RVC_INPUT_REVIEW_NOT_VERIFIED": checks["inputReviewVerified"],
        "RVC_INPUT_HASHES_NOT_VERIFIED": checks["inputHashesVerified"],
        "RVC_INPUT_FALSELY_CLAIMS_RUNTIME": checks["inputDoesNotClaimRuntime"],
        "RVC_INPUT_FALSELY_CLAIMS_CONVERSION": checks["inputDoesNotClaimConversion"],
    }.items():
        if not condition:
            add_blocker(blockers, blocker)

    source = input_manifest.get("source") if isinstance(input_manifest.get("source"), dict) else {}
    vocal = input_manifest.get("vocalInput") if isinstance(input_manifest.get("vocalInput"), dict) else {}
    source_file = Path(str(source.get("path") or ""))
    vocal_file = Path(str(vocal.get("path") or ""))
    source_sha = normalized_sha(source.get("sha256"))
    vocal_sha = normalized_sha(vocal.get("sha256"))
    checks["sourceMatchesExpected"] = bool(source_sha) and source_sha == expected_source_sha
    if not checks["sourceMatchesExpected"]:
        add_blocker(blockers, "SOURCE_SHA256_EXPECTATION_MISMATCH")
    verify_hashed_file(checks, blockers, "sourceAudio", source_file, source_sha)
    verify_hashed_file(checks, blockers, "vocalInput", vocal_file, vocal_sha)

    checks["runtimeSchemaValid"] = runtime_manifest.get("schema") == "echoes.rvc-runtime-installation.v1"
    checks["runtimePass"] = runtime_manifest.get("status") == "PASS"
    upstream = runtime_manifest.get("upstream") if isinstance(runtime_manifest.get("upstream"), dict) else {}
    checks["upstreamRepositoryPinned"] = upstream.get("repository") == PINNED_REPOSITORY
    checks["upstreamCommitPinned"] = upstream.get("commit") == PINNED_COMMIT
    checks["upstreamLicenseMit"] = upstream.get("license") == "MIT"
    runtime_root = Path(str(runtime_manifest.get("installRoot") or ""))
    source_checkout = Path(str((runtime_manifest.get("sourceCheckout") or {}).get("root") or ""))
    checks["runtimeRootPresent"] = runtime_root.is_dir()
    checks["sourceCheckoutPresent"] = source_checkout.is_dir()
    checks["sourceCheckoutInsideRuntime"] = source_checkout.is_dir() and runtime_root.is_dir() and is_within(source_checkout, runtime_root)

    python_info = runtime_manifest.get("python") if isinstance(runtime_manifest.get("python"), dict) else {}
    torch_info = runtime_manifest.get("torch") if isinstance(runtime_manifest.get("torch"), dict) else {}
    provider = str(runtime_manifest.get("provider") or "").lower()
    torch_version = str(torch_info.get("version") or "")
    cuda_available = torch_info.get("cudaAvailable") is True
    cuda_version = str(torch_info.get("cudaVersion") or "")
    checks["python312"] = str(python_info.get("version") or "").startswith("3.12.")
    checks["providerSupported"] = provider in {"cpu", "cuda"}
    checks["providerTorchVersionValid"] = (
        (provider == "cuda" and torch_version.startswith("2.7.1"))
        or (provider == "cpu" and torch_version.startswith("2.4.1"))
    )
    checks["cudaTruthConsistent"] = provider != "cuda" or (cuda_available and cuda_version.startswith("11.8"))
    checks["cpuTruthConsistent"] = provider != "cpu" or (not cuda_available and not cuda_version)
    checks["cpuFallbackRecorded"] = runtime_manifest.get("cpuFallback") is True
    runtime_truth = runtime_manifest.get("truthBoundary") if isinstance(runtime_manifest.get("truthBoundary"), dict) else {}
    checks["runtimeNoConversionClaim"] = runtime_truth.get("voiceConversionProven") is False
    checks["runtimeNoAudioOutputClaim"] = runtime_truth.get("convertedAudioGenerated") is False

    for blocker, condition in {
        "RVC_RUNTIME_SCHEMA_INVALID": checks["runtimeSchemaValid"],
        "RVC_RUNTIME_STATUS_NOT_PASS": checks["runtimePass"],
        "RVC_UPSTREAM_REPOSITORY_DRIFT": checks["upstreamRepositoryPinned"],
        "RVC_UPSTREAM_COMMIT_DRIFT": checks["upstreamCommitPinned"],
        "RVC_UPSTREAM_LICENSE_NOT_MIT": checks["upstreamLicenseMit"],
        "RVC_RUNTIME_ROOT_MISSING": checks["runtimeRootPresent"],
        "RVC_SOURCE_CHECKOUT_MISSING": checks["sourceCheckoutPresent"],
        "RVC_SOURCE_CHECKOUT_OUTSIDE_RUNTIME": checks["sourceCheckoutInsideRuntime"],
        "RVC_PYTHON_312_NOT_PROVEN": checks["python312"],
        "RVC_PROVIDER_UNSUPPORTED": checks["providerSupported"],
        "RVC_PROVIDER_TORCH_VERSION_MISMATCH": checks["providerTorchVersionValid"],
        "RVC_CUDA_TRUTH_INCONSISTENT": checks["cudaTruthConsistent"],
        "RVC_CPU_TRUTH_INCONSISTENT": checks["cpuTruthConsistent"],
        "RVC_CPU_FALLBACK_NOT_RECORDED": checks["cpuFallbackRecorded"],
        "RVC_RUNTIME_FALSELY_CLAIMS_CONVERSION": checks["runtimeNoConversionClaim"],
        "RVC_RUNTIME_FALSELY_CLAIMS_AUDIO_OUTPUT": checks["runtimeNoAudioOutputClaim"],
    }.items():
        if not condition:
            add_blocker(blockers, blocker)

    installed_files = runtime_manifest.get("installedFiles") if isinstance(runtime_manifest.get("installedFiles"), list) else []
    installed_by_path = {
        str(item.get("relativePath")): item
        for item in installed_files
        if isinstance(item, dict) and item.get("relativePath")
    }
    for relative in REQUIRED_SOURCE_FILES:
        entry = installed_by_path.get(relative) if isinstance(installed_by_path.get(relative), dict) else {}
        file_path = source_checkout / relative
        file_sha = normalized_sha(entry.get("sha256"))
        key = "runtimeFile" + re.sub(r"[^A-Za-z0-9]", "", relative.title())
        checks[f"{key}Recorded"] = bool(entry)
        if not entry:
            add_blocker(blockers, f"RVC_REQUIRED_FILE_NOT_RECORDED:{relative}")
        verify_hashed_file(checks, blockers, key, file_path, file_sha)

    checks["modelManifestSchemaValid"] = model_manifest.get("schema") == "echoes.rvc-voice-model.v1"
    checks["modelManifestVerified"] = model_manifest.get("status") == "VERIFIED"
    owner = str(model_manifest.get("voiceOwner") or "").strip()
    authorization = model_manifest.get("authorization") if isinstance(model_manifest.get("authorization"), dict) else {}
    checks["voiceOwnerPresent"] = bool(owner)
    checks["voiceOwnerAuthorized"] = authorization.get("ownerConsentVerified") is True
    checks["conversionAuthorized"] = authorization.get("voiceConversionAuthorized") is True
    checks["thirdPartyImpersonationForbidden"] = authorization.get("thirdPartyImpersonationAllowed") is False
    model = model_manifest.get("model") if isinstance(model_manifest.get("model"), dict) else {}
    model_file = Path(str(model.get("path") or ""))
    model_sha = normalized_sha(model.get("sha256"))
    checks["modelInsideRuntimeWeights"] = runtime_root.is_dir() and is_within(model_file, runtime_root / "source" / "assets" / "weights")
    if not checks["modelInsideRuntimeWeights"]:
        add_blocker(blockers, "VOICE_MODEL_OUTSIDE_RUNTIME_WEIGHTS")
    verify_hashed_file(checks, blockers, "voiceModel", model_file, model_sha)

    index = model_manifest.get("index") if isinstance(model_manifest.get("index"), dict) else {}
    index_file: Path | None = None
    index_sha = normalized_sha(index.get("sha256"))
    if index.get("path"):
        index_file = Path(str(index.get("path")))
        checks["indexInsideRuntimeIndices"] = runtime_root.is_dir() and is_within(index_file, runtime_root / "source" / "assets" / "indices")
        if not checks["indexInsideRuntimeIndices"]:
            add_blocker(blockers, "VOICE_INDEX_OUTSIDE_RUNTIME_INDICES")
        verify_hashed_file(checks, blockers, "voiceIndex", index_file, index_sha)
    else:
        checks["indexInsideRuntimeIndices"] = True
        checks["voiceIndexPresent"] = True
        checks["voiceIndexShaRecorded"] = True
        checks["voiceIndexShaVerified"] = True

    for blocker, condition in {
        "VOICE_MODEL_MANIFEST_SCHEMA_INVALID": checks["modelManifestSchemaValid"],
        "VOICE_MODEL_MANIFEST_NOT_VERIFIED": checks["modelManifestVerified"],
        "VOICE_OWNER_MISSING": checks["voiceOwnerPresent"],
        "VOICE_OWNER_CONSENT_NOT_VERIFIED": checks["voiceOwnerAuthorized"],
        "VOICE_CONVERSION_NOT_AUTHORIZED": checks["conversionAuthorized"],
        "THIRD_PARTY_IMPERSONATION_POLICY_INVALID": checks["thirdPartyImpersonationForbidden"],
    }.items():
        if not condition:
            add_blocker(blockers, blocker)

    admitted = not blockers
    report = {
        "schema": OUTPUT_SCHEMA,
        "status": "ADMITTED" if admitted else "BLOCKED",
        "evaluatedAtUtc": utc_now(),
        "declaredUserSong": bool(args.declare_user_song),
        "input": {
            "manifestPath": str(input_path),
            "manifestSha256": sha256_file(input_path) if input_path.is_file() else None,
            "sourceSha256": source_sha or None,
            "vocalPath": str(vocal_file),
            "vocalSha256": vocal_sha or None,
        },
        "runtime": {
            "manifestPath": str(runtime_path),
            "manifestSha256": sha256_file(runtime_path) if runtime_path.is_file() else None,
            "installRoot": str(runtime_root),
            "sourceCheckout": str(source_checkout),
            "upstreamRepository": upstream.get("repository"),
            "upstreamCommit": upstream.get("commit"),
            "pythonVersion": python_info.get("version"),
            "torchVersion": torch_info.get("version"),
            "provider": provider or None,
            "cudaAvailable": torch_info.get("cudaAvailable"),
        },
        "voiceModel": {
            "manifestPath": str(model_manifest_path),
            "manifestSha256": sha256_file(model_manifest_path) if model_manifest_path.is_file() else None,
            "voiceOwner": owner or None,
            "modelPath": str(model_file),
            "modelSha256": model_sha or None,
            "indexPath": str(index_file) if index_file else None,
            "indexSha256": index_sha or None,
        },
        "checks": checks,
        "blockers": blockers,
        "truthBoundary": {
            "conversionAdmissionEvaluated": True,
            "conversionAdmitted": admitted,
            "preparedInputVerified": admitted,
            "runtimeInstallationEvidenceVerified": admitted,
            "voiceModelIntegrityVerified": admitted,
            "voiceOwnerAuthorizationVerified": admitted,
            "rvcProcessStarted": False,
            "conversionCommandIssued": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "audioUploaded": False,
            "executionAuthorized": False,
            "requiresOperatorApproval": True,
        },
    }
    write_json_atomic(output_path, report)
    print(
        f"EchoesRvcAdmission {report['status']} provider={provider or 'unknown'} "
        f"torch={torch_version or 'unknown'} blockers={len(blockers)} "
        f"execution=false conversion=false output={output_path}"
    )
    return 0 if admitted else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes RVC admission failed: {error}", file=sys.stderr)
        raise SystemExit(2)
