#!/usr/bin/env python3
"""Apply a truthful operator listening decision to a technically valid stem set.

The tool never performs voice conversion and never edits the original separation
manifest. It verifies source/stem hashes and technical QC evidence, writes a
standalone review record, and optionally emits a reviewed manifest copy.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXPECTED_STEMS = ("vocals", "drums", "bass", "other")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVIEW_SCHEMA = "echoes.stem-listening-review.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_sha(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if SHA256_PATTERN.fullmatch(candidate) else ""


def add_blocker(blockers: list[str], value: str) -> None:
    if value not in blockers:
        blockers.append(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--separation-manifest", type=Path, required=True)
    parser.add_argument("--quality-report", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--decision", choices=("approve", "reject"), required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reviewed-manifest-output", type=Path)
    parser.add_argument("--confirm-listened-to-vocals", action="store_true")
    parser.add_argument("--confirm-listened-to-instrumental", action="store_true")
    parser.add_argument("--declare-user-song", action="store_true")
    args = parser.parse_args()

    manifest_path = args.separation_manifest.resolve()
    quality_path = args.quality_report.resolve()
    output_path = args.output.resolve()
    reviewed_manifest_path = (
        args.reviewed_manifest_output.resolve() if args.reviewed_manifest_output else None
    )
    expected_source_sha = normalized_sha(args.expected_source_sha256)
    reviewer = args.reviewer.strip()
    blockers: list[str] = []
    checks: dict[str, bool] = {}

    if not expected_source_sha:
        add_blocker(blockers, "EXPECTED_SOURCE_SHA256_INVALID")
    if not reviewer:
        add_blocker(blockers, "REVIEWER_IDENTITY_MISSING")
    if not manifest_path.is_file():
        add_blocker(blockers, "SEPARATION_MANIFEST_MISSING")
    if not quality_path.is_file():
        add_blocker(blockers, "TECHNICAL_QUALITY_REPORT_MISSING")

    manifest: dict[str, Any] = {}
    quality: dict[str, Any] = {}
    if not blockers:
        manifest = load_json(manifest_path)
        quality = load_json(quality_path)

    if manifest:
        checks["manifestSchemaValid"] = manifest.get("schema") == "echoes.stem-separation-run.v1"
        checks["manifestStatusPass"] = manifest.get("status") == "PASS"
        if not checks["manifestSchemaValid"]:
            add_blocker(blockers, "SEPARATION_MANIFEST_SCHEMA_INVALID")
        if not checks["manifestStatusPass"]:
            add_blocker(blockers, "SEPARATION_MANIFEST_NOT_PASS")

    if quality:
        checks["qualitySchemaValid"] = quality.get("schema") == "echoes.stem-technical-quality.v1"
        checks["qualityStatusPass"] = quality.get("status") == "PASS"
        quality_truth = quality.get("truthBoundary") if isinstance(quality.get("truthBoundary"), dict) else {}
        checks["technicalStemQcProven"] = quality_truth.get("technicalStemQcProven") is True
        if not checks["qualitySchemaValid"]:
            add_blocker(blockers, "TECHNICAL_QUALITY_SCHEMA_INVALID")
        if not checks["qualityStatusPass"]:
            add_blocker(blockers, "TECHNICAL_QUALITY_NOT_PASS")
        if not checks["technicalStemQcProven"]:
            add_blocker(blockers, "TECHNICAL_STEM_QC_NOT_PROVEN")

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    manifest_source_sha = normalized_sha(source.get("sha256"))
    quality_source = quality.get("source") if isinstance(quality.get("source"), dict) else {}
    quality_source_sha = normalized_sha(quality_source.get("sha256"))
    checks["sourceShaMatchesManifest"] = bool(expected_source_sha) and manifest_source_sha == expected_source_sha
    checks["sourceShaMatchesQuality"] = bool(expected_source_sha) and quality_source_sha == expected_source_sha
    if not checks["sourceShaMatchesManifest"]:
        add_blocker(blockers, "SOURCE_SHA256_MANIFEST_MISMATCH")
    if not checks["sourceShaMatchesQuality"]:
        add_blocker(blockers, "SOURCE_SHA256_QUALITY_MISMATCH")

    source_path = Path(str(source.get("path") or ""))
    checks["sourceFilePresent"] = source_path.is_file()
    checks["sourceFileShaVerified"] = False
    if source_path.is_file() and expected_source_sha:
        checks["sourceFileShaVerified"] = sha256_file(source_path) == expected_source_sha
    if not checks["sourceFilePresent"]:
        add_blocker(blockers, "SOURCE_FILE_MISSING")
    elif not checks["sourceFileShaVerified"]:
        add_blocker(blockers, "SOURCE_FILE_SHA256_MISMATCH")

    recorded_quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    recorded_quality_sha = normalized_sha(recorded_quality.get("reportSha256"))
    actual_quality_sha = sha256_file(quality_path) if quality_path.is_file() else ""
    checks["qualityReportShaVerified"] = (
        not recorded_quality_sha or recorded_quality_sha == actual_quality_sha
    )
    if not checks["qualityReportShaVerified"]:
        add_blocker(blockers, "QUALITY_REPORT_SHA256_MISMATCH")

    manifest_checks = manifest.get("checks") if isinstance(manifest.get("checks"), dict) else {}
    checks["manifestTechnicalQcPassed"] = manifest_checks.get("technicalStemQcPassed") is True
    if not checks["manifestTechnicalQcPassed"]:
        add_blocker(blockers, "MANIFEST_TECHNICAL_QC_NOT_PASS")

    stem_entries = {
        str(entry.get("name")): entry
        for entry in (manifest.get("stems") or [])
        if isinstance(entry, dict)
    }
    checks["fourExpectedStemsRecorded"] = set(stem_entries) == set(EXPECTED_STEMS)
    if not checks["fourExpectedStemsRecorded"]:
        add_blocker(blockers, "EXPECTED_STEM_SET_MISMATCH")

    verified_stem_hashes: dict[str, str] = {}
    for name in EXPECTED_STEMS:
        entry = stem_entries.get(name)
        if not isinstance(entry, dict):
            continue
        path = Path(str(entry.get("path") or ""))
        recorded_sha = normalized_sha(entry.get("sha256"))
        if not path.is_file():
            add_blocker(blockers, f"STEM_FILE_MISSING:{name}")
            continue
        actual_sha = sha256_file(path)
        if not recorded_sha or actual_sha != recorded_sha:
            add_blocker(blockers, f"STEM_SHA256_MISMATCH:{name}")
            continue
        verified_stem_hashes[name] = actual_sha
    checks["allStemHashesVerified"] = len(verified_stem_hashes) == len(EXPECTED_STEMS)

    manifest_truth = manifest.get("truthBoundary") if isinstance(manifest.get("truthBoundary"), dict) else {}
    checks["stemSeparationProven"] = manifest_truth.get("stemSeparationProven") is True
    checks["sourceAudioPreserved"] = manifest_checks.get("sourceAudioPreserved") is True
    if not checks["stemSeparationProven"]:
        add_blocker(blockers, "STEM_SEPARATION_NOT_PROVEN")
    if not checks["sourceAudioPreserved"]:
        add_blocker(blockers, "SOURCE_PRESERVATION_NOT_PROVEN")

    if args.decision == "approve":
        if not args.confirm_listened_to_vocals:
            add_blocker(blockers, "LISTENING_CONFIRMATION_MISSING:vocals")
        if not args.confirm_listened_to_instrumental:
            add_blocker(blockers, "LISTENING_CONFIRMATION_MISSING:instrumental")

    review_completed = not blockers
    operator_accepted = review_completed and args.decision == "approve"
    status = "BLOCKED" if blockers else ("APPROVED" if operator_accepted else "REJECTED")
    reviewed_at = utc_now()
    review = {
        "schema": REVIEW_SCHEMA,
        "status": status,
        "decision": args.decision,
        "reviewer": reviewer,
        "notes": args.notes,
        "reviewedAtUtc": reviewed_at,
        "declaredUserSong": bool(args.declare_user_song),
        "inputs": {
            "separationManifestPath": str(manifest_path),
            "separationManifestSha256": sha256_file(manifest_path) if manifest_path.is_file() else None,
            "technicalQualityReportPath": str(quality_path),
            "technicalQualityReportSha256": actual_quality_sha or None,
            "sourceSha256": expected_source_sha or None,
            "verifiedStemSha256": verified_stem_hashes,
        },
        "confirmations": {
            "listenedToVocals": bool(args.confirm_listened_to_vocals),
            "listenedToInstrumental": bool(args.confirm_listened_to_instrumental),
            "automaticApprovalUsed": False,
        },
        "checks": checks,
        "blockers": blockers,
        "truthBoundary": {
            "humanListeningReviewCompleted": review_completed,
            "operatorAcceptedVocalStem": operator_accepted,
            "operatorAcceptedInstrumentalStems": operator_accepted,
            "technicalStemQcProven": bool(checks.get("technicalStemQcProven")) and not blockers,
            "vocalIsolationQualityProven": False,
            "subjectiveVocalQualityAcceptedByOperator": operator_accepted,
            "acapellaReady": operator_accepted,
            "voiceConversionInputReady": operator_accepted,
            "voiceConversionProven": False,
            "automaticApprovalUsed": False,
            "originalManifestMutated": False,
        },
    }
    write_json_atomic(output_path, review)
    review_sha = sha256_file(output_path)

    if reviewed_manifest_path is not None:
        reviewed_manifest = copy.deepcopy(manifest)
        reviewed_manifest["listeningReview"] = {
            "schema": REVIEW_SCHEMA,
            "status": status,
            "decision": args.decision,
            "reviewer": reviewer,
            "reviewPath": str(output_path),
            "reviewSha256": review_sha,
            "reviewedAtUtc": reviewed_at,
        }
        reviewed_truth = reviewed_manifest.setdefault("truthBoundary", {})
        reviewed_truth["humanListeningReviewCompleted"] = review_completed
        reviewed_truth["operatorAcceptedVocalStem"] = operator_accepted
        reviewed_truth["vocalIsolationQualityProven"] = False
        reviewed_truth["acapellaReady"] = operator_accepted
        reviewed_truth["voiceConversionInputReady"] = operator_accepted
        reviewed_truth["voiceConversionProven"] = False
        reviewed_manifest["reviewedManifestCopy"] = True
        write_json_atomic(reviewed_manifest_path, reviewed_manifest)

    print(
        f"EchoesStemListeningReview {status} decision={args.decision} "
        f"reviewer={reviewer or 'missing'} acapellaReady={str(operator_accepted).lower()} "
        f"voiceConversionProven=false output={output_path}"
    )
    return 0 if not blockers else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes stem listening review failed: {error}", file=sys.stderr)
        raise SystemExit(2)
