#!/usr/bin/env python3
"""Verify the reproducible Echoes Cinema Diffusers runtime.

The lock source is ``requirements-diffusers.txt``. Every production dependency
must use an exact ``==`` pin. The verifier reports package drift separately from
PyTorch wheel and NVIDIA runtime blockers so the Windows worker can repair only
what is actually repairable.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "echoes.diffusers-environment-lock.v1"
LOCK_PATH = Path(__file__).with_name("requirements-diffusers.txt")
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


def parse_exact_lock(path: Path = LOCK_PATH) -> dict[str, str]:
    """Parse exact package pins and reject ranges, duplicates, or omissions."""

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


def classify_environment(
    expected: dict[str, str],
    installed: dict[str, str | None],
    import_errors: dict[str, str],
    torch_info: dict[str, Any],
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

    status = "PASS"
    failure_class: str | None = None
    retryable = False
    operator_restart_required = False
    blocker: str | None = None

    if missing:
        status = "FAILED"
        failure_class = "DEPENDENCY_MISSING"
        retryable = True
        blocker = "Missing pinned packages: " + ", ".join(missing)
    elif mismatches:
        status = "FAILED"
        failure_class = "DEPENDENCY_VERSION_DRIFT"
        retryable = True
        blocker = "Pinned dependency versions differ from the lock file."
    elif import_errors:
        status = "FAILED"
        failure_class = "DEPENDENCY_IMPORT_FAILED"
        retryable = True
        blocker = "Pinned packages are installed but one or more imports failed."
    elif not torch_info.get("importable"):
        status = "FAILED"
        failure_class = "TORCH_MISSING"
        retryable = True
        blocker = str(torch_info.get("error") or "PyTorch is not importable.")
    elif require_cuda_build and not torch_info.get("cudaBuildPresent"):
        status = "FAILED"
        failure_class = "CPU_ONLY_TORCH"
        retryable = True
        blocker = "PyTorch is importable but it is not a CUDA-enabled build."
    elif require_cuda_runtime and not torch_info.get("cudaRuntimeAvailable"):
        status = "BLOCKED"
        failure_class = "CUDA_RUNTIME_UNAVAILABLE"
        retryable = False
        operator_restart_required = True
        blocker = "A CUDA-enabled PyTorch wheel is installed, but the NVIDIA runtime is unavailable."

    return {
        "status": status,
        "failureClass": failure_class,
        "retryable": retryable,
        "operatorRestartRequired": operator_restart_required,
        "blocker": blocker,
        "missingPackages": missing,
        "versionMismatches": mismatches,
    }


def inspect_environment(
    *,
    lock_path: Path = LOCK_PATH,
    require_cuda_build: bool = True,
    require_cuda_runtime: bool = True,
) -> dict[str, Any]:
    expected = parse_exact_lock(lock_path)
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
            except Exception as error:  # noqa: BLE001 - exact import blocker belongs in evidence
                import_errors[distribution] = str(error)

    torch_info: dict[str, Any] = {
        "importable": False,
        "version": None,
        "cudaBuildPresent": False,
        "torchCudaVersion": None,
        "cudaRuntimeAvailable": False,
        "deviceCount": 0,
        "deviceName": None,
        "error": None,
    }
    try:
        import torch  # type: ignore

        torch_info.update(
            {
                "importable": True,
                "version": str(torch.__version__),
                "cudaBuildPresent": bool(torch.version.cuda),
                "torchCudaVersion": str(torch.version.cuda) if torch.version.cuda else None,
                "cudaRuntimeAvailable": bool(torch.cuda.is_available()),
            }
        )
        if torch_info["cudaRuntimeAvailable"]:
            torch_info["deviceCount"] = int(torch.cuda.device_count())
            torch_info["deviceName"] = str(torch.cuda.get_device_name(0))
    except Exception as error:  # noqa: BLE001 - exact torch blocker belongs in evidence
        torch_info["error"] = str(error)

    classification = classify_environment(
        expected,
        installed,
        import_errors,
        torch_info,
        require_cuda_build=require_cuda_build,
        require_cuda_runtime=require_cuda_runtime,
    )
    return {
        "schema": SCHEMA,
        **classification,
        "lockPath": str(lock_path.resolve()),
        "expectedVersions": expected,
        "installedVersions": installed,
        "importErrors": import_errors,
        "torch": torch_info,
        "systemDriveWritesAllowed": False,
    }


def _ready_torch() -> dict[str, Any]:
    return {
        "importable": True,
        "version": "2.x+cu128",
        "cudaBuildPresent": True,
        "torchCudaVersion": "12.8",
        "cudaRuntimeAvailable": True,
        "deviceCount": 1,
        "deviceName": "mock-gpu",
        "error": None,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-diffusers-lock-") as temporary:
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
        pins = parse_exact_lock(valid)
        assert pins["diffusers"] == "0.39.0"
        assert pins["transformers"] == "4.57.6"

        ranged = root / "ranged.txt"
        ranged.write_text(valid.read_text(encoding="utf-8").replace("diffusers==0.39.0", "diffusers>=0.39.0"), encoding="utf-8")
        try:
            parse_exact_lock(ranged)
        except RuntimeError as error:
            assert "not an exact pin" in str(error)
        else:
            raise AssertionError("A ranged dependency unexpectedly passed the exact-lock parser")

        incomplete = root / "incomplete.txt"
        incomplete.write_text("diffusers==0.39.0\n", encoding="utf-8")
        try:
            parse_exact_lock(incomplete)
        except RuntimeError as error:
            assert "package set mismatch" in str(error)
        else:
            raise AssertionError("An incomplete dependency lock unexpectedly passed")

        installed = dict(pins)
        ready = classify_environment(pins, installed, {}, _ready_torch())
        assert ready["status"] == "PASS"

        drifted = dict(installed)
        drifted["transformers"] = "5.14.1"
        drift = classify_environment(pins, drifted, {}, _ready_torch())
        assert drift["failureClass"] == "DEPENDENCY_VERSION_DRIFT"
        assert drift["retryable"] is True
        assert drift["versionMismatches"][0]["package"] == "transformers"

        cpu_torch = _ready_torch()
        cpu_torch["cudaBuildPresent"] = False
        cpu_torch["cudaRuntimeAvailable"] = False
        cpu = classify_environment(pins, installed, {}, cpu_torch)
        assert cpu["failureClass"] == "CPU_ONLY_TORCH" and cpu["retryable"] is True

        blocked_torch = _ready_torch()
        blocked_torch["cudaRuntimeAvailable"] = False
        blocked = classify_environment(pins, installed, {}, blocked_torch)
        assert blocked["status"] == "BLOCKED"
        assert blocked["failureClass"] == "CUDA_RUNTIME_UNAVAILABLE"
        assert blocked["operatorRestartRequired"] is True

    print(
        "DiffusersEnvironmentLock PASS exact-pins=required drift=repairable "
        "cpu-torch=repairable cuda-runtime=fail-closed ready=exact"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock-path", type=Path, default=LOCK_PATH)
    parser.add_argument("--allow-cpu-torch", action="store_true")
    parser.add_argument("--allow-unavailable-cuda-runtime", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    payload = inspect_environment(
        lock_path=args.lock_path,
        require_cuda_build=not args.allow_cpu_torch,
        require_cuda_runtime=not args.allow_unavailable_cuda_runtime,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
