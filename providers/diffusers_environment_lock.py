#!/usr/bin/env python3
"""Verify the reproducible Echoes Cinema Python, Torch and CUDA runtime."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import re
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "echoes.diffusers-environment-lock.v2"
LOCK_PATH = Path(__file__).with_name("requirements-diffusers.txt")
TORCH_LOCK_PATH = Path(__file__).with_name("torch-runtime-lock.json")
REQUIRED_DISTRIBUTIONS = (
    "diffusers",
    "transformers",
    "accelerate",
    "safetensors",
    "imageio",
    "imageio-ffmpeg",
)
IMPORT_NAMES = {
    "diffusers": "diffusers",
    "transformers": "transformers",
    "accelerate": "accelerate",
    "safetensors": "safetensors",
    "imageio": "imageio",
    "imageio-ffmpeg": "imageio_ffmpeg",
}


def base_version(value: Any) -> str | None:
    text = str(value or "").strip()
    return text.split("+", 1)[0] if text else None


def parse_exact_lock(path: Path = LOCK_PATH) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line or any(marker in line for marker in (">=", "<=", "~=", "!=", "<", ">")):
            raise RuntimeError(f"Diffusers lock entry is not an exact pin: {line}")
        name, version = (part.strip() for part in line.split("==", 1))
        normalized = name.lower().replace("_", "-")
        if not name or not version:
            raise RuntimeError(f"Invalid Diffusers lock entry: {line}")
        if normalized in pins:
            raise RuntimeError(f"Duplicate Diffusers lock entry: {normalized}")
        pins[normalized] = version
    missing = sorted(set(REQUIRED_DISTRIBUTIONS) - set(pins))
    unexpected = sorted(set(pins) - set(REQUIRED_DISTRIBUTIONS))
    if missing or unexpected:
        raise RuntimeError(f"Diffusers lock package set mismatch; missing={missing}; unexpected={unexpected}")
    return pins


def parse_torch_lock(path: Path = TORCH_LOCK_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or payload.get("schema") != "echoes.torch-runtime-lock.v1":
        raise RuntimeError("Torch runtime lock schema is missing or unsupported")
    required = (
        "torchVersion",
        "torchvisionVersion",
        "cudaTag",
        "expectedTorchCudaVersion",
        "indexUrl",
        "platforms",
        "pythonVersions",
    )
    missing = [name for name in required if payload.get(name) in (None, "", [])]
    if missing:
        raise RuntimeError(f"Torch runtime lock is incomplete: {missing}")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(payload["torchVersion"])):
        raise RuntimeError("Torch runtime lock torchVersion must be exact x.y.z")
    if not re.fullmatch(r"\d+\.\d+\.\d+", str(payload["torchvisionVersion"])):
        raise RuntimeError("Torch runtime lock torchvisionVersion must be exact x.y.z")
    if not re.fullmatch(r"cu\d{3}", str(payload["cudaTag"])):
        raise RuntimeError("Torch runtime lock cudaTag must look like cu118")
    expected_index = f"https://download.pytorch.org/whl/{payload['cudaTag']}"
    if payload["indexUrl"] != expected_index:
        raise RuntimeError(f"Torch runtime lock indexUrl must be {expected_index}")
    if str(payload["expectedTorchCudaVersion"]).replace(".", "") != str(payload["cudaTag"])[2:]:
        raise RuntimeError("Torch runtime lock CUDA tag and expected CUDA version disagree")
    return payload


def classify_environment(
    expected: dict[str, str],
    installed: dict[str, str | None],
    import_errors: dict[str, str],
    torch_info: dict[str, Any],
    torch_lock: dict[str, Any],
    *,
    require_cuda_build: bool = True,
    require_cuda_runtime: bool = True,
) -> dict[str, Any]:
    missing = sorted(package for package in REQUIRED_DISTRIBUTIONS if installed.get(package) is None)
    mismatches = [
        {"package": package, "expected": expected[package], "actual": installed.get(package)}
        for package in REQUIRED_DISTRIBUTIONS
        if installed.get(package) != expected[package]
    ]
    torch_mismatches: list[dict[str, str | None]] = []
    if torch_info.get("importable"):
        expected_torch = str(torch_lock["torchVersion"])
        actual_torch = base_version(torch_info.get("version"))
        if actual_torch != expected_torch:
            torch_mismatches.append({"package": "torch", "expected": expected_torch, "actual": actual_torch})
        expected_vision = str(torch_lock["torchvisionVersion"])
        actual_vision = base_version(torch_info.get("torchvisionVersion"))
        if actual_vision != expected_vision:
            torch_mismatches.append({"package": "torchvision", "expected": expected_vision, "actual": actual_vision})

    status = "PASS"
    failure_class: str | None = None
    retryable = False
    operator_restart_required = False
    blocker: str | None = None

    if missing:
        status, failure_class, retryable = "FAILED", "DEPENDENCY_MISSING", True
        blocker = "Missing pinned packages: " + ", ".join(missing)
    elif mismatches:
        status, failure_class, retryable = "FAILED", "DEPENDENCY_VERSION_DRIFT", True
        blocker = "Pinned dependency versions differ from the lock file."
    elif import_errors:
        status, failure_class, retryable = "FAILED", "DEPENDENCY_IMPORT_FAILED", True
        blocker = "Pinned packages are installed but one or more imports failed."
    elif not torch_info.get("importable"):
        status, failure_class, retryable = "FAILED", "TORCH_MISSING", True
        blocker = str(torch_info.get("error") or "PyTorch is not importable.")
    elif torch_mismatches:
        status, failure_class, retryable = "FAILED", "TORCH_RUNTIME_VERSION_DRIFT", True
        blocker = "Torch or TorchVision differs from the pinned runtime lock."
    elif require_cuda_build and not torch_info.get("cudaBuildPresent"):
        status, failure_class, retryable = "FAILED", "CPU_ONLY_TORCH", True
        blocker = "PyTorch is importable but it is not a CUDA-enabled build."
    elif require_cuda_build and str(torch_info.get("torchCudaVersion") or "") != str(torch_lock["expectedTorchCudaVersion"]):
        status, failure_class, retryable = "FAILED", "TORCH_CUDA_BUILD_DRIFT", True
        blocker = "PyTorch CUDA build differs from the pinned runtime lock."
    elif require_cuda_runtime and not torch_info.get("cudaRuntimeAvailable"):
        status, failure_class = "BLOCKED", "CUDA_RUNTIME_UNAVAILABLE"
        operator_restart_required = True
        blocker = "The pinned CUDA PyTorch wheel is installed, but the NVIDIA runtime is unavailable."

    return {
        "status": status,
        "failureClass": failure_class,
        "retryable": retryable,
        "operatorRestartRequired": operator_restart_required,
        "blocker": blocker,
        "missingPackages": missing,
        "versionMismatches": mismatches,
        "torchRuntimeMismatches": torch_mismatches,
    }


def inspect_environment(
    *,
    lock_path: Path = LOCK_PATH,
    torch_lock_path: Path = TORCH_LOCK_PATH,
    require_cuda_build: bool = True,
    require_cuda_runtime: bool = True,
) -> dict[str, Any]:
    expected = parse_exact_lock(lock_path)
    torch_lock = parse_torch_lock(torch_lock_path)
    installed: dict[str, str | None] = {}
    import_errors: dict[str, str] = {}
    for distribution in REQUIRED_DISTRIBUTIONS:
        try:
            actual = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            actual = None
        installed[distribution] = actual
        if actual is not None:
            try:
                importlib.import_module(IMPORT_NAMES[distribution])
            except Exception as error:  # noqa: BLE001
                import_errors[distribution] = str(error)

    torch_info: dict[str, Any] = {
        "importable": False,
        "version": None,
        "torchvisionVersion": None,
        "cudaBuildPresent": False,
        "torchCudaVersion": None,
        "cudaRuntimeAvailable": False,
        "deviceCount": 0,
        "deviceName": None,
        "error": None,
    }
    try:
        import torch  # type: ignore

        try:
            torchvision_version = importlib.metadata.version("torchvision")
        except importlib.metadata.PackageNotFoundError:
            torchvision_version = None
        torch_info.update(
            {
                "importable": True,
                "version": str(torch.__version__),
                "torchvisionVersion": torchvision_version,
                "cudaBuildPresent": bool(torch.version.cuda),
                "torchCudaVersion": str(torch.version.cuda) if torch.version.cuda else None,
                "cudaRuntimeAvailable": bool(torch.cuda.is_available()),
            }
        )
        if torch_info["cudaRuntimeAvailable"]:
            torch_info["deviceCount"] = int(torch.cuda.device_count())
            torch_info["deviceName"] = str(torch.cuda.get_device_name(0))
    except Exception as error:  # noqa: BLE001
        torch_info["error"] = str(error)

    classification = classify_environment(
        expected,
        installed,
        import_errors,
        torch_info,
        torch_lock,
        require_cuda_build=require_cuda_build,
        require_cuda_runtime=require_cuda_runtime,
    )
    return {
        "schema": SCHEMA,
        **classification,
        "lockPath": str(lock_path.resolve()),
        "torchLockPath": str(torch_lock_path.resolve()),
        "expectedVersions": expected,
        "expectedTorchRuntime": torch_lock,
        "installedVersions": installed,
        "importErrors": import_errors,
        "torch": torch_info,
        "systemDriveWritesAllowed": False,
    }


def _ready_torch() -> dict[str, Any]:
    return {
        "importable": True,
        "version": "2.7.1+cu118",
        "torchvisionVersion": "0.22.1+cu118",
        "cudaBuildPresent": True,
        "torchCudaVersion": "11.8",
        "cudaRuntimeAvailable": True,
        "deviceCount": 1,
        "deviceName": "mock-gpu",
        "error": None,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-environment-lock-") as temporary:
        root = Path(temporary)
        valid = root / "requirements.txt"
        valid.write_text(
            "\n".join(
                [
                    "diffusers==0.39.0",
                    "transformers==4.57.6",
                    "accelerate==1.14.0",
                    "safetensors==0.8.0",
                    "imageio==2.37.4",
                    "imageio-ffmpeg==0.6.0",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        torch_lock_path = root / "torch-runtime-lock.json"
        torch_lock_path.write_text(
            json.dumps(
                {
                    "schema": "echoes.torch-runtime-lock.v1",
                    "torchVersion": "2.7.1",
                    "torchvisionVersion": "0.22.1",
                    "cudaTag": "cu118",
                    "expectedTorchCudaVersion": "11.8",
                    "indexUrl": "https://download.pytorch.org/whl/cu118",
                    "platforms": ["Windows", "Linux"],
                    "pythonVersions": ["3.10", "3.11"],
                }
            ),
            encoding="utf-8",
        )
        pins = parse_exact_lock(valid)
        torch_lock = parse_torch_lock(torch_lock_path)
        installed = dict(pins)
        assert classify_environment(pins, installed, {}, _ready_torch(), torch_lock)["status"] == "PASS"

        drifted = dict(installed)
        drifted["transformers"] = "5.14.1"
        assert classify_environment(pins, drifted, {}, _ready_torch(), torch_lock)["failureClass"] == "DEPENDENCY_VERSION_DRIFT"

        torch_drift = _ready_torch()
        torch_drift["version"] = "2.11.0+cu128"
        torch_drift["torchvisionVersion"] = "0.26.0+cu128"
        torch_drift["torchCudaVersion"] = "12.8"
        drift = classify_environment(pins, installed, {}, torch_drift, torch_lock)
        assert drift["failureClass"] == "TORCH_RUNTIME_VERSION_DRIFT" and drift["retryable"] is True

        cuda_drift = _ready_torch()
        cuda_drift["torchCudaVersion"] = "12.8"
        assert classify_environment(pins, installed, {}, cuda_drift, torch_lock)["failureClass"] == "TORCH_CUDA_BUILD_DRIFT"

        cpu_torch = _ready_torch()
        cpu_torch["cudaBuildPresent"] = False
        cpu_torch["torchCudaVersion"] = None
        cpu_torch["cudaRuntimeAvailable"] = False
        assert classify_environment(pins, installed, {}, cpu_torch, torch_lock)["failureClass"] == "CPU_ONLY_TORCH"

        blocked_torch = _ready_torch()
        blocked_torch["cudaRuntimeAvailable"] = False
        blocked = classify_environment(pins, installed, {}, blocked_torch, torch_lock)
        assert blocked["status"] == "BLOCKED" and blocked["operatorRestartRequired"] is True

        ranged = root / "ranged.txt"
        ranged.write_text(valid.read_text(encoding="utf-8").replace("diffusers==0.39.0", "diffusers>=0.39.0"), encoding="utf-8")
        try:
            parse_exact_lock(ranged)
        except RuntimeError as error:
            assert "not an exact pin" in str(error)
        else:
            raise AssertionError("A ranged dependency unexpectedly passed")

    print(
        "DiffusersEnvironmentLock PASS python=exact torch=2.7.1 torchvision=0.22.1 "
        "cuda-build=11.8 drift=repairable runtime=fail-closed"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock-path", type=Path, default=LOCK_PATH)
    parser.add_argument("--torch-lock-path", type=Path, default=TORCH_LOCK_PATH)
    parser.add_argument("--allow-cpu-torch", action="store_true")
    parser.add_argument("--allow-unavailable-cuda-runtime", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    payload = inspect_environment(
        lock_path=args.lock_path,
        torch_lock_path=args.torch_lock_path,
        require_cuda_build=not args.allow_cpu_torch,
        require_cuda_runtime=not args.allow_unavailable_cuda_runtime,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
