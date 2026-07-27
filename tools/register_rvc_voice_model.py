#!/usr/bin/env python3
"""Register a user-owned RVC model without deserializing or executing it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTPUT_SCHEMA = "echoes.rvc-voice-model.v1"
PINNED_RUNTIME_REPOSITORY = "RVC-Project/Retrieval-based-Voice-Conversion-WebUI"
PINNED_RUNTIME_COMMIT = "4338f12c3c28c80b3ac015e2d0df66c41592746d"
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


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


def safe_filename(name: str, required_suffix: str) -> str:
    candidate = SAFE_NAME.sub("-", Path(name).name).strip(".-")
    if not candidate:
        candidate = f"registered-model{required_suffix}"
    if Path(candidate).suffix.lower() != required_suffix:
        candidate = f"{Path(candidate).stem}{required_suffix}"
    return candidate


def atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp-{os.getpid()}")
    shutil.copyfile(source, temporary)
    temporary.replace(target)


def validate_runtime(runtime_root: Path, runtime: dict[str, Any]) -> Path:
    if runtime.get("schema") != "echoes.rvc-runtime-installation.v1":
        raise RuntimeError("RVC runtime manifest schema is invalid")
    if runtime.get("status") != "PASS":
        raise RuntimeError("RVC runtime must be PASS before model registration")
    if Path(str(runtime.get("installRoot") or "")).resolve() != runtime_root.resolve():
        raise RuntimeError("Runtime root does not match runtime manifest")
    upstream = runtime.get("upstream") if isinstance(runtime.get("upstream"), dict) else {}
    if upstream.get("repository") != PINNED_RUNTIME_REPOSITORY:
        raise RuntimeError("RVC runtime repository drifted")
    if upstream.get("commit") != PINNED_RUNTIME_COMMIT:
        raise RuntimeError("RVC runtime commit drifted")
    source_root = Path(str((runtime.get("sourceCheckout") or {}).get("root") or "")).resolve()
    if not source_root.is_dir() or not is_within(source_root, runtime_root):
        raise RuntimeError("RVC source checkout is missing or unmanaged")
    return source_root


def validate_assets(runtime_root: Path, assets: dict[str, Any]) -> None:
    if assets.get("schema") != "echoes.rvc-core-assets.v1":
        raise RuntimeError("RVC core-assets manifest schema is invalid")
    if assets.get("status") != "VERIFIED":
        raise RuntimeError("RVC core assets must be VERIFIED before model registration")
    if Path(str(assets.get("runtimeRoot") or "")).resolve() != runtime_root.resolve():
        raise RuntimeError("RVC core-assets manifest points to a different runtime")
    for entry in assets.get("assets") or []:
        if not isinstance(entry, dict):
            raise RuntimeError("RVC core-assets manifest contains an invalid entry")
        path = Path(str(entry.get("path") or ""))
        expected = str(entry.get("sha256") or "").lower()
        if not path.is_file() or len(expected) != 64 or sha256_file(path) != expected:
            raise RuntimeError(f"RVC core asset integrity failed: {entry.get('id')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path, required=True)
    parser.add_argument("--core-assets-manifest", type=Path, required=True)
    parser.add_argument("--model-file", type=Path, required=True)
    parser.add_argument("--index-file", type=Path)
    parser.add_argument("--voice-owner", required=True)
    parser.add_argument("--model-name")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declare-user-owned-model", action="store_true")
    parser.add_argument("--confirm-owner-consent", action="store_true")
    parser.add_argument("--authorize-voice-conversion", action="store_true")
    parser.add_argument("--forbid-third-party-impersonation", action="store_true")
    args = parser.parse_args()

    runtime_root = args.runtime_root.resolve()
    runtime_manifest_path = args.runtime_manifest.resolve()
    assets_manifest_path = args.core_assets_manifest.resolve()
    model_input = args.model_file.resolve()
    index_input = args.index_file.resolve() if args.index_file else None
    output_path = args.output.resolve()

    blockers: list[str] = []
    if not args.declare_user_owned_model:
        blockers.append("USER_OWNED_MODEL_DECLARATION_MISSING")
    if not args.confirm_owner_consent:
        blockers.append("VOICE_OWNER_CONSENT_NOT_CONFIRMED")
    if not args.authorize_voice_conversion:
        blockers.append("VOICE_CONVERSION_NOT_AUTHORIZED")
    if not args.forbid_third_party_impersonation:
        blockers.append("THIRD_PARTY_IMPERSONATION_POLICY_MISSING")
    owner = args.voice_owner.strip()
    if not owner:
        blockers.append("VOICE_OWNER_MISSING")
    if blockers:
        raise RuntimeError(",".join(blockers))

    if not runtime_manifest_path.is_file() or not assets_manifest_path.is_file():
        raise RuntimeError("Runtime or core-assets manifest is missing")
    if not model_input.is_file() or model_input.suffix.lower() != ".pth":
        raise RuntimeError("RVC model must be an existing .pth file")
    if model_input.stat().st_size < 1_000_000:
        raise RuntimeError("RVC model file is too small to register")
    if index_input is not None:
        if not index_input.is_file() or index_input.suffix.lower() != ".index":
            raise RuntimeError("RVC index must be an existing .index file")
        if index_input.stat().st_size < 1_024:
            raise RuntimeError("RVC index file is too small to register")

    runtime = load_json(runtime_manifest_path)
    core_assets = load_json(assets_manifest_path)
    source_root = validate_runtime(runtime_root, runtime)
    validate_assets(runtime_root, core_assets)

    requested_name = (args.model_name or model_input.stem).strip()
    model_filename = safe_filename(f"{requested_name}.pth", ".pth")
    model_target = (source_root / "assets" / "weights" / model_filename).resolve()
    if not is_within(model_target, source_root / "assets" / "weights"):
        raise RuntimeError("Managed model target escaped the weights directory")
    atomic_copy(model_input, model_target)

    index_record: dict[str, Any] | None = None
    if index_input is not None:
        index_filename = safe_filename(f"{requested_name}.index", ".index")
        index_target = (source_root / "assets" / "indices" / index_filename).resolve()
        if not is_within(index_target, source_root / "assets" / "indices"):
            raise RuntimeError("Managed index target escaped the indices directory")
        atomic_copy(index_input, index_target)
        index_record = {
            "path": str(index_target),
            "name": index_target.name,
            "sha256": sha256_file(index_target),
            "sizeBytes": index_target.stat().st_size,
        }

    result = {
        "schema": OUTPUT_SCHEMA,
        "version": "1.0.0",
        "status": "VERIFIED",
        "registeredAtUtc": utc_now(),
        "runtimeRoot": str(runtime_root),
        "voiceOwner": owner,
        "modelName": requested_name,
        "authorization": {
            "userOwnedModelDeclared": True,
            "ownerConsentVerified": True,
            "voiceConversionAuthorized": True,
            "thirdPartyImpersonationAllowed": False,
        },
        "model": {
            "path": str(model_target),
            "name": model_target.name,
            "format": "rvc-pytorch-checkpoint",
            "sha256": sha256_file(model_target),
            "sizeBytes": model_target.stat().st_size,
        },
        "index": index_record,
        "evidence": {
            "runtimeManifestPath": str(runtime_manifest_path),
            "runtimeManifestSha256": sha256_file(runtime_manifest_path),
            "coreAssetsManifestPath": str(assets_manifest_path),
            "coreAssetsManifestSha256": sha256_file(assets_manifest_path),
        },
        "truthBoundary": {
            "integrityVerified": True,
            "userOwnedVoice": True,
            "ownerConsentVerified": True,
            "modelCopiedIntoManagedRuntime": True,
            "modelDeserializationAttempted": False,
            "modelLoadProven": False,
            "indexLoadProven": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "audioUploaded": False,
            "executionAuthorized": False,
        },
    }
    write_json_atomic(output_path, result)
    print(
        "EchoesRvcVoiceModel VERIFIED "
        f"owner={owner!r} model={model_target.name} index={bool(index_record)} "
        "deserialized=false inference=false conversion=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"EchoesRvcVoiceModel BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
