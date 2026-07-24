#!/usr/bin/env python3
"""Protect resumable P0 work and archive only completed REAL proofs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "echoes.cinema-p0-resume-policy.v1"
DIAGNOSTIC_FILES = (
    "run-failure.txt",
    "preflight-report.json",
    "gpu-report.json",
    "provider-health.json",
    "provider.log",
    "provider-error.log",
    "job-result.json",
    "video-qc.json",
    "manifest.log",
    "render.log",
    "assemble.log",
)
PRESERVED_FOR_RESUME = (
    "render-manifest.json",
    "render-state.json",
    "resume-plan.json",
    "render-output",
    "proof-audio.wav",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stamp_now() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_drive_c(path: Path) -> bool:
    return path.drive.upper() == "C:" or str(path).lower().startswith("c:\\")


def real_proof_complete(output_root: Path, job_id: str) -> tuple[bool, str]:
    result = load_json(output_root / "job-result.json")
    qc = load_json(output_root / "video-qc.json")
    final_mp4 = output_root / f"{job_id}.mp4"
    if not result:
        return False, "job-result.json is missing or unreadable"
    if result.get("status") != "PASS":
        return False, "job-result status is not PASS"
    if result.get("backendStatus") != "REAL":
        return False, "job-result backendStatus is not REAL"
    if not qc or qc.get("status") != "PASS":
        return False, "video-qc status is not PASS"
    if not final_mp4.is_file() or final_mp4.stat().st_size <= 0:
        return False, "final MP4 is missing or empty"
    return True, "completed REAL proof has PASS job result, REAL backend, PASS QC, and non-empty MP4"


def move_diagnostics(output_root: Path, attempt_root: Path) -> list[str]:
    moved: list[str] = []
    for name in DIAGNOSTIC_FILES:
        source = output_root / name
        if not source.exists():
            continue
        attempt_root.mkdir(parents=True, exist_ok=True)
        destination = attempt_root / name
        if destination.exists():
            destination.unlink() if destination.is_file() else shutil.rmtree(destination)
        shutil.move(str(source), str(destination))
        moved.append(name)
    return moved


def prepare(output_root: Path, archive_root: Path, job_id: str) -> dict[str, Any]:
    output_root = output_root.resolve()
    archive_root = archive_root.resolve()
    if is_drive_c(output_root) or is_drive_c(archive_root):
        raise RuntimeError("P0 resume policy refuses output or archive paths on drive C:")

    archive_root.mkdir(parents=True, exist_ok=True)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    existed = output_root.exists()
    existing_entries = list(output_root.iterdir()) if existed else []

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "timestampUtc": utc_now(),
        "outputRoot": str(output_root),
        "archiveRoot": str(archive_root),
        "jobId": job_id,
        "systemDriveWritesAllowed": False,
        "action": "FRESH",
        "realProofComplete": False,
        "completionReason": "no prior proof directory",
        "movedDiagnostics": [],
        "preservedForResume": [],
        "archivePath": None,
    }

    if not existed or not existing_entries:
        output_root.mkdir(parents=True, exist_ok=True)
        report["completionReason"] = "no prior proof artifacts"
    else:
        complete, reason = real_proof_complete(output_root, job_id)
        report["realProofComplete"] = complete
        report["completionReason"] = reason
        if complete:
            destination = archive_root / f"first-real-ai-clip-{stamp_now()}"
            shutil.move(str(output_root), str(destination))
            output_root.mkdir(parents=True, exist_ok=True)
            report["action"] = "ARCHIVED_COMPLETED_REAL"
            report["archivePath"] = str(destination)
        else:
            attempt_root = output_root / "attempts" / stamp_now()
            report["movedDiagnostics"] = move_diagnostics(output_root, attempt_root)
            report["preservedForResume"] = [
                name for name in PRESERVED_FOR_RESUME if (output_root / name).exists()
            ]
            report["action"] = "PRESERVED_INCOMPLETE_FOR_RESUME"
            report["attemptArchivePath"] = str(attempt_root) if report["movedDiagnostics"] else None

    atomic_write_json(output_root / "resume-policy.json", report)
    return report


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-p0-resume-policy-") as temporary:
        root = Path(temporary) / "D-drive-simulation"
        output = root / "proofs" / "first-real-ai-clip"
        archive = root / "proofs" / "archive"

        fresh = prepare(output, archive, "proof-job")
        assert fresh["action"] == "FRESH"
        assert (output / "resume-policy.json").is_file()

        (output / "render-output" / "clips").mkdir(parents=True)
        (output / "render-output" / "clips" / "shot.mp4").write_bytes(b"clip")
        (output / "render-state.json").write_text('{"status":"FAILED"}\n', encoding="utf-8")
        (output / "job-result.json").write_text('{"status":"FAILED"}\n', encoding="utf-8")
        (output / "provider-error.log").write_text("OOM blocker\n", encoding="utf-8")
        incomplete = prepare(output, archive, "proof-job")
        assert incomplete["action"] == "PRESERVED_INCOMPLETE_FOR_RESUME"
        assert (output / "render-state.json").is_file()
        assert (output / "render-output" / "clips" / "shot.mp4").is_file()
        assert not (output / "job-result.json").exists()
        assert any((output / "attempts").rglob("provider-error.log"))

        (output / "job-result.json").write_text(
            '{"status":"PASS","backendStatus":"REAL"}\n', encoding="utf-8"
        )
        (output / "video-qc.json").write_text('{"status":"PASS"}\n', encoding="utf-8")
        (output / "proof-job.mp4").write_bytes(b"real-video")
        complete = prepare(output, archive, "proof-job")
        assert complete["action"] == "ARCHIVED_COMPLETED_REAL"
        assert complete["realProofComplete"] is True
        assert Path(str(complete["archivePath"])).is_dir()
        assert (output / "resume-policy.json").is_file()
        assert not (output / "proof-job.mp4").exists()

    print("CinemaP0ResumePolicy PASS fresh=validated incomplete=preserved real=archived")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--job-id")
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()
    if args.output_root is None or args.archive_root is None or not args.job_id:
        raise SystemExit("--output-root, --archive-root, and --job-id are required")
    report = prepare(args.output_root, args.archive_root, args.job_id)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
