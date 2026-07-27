#!/usr/bin/env python3
"""Autonomous, hash-ledgered stem separation for songs already approved by Echoes Autopilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STEM_LEDGER_SCHEMA = "echoes.stem-autopilot-ledger.v1"
STEM_REPORT_SCHEMA = "echoes.stem-autopilot-report.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as error:
        raise RuntimeError(f"{label} is unreadable: {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def copy_if_file(archive: zipfile.ZipFile, source: Path, arcname: str) -> None:
    if source.is_file():
        archive.write(source, arcname.replace("\\", "/"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stem-runtime-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--analysis-ledger", type=Path, required=True)
    parser.add_argument("--max-files", type=int, default=2)
    parser.add_argument("--declare-user-song", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    runtime = args.stem_runtime_root.resolve()
    results = args.results_root.resolve()
    control = args.control_root.resolve()
    analysis_ledger_path = args.analysis_ledger.resolve()
    control.mkdir(parents=True, exist_ok=True)
    (control / "logs").mkdir(parents=True, exist_ok=True)

    runtime_manifest_path = runtime / "stem-runtime-manifest.json"
    runner = runtime / "tools" / "separate_song_stems.py"
    runtime_python = runtime / ".venv" / "Scripts" / "python.exe"
    for required in (runtime_manifest_path, runner, runtime_python, analysis_ledger_path):
        if not required.is_file():
            raise RuntimeError(f"Required stem-autopilot file is missing: {required}")

    runtime_manifest = load_json(runtime_manifest_path, "Stem runtime manifest")
    if runtime_manifest.get("status") != "PASS":
        raise RuntimeError("Stem runtime manifest is not PASS")
    analysis_ledger = load_json(analysis_ledger_path, "Analysis ledger")
    if analysis_ledger.get("schema") != "echoes.autopilot-ledger.v1":
        raise RuntimeError("Analysis ledger schema is unsupported")

    stem_ledger_path = control / "stem-autopilot-ledger.json"
    if stem_ledger_path.is_file():
        stem_ledger = load_json(stem_ledger_path, "Stem ledger")
    else:
        stem_ledger = {"schema": STEM_LEDGER_SCHEMA, "version": 1, "updatedAtUtc": utc_now(), "items": []}
    if stem_ledger.get("schema") != STEM_LEDGER_SCHEMA:
        raise RuntimeError("Stem ledger schema is unsupported")
    stem_items = stem_ledger.get("items") if isinstance(stem_ledger.get("items"), list) else []

    candidates = [
        item
        for item in (analysis_ledger.get("items") or [])
        if isinstance(item, dict) and item.get("status") == "PASS" and item.get("sha256") and item.get("sourcePath")
    ]
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%S%fZ")
    run_log = control / "logs" / f"stem-autopilot-{run_id}.log"
    report_items: list[dict[str, Any]] = []
    attempted = successful = failed = already_done = missing_source = 0

    for candidate in candidates:
        source_sha = str(candidate["sha256"]).lower()
        existing = next(
            (item for item in stem_items if isinstance(item, dict) and item.get("sourceSha256") == source_sha),
            None,
        )
        if existing and existing.get("status") == "PASS":
            already_done += 1
            report_items.append(
                {
                    "sourceSha256": source_sha,
                    "sourcePath": candidate.get("sourcePath"),
                    "status": "ALREADY_SEPARATED",
                    "stemJobId": existing.get("stemJobId"),
                }
            )
            continue
        if attempted >= max(1, min(args.max_files, 20)):
            report_items.append(
                {
                    "sourceSha256": source_sha,
                    "sourcePath": candidate.get("sourcePath"),
                    "status": "DEFERRED_MAX_FILES",
                }
            )
            continue

        source = Path(str(candidate["sourcePath"]))
        if not source.is_file() or sha256_file(source) != source_sha:
            missing_source += 1
            report_items.append(
                {
                    "sourceSha256": source_sha,
                    "sourcePath": str(source),
                    "status": "SOURCE_MISSING_OR_CHANGED",
                }
            )
            continue

        attempted += 1
        attempts = int(existing.get("attempts", 0)) + 1 if existing else 1
        analysis_job_id = str(candidate.get("jobId") or f"analysis-{source_sha[:12]}")
        stem_job_id = f"{analysis_job_id}-stems-a{attempts}"
        output_directory = results / analysis_job_id / "stem-separation" / stem_job_id
        invocation_log = control / "logs" / f"{stem_job_id}-invocation.log"
        command = [
            str(runtime_python),
            str(runner),
            "--input",
            str(source),
            "--runtime-root",
            str(runtime),
            "--output-dir",
            str(output_directory),
            "--expected-input-sha256",
            source_sha,
        ]
        if args.declare_user_song:
            command.append("--declare-user-song")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        invocation_log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        manifest_path = output_directory / "stem-separation-manifest.json"
        status = "FAILED"
        error_text: str | None = None
        used_device: str | None = None
        stem_hashes: dict[str, str] = {}
        if completed.returncode == 0 and manifest_path.is_file():
            try:
                manifest = load_json(manifest_path, "Stem separation manifest")
                if manifest.get("status") == "PASS" and manifest.get("source", {}).get("sha256") == source_sha:
                    status = "PASS"
                    used_device = manifest.get("execution", {}).get("usedDevice")
                    stem_hashes = {
                        str(item.get("name")): str(item.get("sha256"))
                        for item in (manifest.get("stems") or [])
                        if isinstance(item, dict)
                    }
                else:
                    error_text = "Stem manifest status or source SHA mismatch"
            except Exception as error:
                error_text = str(error)
        else:
            error_text = f"Stem runner exit code {completed.returncode}"

        record = {
            "sourceSha256": source_sha,
            "sourcePath": str(source),
            "sourceName": source.name,
            "analysisJobId": analysis_job_id,
            "stemJobId": stem_job_id,
            "status": status,
            "attempts": attempts,
            "manifestPath": str(manifest_path),
            "invocationLogPath": str(invocation_log),
            "usedDevice": used_device,
            "stemSha256": stem_hashes,
            "lastAttemptAtUtc": utc_now(),
            "error": error_text,
        }
        stem_items = [
            item
            for item in stem_items
            if not (isinstance(item, dict) and item.get("sourceSha256") == source_sha)
        ]
        stem_items.append(record)
        stem_ledger["items"] = stem_items
        stem_ledger["updatedAtUtc"] = utc_now()
        write_json_atomic(stem_ledger_path, stem_ledger)
        report_items.append(record)
        if status == "PASS":
            successful += 1
        else:
            failed += 1
        with run_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{utc_now()} {status} source={source_sha} stemJob={stem_job_id} device={used_device}\n")

    finished = datetime.now(timezone.utc)
    report = {
        "schema": STEM_REPORT_SCHEMA,
        "status": "PARTIAL" if failed else "PASS",
        "runId": run_id,
        "startedAtUtc": started.isoformat().replace("+00:00", "Z"),
        "finishedAtUtc": finished.isoformat().replace("+00:00", "Z"),
        "durationSeconds": round((finished - started).total_seconds(), 3),
        "summary": {
            "analysisLedgerCandidates": len(candidates),
            "attemptedFiles": attempted,
            "successfulFiles": successful,
            "failedFiles": failed,
            "alreadySeparatedFiles": already_done,
            "missingOrChangedSources": missing_source,
            "stemLedgerItems": len(stem_items),
        },
        "items": report_items,
        "truthBoundary": {
            "currentHostStemControllerExecuted": True,
            "scheduledExecutionObserved": False,
            "userSongSeparated": successful > 0 and args.declare_user_song,
            "vocalIsolationProven": successful > 0,
            "stemSeparationProven": successful > 0,
            "gpuInferenceProven": any(item.get("usedDevice") == "cuda" and item.get("status") == "PASS" for item in report_items),
            "cpuInferenceProven": any(item.get("usedDevice") == "cpu" and item.get("status") == "PASS" for item in report_items),
            "sourceAudioDeleted": False,
            "sourceAudioUploaded": False,
            "voiceConversionProven": False,
            "hpOmenExecutionProven": False,
        },
    }
    latest_report = control / "stem-autopilot-report-latest.json"
    timestamped_report = control / f"stem-autopilot-report-{run_id}.json"
    write_json_atomic(latest_report, report)
    write_json_atomic(timestamped_report, report)
    stem_ledger["items"] = stem_items
    stem_ledger["updatedAtUtc"] = utc_now()
    write_json_atomic(stem_ledger_path, stem_ledger)

    bundle_path = control / "Echoes-Stem-Control-Bundle-Latest.zip"
    temporary_bundle = control / f"Echoes-Stem-Control-{run_id}.tmp.zip"
    with zipfile.ZipFile(temporary_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        copy_if_file(archive, latest_report, "stem-autopilot-report-latest.json")
        copy_if_file(archive, stem_ledger_path, "stem-autopilot-ledger.json")
        copy_if_file(archive, runtime_manifest_path, "stem-runtime-manifest.json")
        copy_if_file(archive, run_log, "logs/stem-autopilot-run.log")
        for item in report_items:
            if item.get("status") not in {"PASS", "FAILED"}:
                continue
            job_id = str(item.get("stemJobId"))
            copy_if_file(archive, Path(str(item.get("manifestPath"))), f"jobs/{job_id}/stem-separation-manifest.json")
            copy_if_file(archive, Path(str(item.get("invocationLogPath"))), f"jobs/{job_id}/stem-invocation.log")
    temporary_bundle.replace(bundle_path)

    status_lines = [
        "ECHOES STEM AUTOPILOT",
        f"Status: {report['status']}",
        f"Candidates: {len(candidates)}",
        f"Attempted: {attempted}",
        f"Successful: {successful}",
        f"Failed: {failed}",
        f"Already separated: {already_done}",
        f"Bundle: {bundle_path}",
    ]
    (control / "STEM-STATUS.txt").write_text("\n".join(status_lines) + "\n", encoding="utf-8")
    print(
        f"EchoesStemAutopilot {report['status']} candidates={len(candidates)} attempted={attempted} "
        f"success={successful} failed={failed} already={already_done} audio-upload=false source-delete=false"
    )
    print(f"Control bundle: {bundle_path}")
    if args.interactive and os.name == "nt":
        os.startfile(str(results))  # type: ignore[attr-defined]
    return 2 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes stem autopilot failed: {error}", file=sys.stderr)
        raise SystemExit(2)
