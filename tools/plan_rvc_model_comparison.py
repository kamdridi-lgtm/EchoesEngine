#!/usr/bin/env python3
"""Create a fail-closed RVC A/B/C comparison plan without running inference."""
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

OUTPUT_SCHEMA = "echoes.rvc-model-comparison-plan.v1"
INPUT_SCHEMA = "echoes.rvc-input-manifest.v1"
MODEL_SCHEMA = "echoes.rvc-voice-model.v1"
REQUIRED_LABELS = ("700", "1000", "1500")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def parse_model_argument(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    label = label.strip()
    require(bool(separator) and label in REQUIRED_LABELS and raw_path.strip(), f"Invalid model argument: {value}")
    return label, Path(raw_path.strip()).resolve()


def validate_input_manifest(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"RVC input manifest is missing: {path}")
    manifest = load_json(path)
    require(manifest.get("schema") == INPUT_SCHEMA, "RVC input manifest schema is invalid")
    require(manifest.get("status") == "READY", "RVC input manifest must be READY")
    require(manifest.get("declaredUserSong") is True, "RVC input must be declared as a user-owned song")

    vocal = manifest.get("vocalInput") if isinstance(manifest.get("vocalInput"), dict) else {}
    vocal_path = Path(str(vocal.get("path") or "")).resolve()
    vocal_sha = normalized_sha(vocal.get("sha256"))
    require(vocal_path.is_file(), "Approved vocal input file is missing")
    require(bool(vocal_sha), "Approved vocal input SHA-256 is invalid")
    require(sha256_file(vocal_path) == vocal_sha, "Approved vocal input SHA-256 mismatch")

    truth = manifest.get("truthBoundary") if isinstance(manifest.get("truthBoundary"), dict) else {}
    require(truth.get("rvcInputManifestPrepared") is True, "RVC input preparation is not proven")
    require(truth.get("approvedListeningReviewVerified") is True, "Approved listening review is not proven")
    require(truth.get("sourceAndVocalHashesVerified") is True, "Input hashes are not proven")
    require(truth.get("voiceConversionProven") is False, "Input manifest falsely claims conversion")

    return {
        "manifest": manifest,
        "path": path,
        "sha256": sha256_file(path),
        "vocalPath": vocal_path,
        "vocalSha256": vocal_sha,
    }


def validate_model_manifest(label: str, path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Voice-model manifest is missing for {label}: {path}")
    manifest = load_json(path)
    require(manifest.get("schema") == MODEL_SCHEMA, f"Voice-model schema is invalid for {label}")
    require(manifest.get("status") == "VERIFIED", f"Voice model {label} must be VERIFIED")

    owner = str(manifest.get("voiceOwner") or "").strip()
    runtime_root = str(manifest.get("runtimeRoot") or "").strip()
    require(bool(owner), f"Voice owner is missing for {label}")
    require(bool(runtime_root), f"Runtime root is missing for {label}")

    authorization = manifest.get("authorization") if isinstance(manifest.get("authorization"), dict) else {}
    require(authorization.get("userOwnedModelDeclared") is True, f"User ownership is not declared for {label}")
    require(authorization.get("ownerConsentVerified") is True, f"Owner consent is not verified for {label}")
    require(authorization.get("voiceConversionAuthorized") is True, f"Voice conversion is not authorized for {label}")
    require(
        authorization.get("thirdPartyImpersonationAllowed") is False,
        f"Third-party impersonation policy drifted for {label}",
    )

    model = manifest.get("model") if isinstance(manifest.get("model"), dict) else {}
    model_path = Path(str(model.get("path") or "")).resolve()
    model_sha = normalized_sha(model.get("sha256"))
    require(model_path.is_file(), f"Registered model file is missing for {label}")
    require(bool(model_sha), f"Registered model SHA-256 is invalid for {label}")
    require(sha256_file(model_path) == model_sha, f"Registered model SHA-256 mismatch for {label}")

    index = manifest.get("index") if isinstance(manifest.get("index"), dict) else {}
    index_path = Path(str(index.get("path") or "")).resolve()
    index_sha = normalized_sha(index.get("sha256"))
    require(index_path.is_file(), f"Registered index file is missing for {label}")
    require(bool(index_sha), f"Registered index SHA-256 is invalid for {label}")
    require(sha256_file(index_path) == index_sha, f"Registered index SHA-256 mismatch for {label}")

    truth = manifest.get("truthBoundary") if isinstance(manifest.get("truthBoundary"), dict) else {}
    require(truth.get("integrityVerified") is True, f"Model integrity is not proven for {label}")
    require(truth.get("userOwnedVoice") is True, f"User-owned voice is not proven for {label}")
    require(truth.get("ownerConsentVerified") is True, f"Consent truth boundary is false for {label}")
    require(truth.get("voiceConversionProven") is False, f"Model manifest falsely claims conversion for {label}")

    return {
        "label": label,
        "manifestPath": path,
        "manifestSha256": sha256_file(path),
        "owner": owner,
        "runtimeRoot": str(Path(runtime_root).resolve()),
        "modelName": str(manifest.get("modelName") or model_path.stem),
        "modelPath": model_path,
        "modelSha256": model_sha,
        "indexPath": index_path,
        "indexSha256": index_sha,
    }


def safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return stem or "approved-vocals"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-manifest", type=Path, required=True)
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        help="Repeat exactly three times as 700=manifest.json, 1000=manifest.json and 1500=manifest.json",
    )
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--f0-method", default="rmvpe")
    parser.add_argument("--pitch-shift", type=int, default=0)
    args = parser.parse_args()

    require(len(args.model) == 3, "Exactly three model manifests are required")
    parsed = [parse_model_argument(value) for value in args.model]
    require(len({label for label, _ in parsed}) == 3, "Model labels must be unique")
    require(set(label for label, _ in parsed) == set(REQUIRED_LABELS), "Required model labels are 700,1000,1500")
    require(args.pitch_shift == 0, "Comparison pitch shift must remain zero")
    require(args.f0_method.strip().lower() == "rmvpe", "Comparison F0 method must remain rmvpe")

    input_record = validate_input_manifest(args.input_manifest.resolve())
    models = [validate_model_manifest(label, path) for label, path in parsed]
    models.sort(key=lambda item: REQUIRED_LABELS.index(item["label"]))

    owners = {item["owner"] for item in models}
    runtimes = {item["runtimeRoot"] for item in models}
    index_hashes = {item["indexSha256"] for item in models}
    model_hashes = {item["modelSha256"] for item in models}
    require(len(owners) == 1, "All comparison models must have the same voice owner")
    require(len(runtimes) == 1, "All comparison models must use the same managed runtime")
    require(len(index_hashes) == 1, "All comparison models must use the same index bytes")
    require(len(model_hashes) == 3, "Comparison models must be three distinct checkpoint files")

    output_directory = args.output_directory.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    input_name = safe_stem(input_record["vocalPath"].stem)

    fixed_parameters = {
        "f0Method": "rmvpe",
        "pitchShiftSemitones": 0,
        "sameInputRequired": True,
        "sameIndexRequired": True,
        "sameRuntimeRequired": True,
        "sameOutputFormat": "wav",
        "effectsAllowed": False,
        "masteringAllowed": False,
        "instrumentalMixAllowed": False,
    }

    runs = []
    for order, item in enumerate(models, start=1):
        runs.append(
            {
                "order": order,
                "label": item["label"],
                "modelName": item["modelName"],
                "modelPath": str(item["modelPath"]),
                "modelSha256": item["modelSha256"],
                "modelManifestPath": str(item["manifestPath"]),
                "modelManifestSha256": item["manifestSha256"],
                "indexPath": str(item["indexPath"]),
                "indexSha256": item["indexSha256"],
                "inputPath": str(input_record["vocalPath"]),
                "inputSha256": input_record["vocalSha256"],
                "outputPath": str(output_directory / f"{input_name}_RVC_{item['label']}E.wav"),
                "parameters": fixed_parameters,
                "status": "PLANNED",
            }
        )

    result = {
        "schema": OUTPUT_SCHEMA,
        "version": "1.0.0",
        "status": "READY",
        "plannedAtUtc": utc_now(),
        "voiceOwner": next(iter(owners)),
        "runtimeRoot": next(iter(runtimes)),
        "input": {
            "manifestPath": str(input_record["path"]),
            "manifestSha256": input_record["sha256"],
            "vocalPath": str(input_record["vocalPath"]),
            "vocalSha256": input_record["vocalSha256"],
        },
        "comparison": {
            "labels": list(REQUIRED_LABELS),
            "fixedParameters": fixed_parameters,
            "runs": runs,
        },
        "truthBoundary": {
            "threeDistinctModelsVerified": True,
            "sharedIndexVerified": True,
            "sharedInputVerified": True,
            "identicalParametersPlanned": True,
            "modelDeserializationAttempted": False,
            "voiceModelLoadProven": False,
            "indexLoadProven": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "audioUploaded": False,
            "executionAuthorized": False,
        },
    }
    write_json_atomic(args.output.resolve(), result)
    print(
        "EchoesRvcModelComparisonPlan READY "
        "models=700,1000,1500 shared_input=true shared_index=true inference=false conversion=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"EchoesRvcModelComparisonPlan BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
