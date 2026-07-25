#!/usr/bin/env python3
"""Truthful expected-vs-installed runtime inventory for Echoes Cinema."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "echoes.cinema-runtime-inventory.v1"
DEPENDENCIES = (
    "diffusers", "transformers", "accelerate",
    "safetensors", "imageio", "imageio-ffmpeg",
)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def exact_pins(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            raise RuntimeError(f"dependency is not exactly pinned: {line}")
        name, version = (part.strip() for part in line.split("==", 1))
        result[name.lower().replace("_", "-")] = version
    if set(result) != set(DEPENDENCIES):
        raise RuntimeError(
            f"dependency set mismatch expected={sorted(DEPENDENCIES)} actual={sorted(result)}"
        )
    return result


def installed_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in (*DEPENDENCIES, "torch", "torchvision"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def base_version(value: Any) -> str | None:
    text = str(value or "").strip()
    return text.split("+", 1)[0] if text else None


def version_entry(expected: Any, actual: Any, *, compare_base: bool = False) -> dict[str, Any]:
    expected_text = str(expected) if expected not in (None, "") else None
    actual_text = str(actual) if actual not in (None, "") else None
    compared = base_version(actual_text) if compare_base else actual_text
    status = "MISSING" if actual_text is None else ("PASS" if compared == expected_text else "DRIFT")
    return {"status": status, "expected": expected_text, "installed": actual_text}


def evidence(path: Path, value: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.is_file(),
        "schema": value.get("schema") if value else None,
        "status": value.get("status") if value else "MISSING",
        "timestampUtc": value.get("timestampUtc") if value else None,
    }


def build_inventory(
    repo_root: Path,
    workspace: Path,
    *,
    actual_versions: Mapping[str, str | None] | None = None,
    python_version: tuple[int, int, int] | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    repo_root, workspace = repo_root.resolve(), workspace.resolve()
    providers = repo_root / "providers"
    torch_lock_path = providers / "torch-runtime-lock.json"
    ffmpeg_lock_path = providers / "ffmpeg-runtime-lock.json"
    requirements_path = providers / "requirements-diffusers.txt"
    bootstrap_path = workspace / "cinema-bootstrap-report.json"
    ffmpeg_path = workspace / "runtime" / "ffmpeg-runtime.json"

    torch_lock = load_json(torch_lock_path)
    ffmpeg_lock = load_json(ffmpeg_lock_path)
    if not torch_lock or torch_lock.get("schema") != "echoes.torch-runtime-lock.v1":
        raise RuntimeError(f"invalid Torch lock: {torch_lock_path}")
    if not ffmpeg_lock or ffmpeg_lock.get("schema") != "echoes.ffmpeg-runtime-lock.v1":
        raise RuntimeError(f"invalid FFmpeg lock: {ffmpeg_lock_path}")
    pins = exact_pins(requirements_path)
    actual = dict(actual_versions) if actual_versions is not None else installed_versions()
    current_python = python_version or (
        sys.version_info.major, sys.version_info.minor, sys.version_info.micro
    )
    allowed_python = [str(v) for v in torch_lock.get("pythonVersions") or []]
    major_minor = f"{current_python[0]}.{current_python[1]}"
    python_status = "PASS" if major_minor in allowed_python else "DRIFT"

    bootstrap, ffmpeg_evidence = load_json(bootstrap_path), load_json(ffmpeg_path)
    cuda = (bootstrap or {}).get("cuda")
    cuda = cuda if isinstance(cuda, dict) else {}
    bootstrap_python = (bootstrap or {}).get("python")
    bootstrap_python = bootstrap_python if isinstance(bootstrap_python, dict) else {}

    dependencies = {
        name: version_entry(expected, actual.get(name))
        for name, expected in sorted(pins.items())
    }
    torch = version_entry(torch_lock["torchVersion"], actual.get("torch"), compare_base=True)
    vision = version_entry(
        torch_lock["torchvisionVersion"], actual.get("torchvision"), compare_base=True
    )
    cuda_build = version_entry(
        torch_lock["expectedTorchCudaVersion"], cuda.get("torchCudaVersion")
    )
    cuda_runtime = (
        "MISSING" if bootstrap is None else ("PASS" if cuda.get("available") is True else "BLOCKED")
    )

    expected_ffmpeg = str(ffmpeg_lock["ffmpegVersion"])
    ffmpeg_line = str((ffmpeg_evidence or {}).get("ffmpegVersionLine") or "")
    ffprobe_line = str((ffmpeg_evidence or {}).get("ffprobeVersionLine") or "")
    ffmpeg = version_entry(
        expected_ffmpeg,
        expected_ffmpeg if f"version {expected_ffmpeg}" in ffmpeg_line else None,
    )
    ffprobe = version_entry(
        expected_ffmpeg,
        expected_ffmpeg if f"version {expected_ffmpeg}" in ffprobe_line else None,
    )
    media_status = (
        "MISSING" if ffmpeg_evidence is None
        else ("PASS" if ffmpeg_evidence.get("status") == "PASS" else "BLOCKED")
    )
    if media_status != "PASS":
        ffmpeg["status"] = ffprobe["status"] = media_status
    ffmpeg.update({
        "path": (ffmpeg_evidence or {}).get("ffmpegPath"),
        "versionLine": ffmpeg_line or None,
        "sha256": (ffmpeg_evidence or {}).get("downloadedSha256"),
        "expectedDigest": (ffmpeg_evidence or {}).get("expectedDigest"),
    })
    ffprobe.update({
        "path": (ffmpeg_evidence or {}).get("ffprobePath"),
        "versionLine": ffprobe_line or None,
    })

    statuses = [
        python_status, torch["status"], vision["status"], cuda_build["status"],
        cuda_runtime, ffmpeg["status"], ffprobe["status"],
        *(item["status"] for item in dependencies.values()),
    ]
    if all(item == "PASS" for item in statuses):
        status, failure = "PASS", None
    elif any(item in {"DRIFT", "BLOCKED"} for item in statuses):
        status, failure = "BLOCKED", "RUNTIME_VERSION_DRIFT_OR_BLOCKER"
    else:
        status, failure = "PARTIAL", "RUNTIME_EVIDENCE_MISSING"

    return {
        "schema": SCHEMA,
        "status": status,
        "failureClass": failure,
        "workspace": str(workspace),
        "repoRoot": str(repo_root),
        "python": {
            "status": python_status,
            "expectedMajorMinor": allowed_python,
            "installed": ".".join(map(str, current_python)),
            "executable": python_executable or sys.executable,
            "bootstrapExecutable": bootstrap_python.get("executable"),
        },
        "torch": torch,
        "torchvision": vision,
        "cuda": {
            "build": cuda_build,
            "runtimeStatus": cuda_runtime,
            "available": cuda.get("available") is True,
            "deviceName": cuda.get("deviceName"),
            "deviceCount": cuda.get("deviceCount"),
            "expectedTag": torch_lock.get("cudaTag"),
        },
        "dependencies": dependencies,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "evidence": {
            "bootstrap": evidence(bootstrap_path, bootstrap),
            "ffmpeg": evidence(ffmpeg_path, ffmpeg_evidence),
            "torchLock": str(torch_lock_path),
            "ffmpegLock": str(ffmpeg_lock_path),
            "dependencyLock": str(requirements_path),
        },
        "systemDriveWritesAllowed": False,
        "secretsPersisted": False,
    }


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-runtime-inventory-") as temporary:
        root = Path(temporary)
        repo, workspace = root / "repo", root / "workspace"
        providers, runtime = repo / "providers", workspace / "runtime"
        providers.mkdir(parents=True)
        runtime.mkdir(parents=True)
        (providers / "torch-runtime-lock.json").write_text(json.dumps({
            "schema": "echoes.torch-runtime-lock.v1",
            "torchVersion": "2.7.1", "torchvisionVersion": "0.22.1",
            "cudaTag": "cu118", "expectedTorchCudaVersion": "11.8",
            "indexUrl": "https://download.pytorch.org/whl/cu118",
            "platforms": ["Windows", "Linux"], "pythonVersions": ["3.10", "3.11"],
        }), encoding="utf-8")
        (providers / "ffmpeg-runtime-lock.json").write_text(json.dumps({
            "schema": "echoes.ffmpeg-runtime-lock.v1",
            "ffmpegVersion": "8.1.2",
        }), encoding="utf-8")
        pins = {
            "diffusers": "0.39.0", "transformers": "4.57.6",
            "accelerate": "1.14.0", "safetensors": "0.8.0",
            "imageio": "2.37.4", "imageio-ffmpeg": "0.6.0",
        }
        (providers / "requirements-diffusers.txt").write_text(
            "\n".join(f"{k}=={v}" for k, v in pins.items()) + "\n", encoding="utf-8"
        )
        (workspace / "cinema-bootstrap-report.json").write_text(json.dumps({
            "schema": "echoes.cinema-bootstrap-report.v3", "status": "PASS",
            "python": {"executable": "D:/A.I/EchoesCinema/.venv-cinema/Scripts/python.exe"},
            "cuda": {"available": True, "torchCudaVersion": "11.8",
                     "deviceName": "mock-gpu", "deviceCount": 1},
        }), encoding="utf-8")
        (runtime / "ffmpeg-runtime.json").write_text(json.dumps({
            "schema": "echoes.ffmpeg-runtime.v1", "status": "PASS",
            "ffmpegVersionLine": "ffmpeg version 8.1.2 essentials_build",
            "ffprobeVersionLine": "ffprobe version 8.1.2 essentials_build",
            "ffmpegPath": "D:/tools/ffmpeg.exe", "ffprobePath": "D:/tools/ffprobe.exe",
            "downloadedSha256": "a" * 64, "expectedDigest": "sha256:" + "a" * 64,
        }), encoding="utf-8")
        actual = {**pins, "torch": "2.7.1+cu118", "torchvision": "0.22.1+cu118"}
        ready = build_inventory(repo, workspace, actual_versions=actual,
                                python_version=(3, 10, 14))
        assert ready["status"] == "PASS", ready
        drifted = dict(actual)
        drifted["transformers"] = "5.0.0"
        drift = build_inventory(repo, workspace, actual_versions=drifted,
                                python_version=(3, 10, 14))
        assert drift["status"] == "BLOCKED"
        (runtime / "ffmpeg-runtime.json").unlink()
        missing = build_inventory(repo, workspace, actual_versions=actual,
                                  python_version=(3, 10, 14))
        assert missing["status"] == "PARTIAL"
        assert missing["ffmpeg"]["status"] == "MISSING"
    print("CinemaRuntimeInventory PASS exact=pass drift=blocked missing=partial")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--workspace", type=Path, default=Path(r"D:\A.I\EchoesCinema"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    value = build_inventory(args.repo_root, args.workspace)
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if value["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
