#!/usr/bin/env python3
"""Normalize stem truth claims, run technical QC, and rebuild the no-audio bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def write_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_file(archive: zipfile.ZipFile, source: Path, name: str) -> None:
    if source.is_file() and source.suffix.lower() not in AUDIO_EXTENSIONS:
        archive.write(source, name.replace("\\", "/"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stem-runtime-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    args = parser.parse_args()

    runtime = args.stem_runtime_root.resolve()
    control = args.control_root.resolve()
    runtime_manifest_path = runtime / "stem-runtime-manifest.json"
    ledger_path = control / "stem-autopilot-ledger.json"
    report_path = control / "stem-autopilot-report-latest.json"
    quality_tool = runtime / "tools" / "analyze_stem_quality.py"
    for required in (runtime_manifest_path, ledger_path, report_path, quality_tool):
        if not required.is_file():
            raise RuntimeError(f"Stem QC required file is missing: {required}")

    runtime_manifest = load(runtime_manifest_path)
    ledger = load(ledger_path)
    report = load(report_path)
    ffmpeg_info = runtime_manifest.get("ffmpeg") if isinstance(runtime_manifest.get("ffmpeg"), dict) else {}
    ffmpeg = Path(str(ffmpeg_info.get("executable") or ""))
    python = Path(str((runtime_manifest.get("python") or {}).get("executable") or ""))
    if not ffmpeg.is_file() or not python.is_file():
        raise RuntimeError("Stem QC runtime paths are incomplete")

    pass_items = [
        item for item in (ledger.get("items") or [])
        if isinstance(item, dict) and item.get("status") == "PASS"
    ]
    qc_pass = 0
    qc_review = 0
    qc_blocked = 0

    for item in pass_items:
        manifest_path = Path(str(item.get("manifestPath") or ""))
        if not manifest_path.is_file():
            item["qualityStatus"] = "BLOCKED"
            item["qualityError"] = "SEPARATION_MANIFEST_MISSING"
            qc_blocked += 1
            continue
        manifest = load(manifest_path)
        source = Path(str((manifest.get("source") or {}).get("path") or ""))
        source_sha = str((manifest.get("source") or {}).get("sha256") or "").lower()
        stems = manifest.get("stems") if isinstance(manifest.get("stems"), list) else []
        stem_paths = [Path(str(stem.get("path") or "")) for stem in stems if isinstance(stem, dict)]
        stems_dir = stem_paths[0].parent if stem_paths else Path()
        quality_path = manifest_path.parent / "stem-quality-report.json"
        command = [
            str(python),
            str(quality_tool),
            "--source", str(source),
            "--stems-dir", str(stems_dir),
            "--ffmpeg", str(ffmpeg),
            "--expected-source-sha256", source_sha,
            "--output", str(quality_path),
        ]
        separation_truth = manifest.get("truthBoundary") if isinstance(manifest.get("truthBoundary"), dict) else {}
        if separation_truth.get("userSongSeparated") is True:
            command.append("--declare-user-song")
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        quality_log = manifest_path.parent / "stem-quality-normalization.log"
        quality_log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
        quality = load(quality_path) if quality_path.is_file() else {
            "schema": "echoes.stem-technical-quality.v1",
            "status": "BLOCKED",
            "advisoryStatus": "REVIEW",
            "blockers": [f"QUALITY_TOOL_EXIT:{completed.returncode}"],
            "truthBoundary": {"technicalStemQcProven": False},
        }
        technical_pass = completed.returncode == 0 and quality.get("status") == "PASS"
        advisory = str(quality.get("advisoryStatus") or "REVIEW")
        quality_sha = sha256_file(quality_path) if quality_path.is_file() else None

        manifest["quality"] = {
            "reportPath": str(quality_path),
            "reportSha256": quality_sha,
            "status": quality.get("status"),
            "advisoryStatus": advisory,
            "technicalStemQcProven": technical_pass,
            "vocalIsolationQualityProven": False,
            "humanListeningReviewCompleted": False,
        }
        checks = manifest.setdefault("checks", {})
        checks["technicalStemQcPassed"] = technical_pass
        checks["qualityReportHashRecorded"] = bool(quality_sha)
        truth = manifest.setdefault("truthBoundary", {})
        truth["vocalStemGenerated"] = True
        truth["vocalIsolationProven"] = False
        truth["vocalIsolationQualityProven"] = False
        truth["technicalStemQcProven"] = technical_pass
        truth["humanListeningReviewCompleted"] = False
        truth["acapellaReady"] = False
        truth["instrumentalMixCreated"] = False
        truth["voiceConversionProven"] = False
        write_atomic(manifest_path, manifest)

        item["qualityReportPath"] = str(quality_path)
        item["qualityReportSha256"] = quality_sha
        item["qualityStatus"] = quality.get("status")
        item["qualityAdvisoryStatus"] = advisory
        item["technicalStemQcProven"] = technical_pass
        item["vocalIsolationQualityProven"] = False
        item["qualityNormalizedAtUtc"] = utc_now()
        item["qualityError"] = None if technical_pass else ";".join(quality.get("blockers") or ["QUALITY_BLOCKED"])
        if technical_pass:
            qc_pass += 1
            if advisory == "REVIEW":
                qc_review += 1
        else:
            qc_blocked += 1

    all_qc_pass = bool(pass_items) and qc_pass == len(pass_items) and qc_blocked == 0
    ledger["updatedAtUtc"] = utc_now()
    ledger["technicalQc"] = {
        "processedStemSets": len(pass_items),
        "pass": qc_pass,
        "review": qc_review,
        "blocked": qc_blocked,
        "allPass": all_qc_pass,
    }
    write_atomic(ledger_path, ledger)

    report_truth = report.setdefault("truthBoundary", {})
    report_truth["vocalStemGenerated"] = bool(pass_items)
    report_truth["vocalIsolationProven"] = False
    report_truth["vocalIsolationQualityProven"] = False
    report_truth["technicalStemQcProven"] = all_qc_pass
    report_truth["humanListeningReviewCompleted"] = False
    report_truth["acapellaReady"] = False
    report_truth["voiceConversionProven"] = False
    report["technicalQc"] = {
        "processedStemSets": len(pass_items),
        "pass": qc_pass,
        "review": qc_review,
        "blocked": qc_blocked,
        "allPass": all_qc_pass,
    }
    if qc_blocked:
        report["status"] = "PARTIAL"
    report["truthNormalizedAtUtc"] = utc_now()
    write_atomic(report_path, report)

    runtime_truth = runtime_manifest.setdefault("truthBoundary", {})
    runtime_truth["realStemInferenceExecuted"] = bool(pass_items)
    runtime_truth["vocalStemGenerated"] = bool(pass_items)
    runtime_truth["vocalIsolationProven"] = False
    runtime_truth["vocalIsolationQualityProven"] = False
    runtime_truth["technicalStemQcProven"] = all_qc_pass
    runtime_truth["humanListeningReviewCompleted"] = False
    runtime_truth["acapellaReady"] = False
    runtime_truth["voiceConversionProven"] = False
    runtime_manifest["technicalQc"] = {
        "schema": "echoes.stem-technical-quality.v1",
        "status": "PASS" if all_qc_pass else "PARTIAL",
        "processedStemSets": len(pass_items),
        "pass": qc_pass,
        "review": qc_review,
        "blocked": qc_blocked,
    }
    write_atomic(runtime_manifest_path, runtime_manifest)

    bundle = control / "Echoes-Stem-Control-Bundle-Latest.zip"
    temporary = control / f"Echoes-Stem-Control-QC-{os.getpid()}.tmp.zip"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive_file(archive, report_path, "stem-autopilot-report-latest.json")
        archive_file(archive, ledger_path, "stem-autopilot-ledger.json")
        archive_file(archive, runtime_manifest_path, "stem-runtime-manifest.json")
        for log in sorted((control / "logs").glob("*.log")):
            archive_file(archive, log, f"logs/{log.name}")
        for item in ledger.get("items") or []:
            if not isinstance(item, dict) or item.get("status") != "PASS":
                continue
            job_id = str(item.get("stemJobId") or "unknown")
            archive_file(archive, Path(str(item.get("manifestPath") or "")), f"jobs/{job_id}/stem-separation-manifest.json")
            archive_file(archive, Path(str(item.get("qualityReportPath") or "")), f"jobs/{job_id}/stem-quality-report.json")
            archive_file(archive, Path(str(item.get("invocationLogPath") or "")), f"jobs/{job_id}/stem-invocation.log")
    temporary.replace(bundle)

    with zipfile.ZipFile(bundle) as archive:
        if any(Path(name).suffix.lower() in AUDIO_EXTENSIONS for name in archive.namelist()):
            raise RuntimeError("Stem control bundle contains audio")

    print(
        f"EchoesStemTruthAndQc {'PASS' if all_qc_pass else 'PARTIAL'} "
        f"sets={len(pass_items)} qcPass={qc_pass} review={qc_review} blocked={qc_blocked} "
        "vocalIsolationQuality=false acapellaReady=false"
    )
    return 0 if all_qc_pass else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes stem truth/QC normalization failed: {error}", file=sys.stderr)
        raise SystemExit(2)
