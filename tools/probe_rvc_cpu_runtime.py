#!/usr/bin/env python3
"""Probe a fully installed Echoes RVC CPU/DirectML dependency runtime.

This proves imports and a deterministic CPU tensor operation. It deliberately
loads no RVC voice model, performs no feature extraction and converts no audio.
"""
from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "echoes.rvc-cpu-runtime-dependency-proof.v1"
REQUIRED_IMPORTS = {
    "torch": "torch",
    "torchaudio": "torchaudio",
    "torchDirectMl": "torch_directml",
    "faiss": "faiss",
    "librosa": "librosa",
    "soundfile": "soundfile",
    "gradio": "gradio",
    "fastapi": "fastapi",
    "onnxruntime": "onnxruntime",
    "opencv": "cv2",
    "parselmouth": "parselmouth",
    "scikitLearn": "sklearn",
    "scipy": "scipy",
    "transformers": "transformers",
    "torchfcpe": "torchfcpe",
    "av": "av",
    "einops": "einops",
    "yaml": "yaml",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def module_version(module: Any) -> str | None:
    for attribute in ("__version__", "VERSION", "version"):
        value = getattr(module, attribute, None)
        if isinstance(value, (str, int, float)):
            return str(value)
    return None


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runtime = args.runtime_root.resolve()
    source = runtime / "source"
    blockers: list[str] = []
    imports: dict[str, Any] = {}
    loaded: dict[str, Any] = {}

    for label, module_name in REQUIRED_IMPORTS.items():
        try:
            module = importlib.import_module(module_name)
            loaded[label] = module
            imports[label] = {
                "module": module_name,
                "imported": True,
                "version": module_version(module),
            }
        except Exception as error:
            imports[label] = {
                "module": module_name,
                "imported": False,
                "error": f"{type(error).__name__}: {error}",
            }
            blockers.append(f"IMPORT_FAILED:{module_name}")

    torch = loaded.get("torch")
    torchaudio = loaded.get("torchaudio")
    torch_version = module_version(torch) if torch is not None else None
    torchaudio_version = module_version(torchaudio) if torchaudio is not None else None
    torch_version_ok = bool(torch_version and torch_version.startswith("2.4.1"))
    torchaudio_version_ok = bool(torchaudio_version and torchaudio_version.startswith("2.4.1"))
    if not torch_version_ok:
        blockers.append("CPU_TORCH_VERSION_NOT_2_4_1")
    if not torchaudio_version_ok:
        blockers.append("CPU_TORCHAUDIO_VERSION_NOT_2_4_1")

    cuda_available = bool(torch is not None and torch.cuda.is_available())
    cuda_version = str(torch.version.cuda) if torch is not None and torch.version.cuda is not None else None
    if cuda_available or cuda_version:
        blockers.append("CPU_PROFILE_FALSELY_EXPOSES_CUDA")

    tensor_result: list[float] | None = None
    tensor_exact = False
    tensor_finite = False
    if torch is not None:
        try:
            left = torch.tensor([1.0, 2.0, -3.0, 4.5], dtype=torch.float32, device="cpu")
            right = torch.tensor([2.0, 3.0, 1.0, -0.5], dtype=torch.float32, device="cpu")
            output = left * 2.0 + right
            tensor_result = [float(value) for value in output.tolist()]
            expected = [4.0, 7.0, -5.0, 8.5]
            tensor_exact = tensor_result == expected
            tensor_finite = all(math.isfinite(value) for value in tensor_result)
            if not tensor_exact:
                blockers.append("CPU_TENSOR_RESULT_MISMATCH")
            if not tensor_finite:
                blockers.append("CPU_TENSOR_NON_FINITE")
        except Exception as error:
            blockers.append(f"CPU_TENSOR_EXECUTION_FAILED:{type(error).__name__}")

    webui = source / "webui.py"
    webui_present = webui.is_file()
    webui_compile_exit: int | None = None
    webui_compile_error = ""
    if webui_present:
        completed = subprocess.run(
            [sys.executable, "-m", "py_compile", str(webui)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        webui_compile_exit = completed.returncode
        webui_compile_error = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0:
            blockers.append("WEBUI_SOURCE_COMPILE_FAILED")
    else:
        blockers.append("WEBUI_SOURCE_MISSING")

    torch_directml = loaded.get("torchDirectMl")
    directml_imported = torch_directml is not None
    directml_version = module_version(torch_directml) if torch_directml is not None else None

    proof = {
        "schema": SCHEMA,
        "status": "PASS" if not blockers else "BLOCKED",
        "finishedAtUtc": utc_now(),
        "runtimeRoot": str(runtime),
        "python": {
            "executable": sys.executable,
            "version": ".".join(map(str, sys.version_info[:3])),
        },
        "provider": {
            "profile": "cpu-directml-torch241",
            "torchVersion": torch_version,
            "torchaudioVersion": torchaudio_version,
            "torchVersionValid": torch_version_ok,
            "torchaudioVersionValid": torchaudio_version_ok,
            "cudaAvailable": cuda_available,
            "cudaVersion": cuda_version,
            "directMlImported": directml_imported,
            "directMlVersion": directml_version,
            "directMlDeviceExecutionAttempted": False,
        },
        "imports": imports,
        "cpuTensor": {
            "operation": "left * 2 + right",
            "result": tensor_result,
            "exact": tensor_exact,
            "finite": tensor_finite,
        },
        "source": {
            "webUiPath": str(webui),
            "webUiPresent": webui_present,
            "webUiCompileExitCode": webui_compile_exit,
            "webUiCompileError": webui_compile_error,
        },
        "blockers": blockers,
        "truthBoundary": {
            "fullDependencyEnvironmentImported": not blockers,
            "cpuTensorComputeProven": tensor_exact and tensor_finite,
            "directMlImportProven": directml_imported,
            "directMlDeviceExecutionProven": False,
            "rvcSourceCompiled": webui_compile_exit == 0,
            "hubertModelLoaded": False,
            "rmvpeModelLoaded": False,
            "voiceModelLoaded": False,
            "rvcInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "audioUploaded": False,
            "executionAuthorized": False,
        },
    }
    write_json_atomic(args.output.resolve(), proof)
    print(
        f"EchoesRvcCpuRuntimeProbe {proof['status']} imports={len(imports)} "
        f"torch={torch_version} directml={directml_imported} cpuTensor={tensor_exact} "
        "rvcInference=false conversion=false"
    )
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
