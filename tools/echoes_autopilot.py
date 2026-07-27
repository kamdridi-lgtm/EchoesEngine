#!/usr/bin/env python3
"""Deterministic, fail-closed Echoes Autopilot inbox controller."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = "echoes.autopilot-ledger.v1"
REPORT_SCHEMA = "echoes.autopilot-report.v1"
POLICY_SCHEMA = "echoes.autopilot-policy.v1"
ALLOWED_EXTENSION_CEILING = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"{label} is unreadable: {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_policy(policy: dict[str, Any]) -> list[str]:
    if policy.get("schema") != POLICY_SCHEMA:
        raise RuntimeError("Unsupported autopilot policy schema")
    interval = int(policy.get("scanIntervalMinutes", 0) or 0)
    maximum = int(policy.get("maxFilesPerRun", 0) or 0)
    if not 1 <= interval <= 60:
        raise RuntimeError("Policy scanIntervalMinutes must be between 1 and 60")
    if not 1 <= maximum <= 100:
        raise RuntimeError("Policy maxFilesPerRun must be between 1 and 100")
    extensions = [str(item).lower() for item in policy.get("allowedExtensions", [])]
    if not extensions or any(item not in ALLOWED_EXTENSION_CEILING for item in extensions):
        raise RuntimeError("Policy requested an unsupported audio extension")
    safety = policy.get("safety") if isinstance(policy.get("safety"), dict) else {}
    required = {
        "allowAudioUpload": False,
        "allowSourceDeletion": False,
        "allowArbitraryCommands": False,
        "requireHashLedger": True,
        "requireOperatorApprovalForExecution": True,
    }
    for field, expected in required.items():
        if safety.get(field) is not expected:
            raise RuntimeError(f"Unsafe policy field: {field}")
    return extensions


def safe_base_name(path: Path) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", path.stem).strip("-") or "audio"
    return value[:48].rstrip("-") or "audio"


def acquire_lock(path: Path) -> int | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            age = time.time() - path.stat().st_mtime
            if age > 7200:
                path.unlink()
        except OSError:
            pass
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(descriptor, f"pid={os.getpid()} started={utc_now()}\n".encode("utf-8"))
    return descriptor


def release_lock(path: Path, descriptor: int | None) -> None:
    if descriptor is not None:
        try:
            os.close(descriptor)
        finally:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def empty_ledger() -> dict[str, Any]:
    return {"schema": LEDGER_SCHEMA, "version": 1, "updatedAtUtc": utc_now(), "items": []}


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    ledger["updatedAtUtc"] = utc_now()
    write_json_atomic(path, ledger)


def copy_bundle_file(archive: zipfile.ZipFile, source: Path, arcname: str) -> None:
    if source.is_file():
        archive.write(source, arcname.replace("\\", "/"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--autopilot-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--inbox-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument(
        "--remote-policy-url",
        default="https://raw.githubusercontent.com/kamdridi-lgtm/EchoesEngine/main/config/echoes-autopilot-policy.v1.json",
    )
    parser.add_argument("--skip-remote-policy", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    if os.name != "nt":
        raise RuntimeError("Echoes Autopilot currently supports Windows only")

    autopilot = args.autopilot_root.resolve()
    runtime = args.runtime_root.resolve()
    inbox = args.inbox_root.resolve()
    results = args.results_root.resolve()
    control = args.control_root.resolve()
    for directory in (autopilot, inbox, results, control, control / "logs"):
        directory.mkdir(parents=True, exist_ok=True)

    lock_path = control / "autopilot.lock"
    lock_descriptor = acquire_lock(lock_path)
    if lock_descriptor is None:
        print("EchoesAutopilot SKIP reason=another-controller-is-running")
        return 0

    try:
        policy = load_object(args.policy.resolve(), "Local autopilot policy")
        validate_policy(policy)
        policy_source = "local"
        remote_applied = False
        remote_error: str | None = None
        if not args.skip_remote_policy and args.remote_policy_url:
            try:
                request = urllib.request.Request(args.remote_policy_url, headers={"User-Agent": "EchoesAutopilot/1.0"})
                with urllib.request.urlopen(request, timeout=20) as response:
                    candidate = json.loads(response.read().decode("utf-8"))
                if not isinstance(candidate, dict):
                    raise RuntimeError("Remote policy was not a JSON object")
                validate_policy(candidate)
                policy = candidate
                policy_source = "github-approved"
                remote_applied = True
                write_json_atomic(control / "remote-policy-cache.json", candidate)
            except Exception as error:  # remote failure must not destroy the local safe policy
                remote_error = str(error)

        extensions = set(validate_policy(policy))
        runtime_manifest_path = runtime / "runtime-manifest.json"
        analyzer_path = runtime / "Analyze-EchoesSong.ps1"
        runtime_python = runtime / ".venv" / "Scripts" / "python.exe"
        for required in (runtime_manifest_path, analyzer_path, runtime_python):
            if not required.is_file():
                raise RuntimeError(f"Installed runtime is incomplete: {required}")
        runtime_manifest = load_object(runtime_manifest_path, "Runtime manifest")
        if runtime_manifest.get("schema") != "echoes.local-song-activity-runtime-installation.v1" or runtime_manifest.get("status") != "PASS":
            raise RuntimeError("Installed runtime manifest is not PASS")

        ledger_path = control / "autopilot-ledger.json"
        ledger = load_object(ledger_path, "Autopilot ledger") if ledger_path.is_file() else empty_ledger()
        if ledger.get("schema") != LEDGER_SCHEMA:
            raise RuntimeError("Unsupported autopilot ledger schema")
        items = ledger.get("items") if isinstance(ledger.get("items"), list) else []

        started = datetime.now(timezone.utc)
        run_id = started.strftime("%Y%m%dT%H%M%S%fZ")
        run_log_path = control / "logs" / f"autopilot-{run_id}.log"
        discovered = sorted(
            (path for path in inbox.iterdir() if path.is_file() and path.suffix.lower() in extensions),
            key=lambda path: (path.stat().st_mtime_ns, str(path).lower()),
        )

        report_items: list[dict[str, Any]] = []
        attempted = successful = failed = duplicate_count = 0
        seen: dict[str, str] = {}

        if policy.get("enabled") is True:
            for source in discovered:
                source_sha = sha256_file(source)
                if source_sha in seen:
                    duplicate_count += 1
                    report_items.append(
                        {
                            "path": str(source),
                            "sha256": source_sha,
                            "status": "SKIPPED_DUPLICATE_HASH",
                            "canonicalPath": seen[source_sha],
                        }
                    )
                    continue
                seen[source_sha] = str(source)

                existing = next((item for item in items if isinstance(item, dict) and item.get("sha256") == source_sha), None)
                if existing and existing.get("status") == "PASS":
                    report_items.append(
                        {
                            "path": str(source),
                            "sha256": source_sha,
                            "status": "ALREADY_PROCESSED",
                            "jobId": existing.get("jobId"),
                        }
                    )
                    continue
                if attempted >= int(policy["maxFilesPerRun"]):
                    report_items.append({"path": str(source), "sha256": source_sha, "status": "DEFERRED_MAX_FILES"})
                    continue

                attempted += 1
                prior_attempts = int(existing.get("attempts", 0)) if existing else 0
                attempt = prior_attempts + 1
                job_id = f"{safe_base_name(source)}-{source_sha[:12]}-a{attempt}"
                job_directory = results / job_id
                analysis_manifest_path = job_directory / "analysis-run-manifest.json"
                invocation_log_path = control / "logs" / f"{job_id}-invocation.log"
                command = [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(analyzer_path),
                    "-InputPath",
                    str(source),
                    "-RuntimeRoot",
                    str(runtime),
                    "-OutputRoot",
                    str(results),
                    "-JobId",
                    job_id,
                    "-ExpectedInputSha256",
                    source_sha,
                ]
                if policy.get("declareUserSong") is True:
                    command.append("-DeclareUserSong")
                completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
                invocation_log_path.write_text(completed.stdout + completed.stderr, encoding="utf-8")

                status = "FAILED"
                error_text: str | None = None
                canonical_sha: str | None = None
                if completed.returncode == 0 and analysis_manifest_path.is_file():
                    try:
                        analysis_manifest = load_object(analysis_manifest_path, "Analysis manifest")
                        if analysis_manifest.get("status") == "PASS" and analysis_manifest.get("source", {}).get("sha256") == source_sha:
                            status = "PASS"
                            canonical_sha = analysis_manifest.get("timeline", {}).get("canonicalSha256")
                        else:
                            error_text = "Analysis manifest status or source SHA mismatch"
                    except Exception as error:
                        error_text = str(error)
                else:
                    error_text = f"Analyzer exit code {completed.returncode}"

                if job_directory.is_dir():
                    shutil.copy2(invocation_log_path, job_directory / "autopilot-invocation.log")
                items = [item for item in items if not (isinstance(item, dict) and item.get("sha256") == source_sha)]
                record = {
                    "sha256": source_sha,
                    "sourcePath": str(source),
                    "sourceName": source.name,
                    "sizeBytes": source.stat().st_size,
                    "status": status,
                    "attempts": attempt,
                    "jobId": job_id,
                    "analysisManifestPath": str(analysis_manifest_path),
                    "analysisLogPath": str(invocation_log_path),
                    "canonicalTimelineSha256": canonical_sha,
                    "lastAttemptAtUtc": utc_now(),
                    "error": error_text,
                }
                items.append(record)
                ledger["items"] = items
                save_ledger(ledger_path, ledger)
                if status == "PASS":
                    successful += 1
                else:
                    failed += 1
                report_items.append(record)
                with run_log_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{utc_now()} {status} sha={source_sha} job={job_id} path={source}\n")

        finished = datetime.now(timezone.utc)
        report: dict[str, Any] = {
            "schema": REPORT_SCHEMA,
            "status": "PARTIAL" if failed else "PASS",
            "runId": run_id,
            "startedAtUtc": started.isoformat().replace("+00:00", "Z"),
            "finishedAtUtc": finished.isoformat().replace("+00:00", "Z"),
            "durationSeconds": round((finished - started).total_seconds(), 3),
            "policy": {
                "source": policy_source,
                "version": policy.get("version"),
                "enabled": policy.get("enabled") is True,
                "maxFilesPerRun": int(policy["maxFilesPerRun"]),
                "remotePolicyApplied": remote_applied,
                "remotePolicyError": remote_error,
            },
            "paths": {
                "autopilotRoot": str(autopilot),
                "runtimeRoot": str(runtime),
                "inboxRoot": str(inbox),
                "resultsRoot": str(results),
                "controlRoot": str(control),
            },
            "summary": {
                "discoveredFiles": len(discovered),
                "attemptedFiles": attempted,
                "successfulFiles": successful,
                "failedFiles": failed,
                "duplicateHashFiles": duplicate_count,
                "ledgerItems": len(items),
            },
            "items": report_items,
            "truthBoundary": {
                "currentHostControllerExecuted": True,
                "scheduledExecutionObserved": not args.interactive,
                "remotePolicyControlObserved": remote_applied,
                "userSongAnalyzed": successful > 0 and policy.get("declareUserSong") is True,
                "hpOmenExecutionProven": False,
                "sourceAudioDeleted": False,
                "sourceAudioUploaded": False,
                "instrumentalClassificationProven": False,
                "vocalIsolationProven": False,
                "stemSeparationProven": False,
                "voiceConversionProven": False,
                "gpuInferenceProven": False,
                "tensorRtInferenceProven": False,
            },
        }
        latest_report_path = control / "autopilot-report-latest.json"
        timestamped_report_path = control / f"autopilot-report-{run_id}.json"
        write_json_atomic(latest_report_path, report)
        write_json_atomic(timestamped_report_path, report)
        ledger["items"] = items
        save_ledger(ledger_path, ledger)

        if policy.get("createControlBundle") is True:
            bundle_path = control / "Echoes-Control-Bundle-Latest.zip"
            temporary_bundle = control / f"Echoes-Control-Bundle-{run_id}.tmp.zip"
            with zipfile.ZipFile(temporary_bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                copy_bundle_file(archive, latest_report_path, "autopilot-report-latest.json")
                copy_bundle_file(archive, ledger_path, "autopilot-ledger.json")
                copy_bundle_file(archive, runtime_manifest_path, "runtime-manifest.json")
                copy_bundle_file(archive, run_log_path, "logs/autopilot-run.log")
                for item in report_items:
                    if item.get("status") not in {"PASS", "FAILED"}:
                        continue
                    job_id = str(item.get("jobId"))
                    job_directory = results / job_id
                    copy_bundle_file(archive, Path(str(item.get("analysisManifestPath"))), f"jobs/{job_id}/analysis-run-manifest.json")
                    copy_bundle_file(archive, Path(str(item.get("analysisLogPath"))), f"jobs/{job_id}/autopilot-invocation.log")
                    copy_bundle_file(archive, job_directory / "timeline" / "song-activity-timeline.json", f"jobs/{job_id}/song-activity-timeline.json")
                    copy_bundle_file(archive, job_directory / "timeline" / "song-activity-timeline.csv", f"jobs/{job_id}/song-activity-timeline.csv")
            temporary_bundle.replace(bundle_path)

        status_text = "\n".join(
            [
                "ECHOES AUTOPILOT",
                f"Status: {report['status']}",
                f"Run: {run_id}",
                f"Discovered: {len(discovered)}",
                f"Attempted: {attempted}",
                f"Successful: {successful}",
                f"Failed: {failed}",
                f"Inbox: {inbox}",
                f"Results: {results}",
                f"Control bundle: {control / 'Echoes-Control-Bundle-Latest.zip'}",
            ]
        )
        (control / "STATUS.txt").write_text(status_text + "\n", encoding="utf-8")
        print(
            f"EchoesAutopilot {report['status']} discovered={len(discovered)} attempted={attempted} "
            f"success={successful} failed={failed} policy={policy_source} audio-upload=false source-delete=false"
        )
        print(f"Inbox:   {inbox}")
        print(f"Results: {results}")
        print(f"Control: {control / 'Echoes-Control-Bundle-Latest.zip'}")

        if args.interactive and policy.get("openResultsOnInteractiveRun") is True:
            os.startfile(str(inbox if not discovered else results))  # type: ignore[attr-defined]
        return 2 if failed else 0
    finally:
        release_lock(lock_path, lock_descriptor)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes Autopilot failed: {error}", file=sys.stderr)
        raise SystemExit(2)
