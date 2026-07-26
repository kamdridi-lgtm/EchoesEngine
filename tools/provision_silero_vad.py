#!/usr/bin/env python3
"""Provision and inspect the official Silero VAD ONNX model fail-closed."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import onnx

PACKAGE = "silero-vad"
VERSION = "6.2.1"
PYPI_JSON = f"https://pypi.org/pypi/{PACKAGE}/{VERSION}/json"
WHEEL_FILENAME = "silero_vad-6.2.1-py3-none-any.whl"
WHEEL_SHA256 = "09de93c4d874bb19c53e62a47dd38be5f163cedad2b5599583231f2a84ef79cb"
SCHEMA = "echoes.silero-vad-provisioning.v1"
PLACEHOLDER = "PIN_AFTER_DISCOVERY"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tensor_shape(value_info: onnx.ValueInfoProto) -> list[int | str | None]:
    tensor = value_info.type.tensor_type
    result: list[int | str | None] = []
    for dimension in tensor.shape.dim:
        if dimension.HasField("dim_value"):
            result.append(int(dimension.dim_value))
        elif dimension.HasField("dim_param"):
            result.append(str(dimension.dim_param))
        else:
            result.append(None)
    return result


def tensor_record(value_info: onnx.ValueInfoProto) -> dict[str, Any]:
    tensor = value_info.type.tensor_type
    return {
        "name": value_info.name,
        "elementType": int(tensor.elem_type),
        "shape": tensor_shape(value_info),
    }


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "EchoesEngine/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "EchoesEngine/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def choose_model(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    priorities = (
        "silero_vad.onnx",
        "silero_vad_16k_op16.onnx",
        "silero_vad_16k_op15.onnx",
        "silero_vad_16k_op15_ifless.onnx",
    )
    for filename in priorities:
        for candidate in candidates:
            if Path(candidate["archivePath"]).name == filename:
                return candidate
    if not candidates:
        raise RuntimeError("No ONNX model was present in the verified wheel")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-model-sha256", default=PLACEHOLDER)
    parser.add_argument("--expected-model-size", type=int, default=0)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = output_dir / WHEEL_FILENAME
    extracted_root = output_dir / "wheel"
    selected_path = output_dir / "model" / "silero_vad.onnx"
    manifest_path = output_dir / "silero-vad-provisioning.json"

    metadata = fetch_json(PYPI_JSON)
    releases = metadata.get("urls", [])
    wheel_entry = next((item for item in releases if item.get("filename") == WHEEL_FILENAME), None)
    if wheel_entry is None:
        raise RuntimeError(f"Pinned wheel {WHEEL_FILENAME} is absent from PyPI metadata")
    metadata_sha = str(wheel_entry.get("digests", {}).get("sha256", "")).lower()
    if metadata_sha != WHEEL_SHA256:
        raise RuntimeError(f"PyPI metadata digest drift: expected {WHEEL_SHA256}, actual {metadata_sha}")

    wheel_bytes = download(str(wheel_entry["url"]))
    wheel_sha = sha256_bytes(wheel_bytes)
    if wheel_sha != WHEEL_SHA256:
        raise RuntimeError(f"Wheel digest mismatch: expected {WHEEL_SHA256}, actual {wheel_sha}")
    wheel_path.write_bytes(wheel_bytes)

    if extracted_root.exists():
        shutil.rmtree(extracted_root)
    extracted_root.mkdir(parents=True)

    candidates: list[dict[str, Any]] = []
    licence_entries: list[str] = []
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(extracted_root)
        for member in archive.infolist():
            lower = member.filename.lower()
            if "license" in lower and not member.is_dir():
                licence_entries.append(member.filename)
            if not lower.endswith(".onnx") or member.is_dir():
                continue
            payload = archive.read(member.filename)
            model = onnx.load_model_from_string(payload)
            onnx.checker.check_model(model)
            initializer_names = {value.name for value in model.graph.initializer}
            runtime_inputs = [value for value in model.graph.input if value.name not in initializer_names]
            candidates.append(
                {
                    "archivePath": member.filename,
                    "filename": Path(member.filename).name,
                    "sha256": sha256_bytes(payload),
                    "sizeBytes": len(payload),
                    "irVersion": int(model.ir_version),
                    "opsets": [
                        {"domain": item.domain, "version": int(item.version)}
                        for item in model.opset_import
                    ],
                    "inputs": [tensor_record(value) for value in runtime_inputs],
                    "outputs": [tensor_record(value) for value in model.graph.output],
                }
            )

    candidates.sort(key=lambda item: item["archivePath"])
    selected = choose_model(candidates)
    selected_payload = (extracted_root / selected["archivePath"]).read_bytes()
    selected_path.parent.mkdir(parents=True, exist_ok=True)
    selected_path.write_bytes(selected_payload)

    expected_sha = args.expected_model_sha256.strip().lower()
    expected_size = int(args.expected_model_size)
    pinned = expected_sha != PLACEHOLDER.lower() and expected_size > 0
    blockers: list[str] = []
    if pinned and selected["sha256"] != expected_sha:
        blockers.append("MODEL_SHA256_MISMATCH")
    if pinned and selected["sizeBytes"] != expected_size:
        blockers.append("MODEL_SIZE_MISMATCH")
    if not licence_entries:
        blockers.append("LICENCE_FILE_MISSING")

    manifest = {
        "schema": SCHEMA,
        "status": "PASS" if pinned and not blockers else "DISCOVERY",
        "package": {
            "name": PACKAGE,
            "version": VERSION,
            "filename": WHEEL_FILENAME,
            "sha256": wheel_sha,
            "source": "PyPI JSON API",
        },
        "licence": {
            "declared": "MIT",
            "entries": sorted(licence_entries),
            "commercialUseAllowed": True,
        },
        "selectedModel": selected,
        "allOnnxModels": candidates,
        "selectedOutputPath": selected_path.as_posix(),
        "networkRequested": True,
        "executableLoaded": False,
        "inferenceExecuted": False,
        "blockers": blockers,
        "truthBoundary": {
            "productionPackageProvisioned": pinned and not blockers,
            "productionModelIntegrityProven": pinned and not blockers,
            "productionModelInferenceProven": False,
            "voiceActivityDetectionProven": False,
            "voiceConversionProven": False,
            "gpuInferenceProven": False,
            "tensorRtInferenceProven": False,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, separators=(",", ":")))

    if expected_sha == PLACEHOLDER.lower() or expected_size <= 0:
        print(
            f"SILERO_MODEL_DISCOVERY sha256={selected['sha256']} size={selected['sizeBytes']} "
            f"file={selected['filename']}",
            file=sys.stderr,
        )
        return 3
    if blockers:
        print("Silero VAD provisioning blocked: " + ",".join(blockers), file=sys.stderr)
        return 2
    print(
        f"EchoesSileroVadProvisioning PASS package={wheel_sha} "
        f"model={selected['sha256']} size={selected['sizeBytes']} licence=MIT inference=not-run"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
