#!/usr/bin/env python3
"""Fail-closed local preflight for the first real Echoes Cinema AI clip.

The preflight does not install or download anything. It records the exact Python,
CUDA, GPU, dependency, storage, cache, FFmpeg, and provider-port state before a
real-model load is attempted. The report contains no authentication tokens.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any


SCHEMA = "echoes.cinema-p0-preflight.v1"
REQUIRED_PACKAGES = (
    "torch",
    "diffusers",
    "transformers",
    "accelerate",
    "safetensors",
    "imageio",
    "imageio-ffmpeg",
)
STORAGE_ENV_VARS = (
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "TRANSFORMERS_CACHE",
    "TORCH_HOME",
    "PIP_CACHE_DIR",
    "XDG_CACHE_HOME",
    "CUDA_CACHE_PATH",
    "NUMBA_CACHE_DIR",
    "PYTHONPYCACHEPREFIX",
    "TEMP",
    "TMP",
    "TMPDIR",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def windows_drive(raw: str | os.PathLike[str]) -> str:
    text = str(raw)
    if ":" not in text and "\\" not in text:
        return ""
    return PureWindowsPath(text).drive.upper()


def targets_system_drive(raw: str | os.PathLike[str]) -> bool:
    return windows_drive(raw) == "C:"


def path_is_on_expected_drive(raw: str | os.PathLike[str], expected_drive: str) -> bool:
    actual = windows_drive(raw)
    expected = expected_drive.rstrip("\\/").upper()
    if not expected.endswith(":"):
        expected += ":"
    return actual == expected


def port_is_available(host: str, port: int) -> tuple[bool, str | None]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            candidate.bind((host, port))
        return True, None
    except OSError as error:
        return False, str(error)


def package_versions() -> tuple[dict[str, str | None], list[str]]:
    versions: dict[str, str | None] = {}
    missing: list[str] = []
    for name in REQUIRED_PACKAGES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
            missing.append(name)
    return versions, missing


def nvidia_smi_report() -> tuple[dict[str, Any] | None, str | None]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None, "nvidia-smi is not available in PATH"
    command = [
        executable,
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)
    if completed.returncode != 0:
        return None, completed.stderr.strip() or f"nvidia-smi exited with {completed.returncode}"
    first_line = next((line.strip() for line in completed.stdout.splitlines() if line.strip()), "")
    fields = [field.strip() for field in first_line.split(",")]
    if len(fields) < 3:
        return {"raw": first_line}, "nvidia-smi returned an unexpected format"
    try:
        memory_mib: int | None = int(float(fields[1]))
    except ValueError:
        memory_mib = None
    return {
        "executable": executable,
        "name": fields[0],
        "memoryTotalMiB": memory_mib,
        "driverVersion": fields[2],
    }, None


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: Any) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAILED", "detail": detail})


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    workspace = args.workspace.resolve()
    output = args.output.resolve() if args.output else workspace / "proofs" / "first-real-ai-clip" / "preflight-report.json"
    workspace.mkdir(parents=True, exist_ok=True)

    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    expected_drive = args.expected_drive.rstrip("\\/")
    if os.name == "nt":
        add_check(
            checks,
            "workspace-drive",
            path_is_on_expected_drive(workspace, expected_drive) and not targets_system_drive(workspace),
            {"workspace": str(workspace), "expectedDrive": expected_drive, "actualDrive": windows_drive(workspace)},
        )
        add_check(
            checks,
            "python-executable-drive",
            not targets_system_drive(sys.executable),
            {"executable": sys.executable, "actualDrive": windows_drive(sys.executable)},
        )
    else:
        add_check(checks, "workspace-drive", True, {"workspace": str(workspace), "platform": os.name})
        add_check(checks, "python-executable-drive", True, {"executable": sys.executable, "platform": os.name})

    disk = shutil.disk_usage(workspace)
    free_gib = disk.free / 1024**3
    add_check(
        checks,
        "workspace-free-space",
        free_gib >= args.minimum_free_gib,
        {"freeBytes": disk.free, "freeGiB": round(free_gib, 2), "minimumRequiredGiB": args.minimum_free_gib},
    )

    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    add_check(checks, "ffmpeg", ffmpeg is not None, ffmpeg)
    add_check(checks, "ffprobe", ffprobe is not None, ffprobe)

    versions, missing = package_versions()
    add_check(checks, "python-packages", not missing, {"versions": versions, "missing": missing})

    cuda: dict[str, Any] = {
        "available": False,
        "deviceCount": 0,
        "deviceName": None,
        "vramBytes": None,
        "vramGiB": None,
        "torchCudaVersion": None,
        "error": None,
    }
    try:
        import torch

        cuda["torchVersion"] = torch.__version__
        cuda["torchCudaVersion"] = torch.version.cuda
        cuda["available"] = bool(torch.cuda.is_available())
        cuda["deviceCount"] = int(torch.cuda.device_count()) if cuda["available"] else 0
        if cuda["available"]:
            properties = torch.cuda.get_device_properties(0)
            cuda["deviceName"] = torch.cuda.get_device_name(0)
            cuda["vramBytes"] = int(properties.total_memory)
            cuda["vramGiB"] = round(properties.total_memory / 1024**3, 2)
            try:
                free_bytes, total_bytes = torch.cuda.mem_get_info(0)
                cuda["memoryFreeBytesAtPreflight"] = int(free_bytes)
                cuda["memoryTotalBytesAtPreflight"] = int(total_bytes)
            except Exception as error:  # noqa: BLE001 - optional evidence only
                warnings.append(f"torch CUDA memory query unavailable: {error}")
    except Exception as error:  # noqa: BLE001 - exact import/runtime blocker belongs in report
        cuda["error"] = str(error)

    add_check(
        checks,
        "cuda",
        bool(cuda["available"]) if args.require_cuda else cuda["error"] is None,
        cuda,
    )

    storage_paths: dict[str, str | None] = {name: os.environ.get(name) for name in STORAGE_ENV_VARS}
    unset_storage = [name for name, value in storage_paths.items() if not value]
    c_drive_storage = [name for name, value in storage_paths.items() if value and targets_system_drive(value)]
    add_check(
        checks,
        "storage-redirection",
        not unset_storage and not c_drive_storage,
        {"paths": storage_paths, "unset": unset_storage, "systemDriveTargets": c_drive_storage},
    )

    available, port_error = port_is_available(args.provider_host, args.provider_port)
    add_check(
        checks,
        "provider-port",
        available,
        {"host": args.provider_host, "port": args.provider_port, "error": port_error},
    )

    smi, smi_error = nvidia_smi_report()
    if smi_error:
        warnings.append(f"nvidia-smi evidence unavailable: {smi_error}")

    cache_root = workspace / "cache"
    cache = {
        "root": str(cache_root),
        "huggingFaceBytes": directory_size(cache_root / "huggingface"),
        "torchBytes": directory_size(cache_root / "torch"),
        "pipBytes": directory_size(cache_root / "pip"),
    }

    failed = [item for item in checks if item["status"] != "PASS"]
    return {
        "schema": SCHEMA,
        "timestampUtc": utc_now(),
        "status": "PASS" if not failed else "FAILED",
        "blockers": [item["name"] for item in failed],
        "warnings": warnings,
        "workspace": str(workspace),
        "reportPath": str(output),
        "python": {
            "version": sys.version,
            "versionInfo": list(sys.version_info[:3]),
            "executable": sys.executable,
            "architectureBits": 64 if sys.maxsize > 2**32 else 32,
        },
        "tools": {"ffmpeg": ffmpeg, "ffprobe": ffprobe},
        "packages": versions,
        "cuda": cuda,
        "nvidiaSmi": smi,
        "storage": {
            "workspaceFreeBytes": disk.free,
            "workspaceFreeGiB": round(free_gib, 2),
            "minimumRequiredGiB": args.minimum_free_gib,
            "systemDriveWritesAllowed": False,
            "environment": storage_paths,
            "cache": cache,
        },
        "providerPort": {"host": args.provider_host, "port": args.provider_port, "available": available},
        "checks": checks,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-p0-preflight-test-") as temporary:
        root = Path(temporary)
        nested = root / "cache" / "huggingface"
        nested.mkdir(parents=True)
        (nested / "model.bin").write_bytes(b"x" * 128)
        assert directory_size(root) == 128
        assert targets_system_drive(r"C:\EchoesCinema")
        assert not targets_system_drive(r"D:\A.I\EchoesCinema")
        assert path_is_on_expected_drive(r"D:\A.I\EchoesCinema", "D:")
        available, error = port_is_available("127.0.0.1", 0)
        assert available and error is None
        output = root / "report.json"
        atomic_write_json(output, {"schema": SCHEMA, "status": "PASS"})
        assert json.loads(output.read_text(encoding="utf-8"))["schema"] == SCHEMA
    print("CinemaP0Preflight PASS path-safety=validated atomic-report=validated port-probe=validated")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path(r"D:\A.I\EchoesCinema"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--minimum-free-gib", type=float, default=35.0)
    parser.add_argument("--expected-drive", default="D:")
    parser.add_argument("--provider-host", default="127.0.0.1")
    parser.add_argument("--provider-port", type=int, default=8081)
    parser.add_argument("--require-cuda", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.minimum_free_gib < 0:
        parser.error("--minimum-free-gib must be non-negative")
    if args.provider_port < 0 or args.provider_port > 65535:
        parser.error("--provider-port must be between 0 and 65535")
    return args


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()
    report = build_report(args)
    output = Path(report["reportPath"])
    atomic_write_json(output, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Cinema P0 preflight report: {output}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
