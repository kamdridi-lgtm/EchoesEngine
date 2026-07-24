#!/usr/bin/env python3
"""Safe lifecycle cleanup for Echoes Cinema job directories.

The janitor never removes active/recoverable jobs, pinned jobs, the service
ledger, final MP4 files, manifests, QC reports, job results, or logs. It prunes
only disposable render intermediates from old terminal jobs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"PASS", "FAILED", "BROKEN"}
PROTECTED_STATUSES = {"QUEUED", "RUNNING", "RECOVERABLE"}
DEFAULT_DISPOSABLE_RELATIVE_PATHS = (
    "render-output/clips",
    "render-output/frames",
    "render-output/tmp",
    "temp",
    "tmp",
    "provider-downloads",
)
REPORT_SCHEMA = "echoes.cinema-storage-janitor-report.v1"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def directory_bytes(path: Path) -> int:
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


def safe_job_dir(output_root: Path, job_id: str) -> Path:
    root = output_root.resolve()
    candidate = (root / job_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise RuntimeError(f"job directory escapes output root: {job_id}") from error
    if candidate == root or candidate.name == "_service":
        raise RuntimeError(f"unsafe job directory: {candidate}")
    return candidate


def safe_child(job_dir: Path, relative: str) -> Path:
    candidate = (job_dir / Path(relative)).resolve()
    try:
        candidate.relative_to(job_dir.resolve())
    except ValueError as error:
        raise RuntimeError(f"cleanup target escapes job directory: {relative}") from error
    return candidate


@dataclass(frozen=True)
class JanitorPolicy:
    minimum_age_days: float = 3.0
    keep_newest_terminal_jobs: int = 3
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.minimum_age_days < 0:
            raise ValueError("minimum_age_days must be non-negative")
        if self.keep_newest_terminal_jobs < 0:
            raise ValueError("keep_newest_terminal_jobs must be non-negative")


def load_ledger(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cinema job ledger is unreadable or corrupt: {path}: {error}") from error
    if payload.get("schema") != "echoes.cinema-job-ledger.v1":
        raise RuntimeError(f"unsupported Cinema job ledger schema: {path}")
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        raise RuntimeError(f"Cinema job ledger jobs map is invalid: {path}")
    return payload


def prune_jobs(
    output_root: Path,
    ledger: dict[str, Any],
    policy: JanitorPolicy,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = now or utc_now()
    threshold = now - timedelta(days=policy.minimum_age_days)
    jobs = ledger.get("jobs") or {}

    terminal_records: list[tuple[str, dict[str, Any], datetime]] = []
    for job_id, raw in jobs.items():
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", ""))
        if status not in TERMINAL_STATUSES:
            continue
        updated = parse_utc(raw.get("finishedAt") or raw.get("updatedAt")) or datetime.min.replace(tzinfo=timezone.utc)
        terminal_records.append((str(job_id), raw, updated))
    terminal_records.sort(key=lambda item: (item[2], item[0]), reverse=True)
    protected_newest = {job_id for job_id, _, _ in terminal_records[: policy.keep_newest_terminal_jobs]}

    removed: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    freed_bytes = 0

    for job_id, raw in jobs.items():
        if not isinstance(raw, dict):
            preserved.append({"jobId": str(job_id), "reason": "invalid-ledger-record"})
            continue
        job_id = str(job_id)
        status = str(raw.get("status", ""))
        if status in PROTECTED_STATUSES:
            preserved.append({"jobId": job_id, "status": status, "reason": "active-or-recoverable"})
            continue
        if status not in TERMINAL_STATUSES:
            preserved.append({"jobId": job_id, "status": status, "reason": "unsupported-status"})
            continue
        if raw.get("pinned") is True:
            preserved.append({"jobId": job_id, "status": status, "reason": "ledger-pinned"})
            continue
        if job_id in protected_newest:
            preserved.append({"jobId": job_id, "status": status, "reason": "newest-terminal-protection"})
            continue

        updated = parse_utc(raw.get("finishedAt") or raw.get("updatedAt"))
        if updated is None or updated > threshold:
            preserved.append({"jobId": job_id, "status": status, "reason": "retention-age-not-reached"})
            continue

        try:
            job_dir = safe_job_dir(root, job_id)
            if not job_dir.is_dir():
                preserved.append({"jobId": job_id, "status": status, "reason": "job-directory-missing"})
                continue
            if (job_dir / ".keep").exists():
                preserved.append({"jobId": job_id, "status": status, "reason": "keep-marker"})
                continue

            job_removed = 0
            targets: list[str] = []
            for relative in DEFAULT_DISPOSABLE_RELATIVE_PATHS:
                target = safe_child(job_dir, relative)
                if not target.exists():
                    continue
                before = directory_bytes(target) if target.is_dir() else (target.stat().st_size if target.is_file() else 0)
                if not policy.dry_run:
                    if target.is_dir() and not target.is_symlink():
                        shutil.rmtree(target)
                    else:
                        target.unlink(missing_ok=True)
                job_removed += before
                targets.append(relative)
            if targets:
                freed_bytes += job_removed
                removed.append(
                    {
                        "jobId": job_id,
                        "status": status,
                        "freedBytes": job_removed,
                        "targets": targets,
                        "dryRun": policy.dry_run,
                    }
                )
            else:
                preserved.append({"jobId": job_id, "status": status, "reason": "no-disposable-intermediates"})
        except Exception as error:  # noqa: BLE001 - cleanup errors belong in the report
            errors.append({"jobId": job_id, "status": status, "error": str(error)})

    return {
        "schema": REPORT_SCHEMA,
        "status": "PASS" if not errors else "PARTIAL",
        "timestampUtc": now.isoformat().replace("+00:00", "Z"),
        "outputRoot": str(root),
        "policy": {
            "minimumAgeDays": policy.minimum_age_days,
            "keepNewestTerminalJobs": policy.keep_newest_terminal_jobs,
            "dryRun": policy.dry_run,
            "protectedStatuses": sorted(PROTECTED_STATUSES),
            "preservedEvidence": [
                "final MP4 files",
                "job-result.json",
                "render-manifest.json",
                "render-state.json",
                "resume-plan.json",
                "video-qc.json",
                "logs",
                "service ledger",
            ],
        },
        "freedBytes": freed_bytes,
        "removed": removed,
        "preserved": preserved,
        "errors": errors,
    }


def self_test() -> int:
    fixed_now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="echoes-janitor-test-") as temp_dir:
        root = Path(temp_dir) / "jobs"
        root.mkdir()
        jobs: dict[str, Any] = {}

        def add(job_id: str, status: str, days_old: int, *, pinned: bool = False, keep_marker: bool = False) -> None:
            job_dir = root / job_id
            clips = job_dir / "render-output" / "clips"
            clips.mkdir(parents=True)
            (clips / "clip.mp4").write_bytes(b"x" * 128)
            (job_dir / f"{job_id}.mp4").write_bytes(b"final")
            (job_dir / "job-result.json").write_text("{}", encoding="utf-8")
            if keep_marker:
                (job_dir / ".keep").write_text("", encoding="utf-8")
            stamp = (fixed_now - timedelta(days=days_old)).isoformat().replace("+00:00", "Z")
            jobs[job_id] = {
                "jobId": job_id,
                "status": status,
                "updatedAt": stamp,
                "finishedAt": stamp if status in TERMINAL_STATUSES else None,
                "pinned": pinned,
            }

        add("running", "RUNNING", 20)
        add("recoverable", "RECOVERABLE", 20)
        add("newest-pass", "PASS", 1)
        add("old-pass", "PASS", 20)
        add("pinned-pass", "PASS", 30, pinned=True)
        add("marker-pass", "PASS", 30, keep_marker=True)

        ledger = {"schema": "echoes.cinema-job-ledger.v1", "jobs": jobs}
        report = prune_jobs(
            root,
            ledger,
            JanitorPolicy(minimum_age_days=3, keep_newest_terminal_jobs=1),
            now=fixed_now,
        )
        assert report["status"] == "PASS"
        assert report["freedBytes"] == 128
        assert not (root / "old-pass" / "render-output" / "clips").exists()
        assert (root / "old-pass" / "old-pass.mp4").is_file()
        assert (root / "old-pass" / "job-result.json").is_file()
        assert (root / "running" / "render-output" / "clips").is_dir()
        assert (root / "recoverable" / "render-output" / "clips").is_dir()
        assert (root / "newest-pass" / "render-output" / "clips").is_dir()
        assert (root / "pinned-pass" / "render-output" / "clips").is_dir()
        assert (root / "marker-pass" / "render-output" / "clips").is_dir()

        dry_dir = root / "dry-old"
        (dry_dir / "render-output" / "clips").mkdir(parents=True)
        (dry_dir / "render-output" / "clips" / "clip.mp4").write_bytes(b"y" * 64)
        jobs["dry-old"] = {
            "jobId": "dry-old",
            "status": "FAILED",
            "updatedAt": "2026-06-01T00:00:00Z",
            "finishedAt": "2026-06-01T00:00:00Z",
        }
        dry_report = prune_jobs(
            root,
            ledger,
            JanitorPolicy(minimum_age_days=3, keep_newest_terminal_jobs=0, dry_run=True),
            now=fixed_now,
        )
        assert dry_report["freedBytes"] >= 64
        assert (dry_dir / "render-output" / "clips").is_dir()

    print("CinemaStorageJanitor PASS active=protected evidence=preserved intermediates=pruned")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--minimum-age-days", type=float, default=3.0)
    parser.add_argument("--keep-newest-terminal-jobs", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.output_root is None:
        raise SystemExit("--output-root is required")
    ledger_path = args.ledger or (args.output_root / "_service" / "job-ledger.json")
    if not ledger_path.is_file():
        raise SystemExit(f"Cinema job ledger not found: {ledger_path}")

    ledger = load_ledger(ledger_path)
    policy = JanitorPolicy(
        minimum_age_days=args.minimum_age_days,
        keep_newest_terminal_jobs=args.keep_newest_terminal_jobs,
        dry_run=args.dry_run,
    )
    report = prune_jobs(args.output_root, ledger, policy)
    report_path = args.report or (args.output_root / "_service" / "storage-janitor-report.json")
    atomic_write_json(report_path, report)
    print(
        f"CinemaStorageJanitor {report['status']} freed={report['freedBytes']} "
        f"jobs={len(report['removed'])} report={report_path}"
    )
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
