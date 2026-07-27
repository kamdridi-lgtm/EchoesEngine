#!/usr/bin/env python3
"""Prepare a truthful RVC input manifest from an approved stem review.

This tool copies no audio, uploads no audio, loads no voice model and performs no
voice conversion. It verifies the reviewed stem evidence and emits only a
hash-addressed preparation manifest for a future, separately proven RVC runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OUTPUT_SCHEMA = "echoes.rvc-input-manifest.v1"


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
    parser.add_argument("--reviewed-separation-manifest", type=Path, required=True)
    parser.add_argument("--listening-review", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--declare-user-song", action="store_true")
    args = parser.parse_args()

    reviewed_manifest_path = args.reviewed_separation_manifest.resolve()
    review_path = args.listening_review.resolve()
    output_path = args.output.resolve()
    expected_source_sha = normalized_sha(args.expected_source_sha256)
    blockers: list[str] = []
    checks: dict[str, bool] = {}

    if not expected_source_sha:
        add_blocker(blockers, "EXPECTED_SOURCE_SHA256_INVALID")
    if not reviewed_manifest_path.is_file():
        add_blocker(blockers, "REVIEWED_SEPARATION_MANIFEST_MISSING")
    if not review_path.is_file():
        add_blocker(blockers, "LISTENING_REVIEW_MISSING")

    reviewed_manifest: dict[str, Any] = {}
    review: dict[str, Any] = {}
    if not blockers:
        reviewed_manifest = load_json(reviewed_manifest_path)
        review = load_json(review_path)

    checks["reviewSchemaValid"] = review.get("schema") == "echoes.stem-listening-review.v1"
    checks["reviewApproved"] = review.get("status") == "APPROVED" and review.get("decision") == "approve"
    reviewer = str(review.get("reviewer") or "").strip()
    checks["reviewerIdentityPresent"] = bool(reviewer)
    confirmations = review.get("confirmations") if isinstance(review.get("confirmations"), dict) else {}
    checks["vocalListeningConfirmed"] = confirmations.get("listenedToVocals") is True
    checks["instrumentalListeningConfirmed"] = confirmations.get("listenedToInstrumental") is True
    checks["automaticApprovalUnused"] = confirmations.get("automaticApprovalUsed") is False
    review_truth = review.get("truthBoundary") if isinstance(review.get("truthBoundary"), dict) else {}
    checks["humanListeningReviewCompleted"] = review_truth.get("humanListeningReviewCompleted") is True
    checks["operatorAcceptedVocalStem"] = review_truth.get("operatorAcceptedVocalStem") is True
    checks["acapellaReady"] = review_truth.get("acapellaReady") is True
    checks["voiceConversionInputReady"] = review_truth.get("voiceConversionInputReady") is True
    checks["reviewDoesNotClaimConversion"] = review_truth.get("voiceConversionProven") is False

    review_requirements = {
        "LISTENING_REVIEW_SCHEMA_INVALID": checks["reviewSchemaValid"],
        "LISTENING_REVIEW_NOT_APPROVED": checks["reviewApproved"],
        "REVIEWER_IDENTITY_MISSING": checks["reviewerIdentityPresent"],
        "VOCAL_LISTENING_CONFIRMATION_MISSING": checks["vocalListeningConfirmed"],
        "INSTRUMENTAL_LISTENING_CONFIRMATION_MISSING": checks["instrumentalListeningConfirmed"],
        "AUTOMATIC_APPROVAL_FORBIDDEN": checks["automaticApprovalUnused"],
        "HUMAN_LISTENING_REVIEW_NOT_COMPLETED": checks["humanListeningReviewCompleted"],
        "VOCAL_STEM_NOT_ACCEPTED": checks["operatorAcceptedVocalStem"],
        "ACAPELLA_NOT_READY": checks["acapellaReady"],
        "VOICE_CONVERSION_INPUT_NOT_READY": checks["voiceConversionInputReady"],
        "REVIEW_FALSELY_CLAIMS_CONVERSION": checks["reviewDoesNotClaimConversion"],
    }
    for blocker, condition in review_requirements.items():
        if not condition:
            add_blocker(blockers, blocker)

    checks["reviewedManifestSchemaValid"] = reviewed_manifest.get("schema") == "echoes.stem-separation-run.v1"
    checks["reviewedManifestStatusPass"] = reviewed_manifest.get("status") == "PASS"
    checks["reviewedManifestCopy"] = reviewed_manifest.get("reviewedManifestCopy") is True
    manifest_truth = (
        reviewed_manifest.get("truthBoundary")
        if isinstance(reviewed_manifest.get("truthBoundary"), dict)
        else {}
    )
    checks["stemSeparationProven"] = manifest_truth.get("stemSeparationProven") is True
    checks["technicalStemQcProven"] = manifest_truth.get("technicalStemQcProven") is True
    checks["manifestListeningReviewCompleted"] = manifest_truth.get("humanListeningReviewCompleted") is True
    checks["manifestAcapellaReady"] = manifest_truth.get("acapellaReady") is True
    checks["manifestVoiceConversionInputReady"] = manifest_truth.get("voiceConversionInputReady") is True
    checks["manifestDoesNotClaimConversion"] = manifest_truth.get("voiceConversionProven") is False

    manifest_requirements = {
        "REVIEWED_MANIFEST_SCHEMA_INVALID": checks["reviewedManifestSchemaValid"],
        "REVIEWED_MANIFEST_NOT_PASS": checks["reviewedManifestStatusPass"],
        "REVIEWED_MANIFEST_COPY_MARKER_MISSING": checks["reviewedManifestCopy"],
        "STEM_SEPARATION_NOT_PROVEN": checks["stemSeparationProven"],
        "TECHNICAL_STEM_QC_NOT_PROVEN": checks["technicalStemQcProven"],
        "MANIFEST_LISTENING_REVIEW_NOT_COMPLETED": checks["manifestListeningReviewCompleted"],
        "MANIFEST_ACAPELLA_NOT_READY": checks["manifestAcapellaReady"],
        "MANIFEST_VOICE_INPUT_NOT_READY": checks["manifestVoiceConversionInputReady"],
        "MANIFEST_FALSELY_CLAIMS_CONVERSION": checks["manifestDoesNotClaimConversion"],
    }
    for blocker, condition in manifest_requirements.items():
        if not condition:
            add_blocker(blockers, blocker)

    review_sha = sha256_file(review_path) if review_path.is_file() else ""
    manifest_review = (
        reviewed_manifest.get("listeningReview")
        if isinstance(reviewed_manifest.get("listeningReview"), dict)
        else {}
    )
    checks["reviewReferenceStatusApproved"] = manifest_review.get("status") == "APPROVED"
    checks["reviewReferenceDecisionApprove"] = manifest_review.get("decision") == "approve"
    checks["reviewReferenceShaVerified"] = normalized_sha(manifest_review.get("reviewSha256")) == review_sha
    checks["reviewReferenceReviewerMatches"] = str(manifest_review.get("reviewer") or "").strip() == reviewer
    for blocker, condition in {
        "REVIEW_REFERENCE_NOT_APPROVED": checks["reviewReferenceStatusApproved"],
        "REVIEW_REFERENCE_DECISION_INVALID": checks["reviewReferenceDecisionApprove"],
        "REVIEW_REFERENCE_SHA256_MISMATCH": checks["reviewReferenceShaVerified"],
        "REVIEW_REFERENCE_REVIEWER_MISMATCH": checks["reviewReferenceReviewerMatches"],
    }.items():
        if not condition:
            add_blocker(blockers, blocker)

    source = reviewed_manifest.get("source") if isinstance(reviewed_manifest.get("source"), dict) else {}
    source_path = Path(str(source.get("path") or ""))
    source_sha = normalized_sha(source.get("sha256"))
    review_inputs = review.get("inputs") if isinstance(review.get("inputs"), dict) else {}
    review_source_sha = normalized_sha(review_inputs.get("sourceSha256"))
    checks["sourceShaMatchesExpected"] = source_sha == expected_source_sha and bool(source_sha)
    checks["reviewSourceShaMatchesExpected"] = review_source_sha == expected_source_sha and bool(review_source_sha)
    checks["sourceFilePresent"] = source_path.is_file()
    checks["sourceFileShaVerified"] = source_path.is_file() and sha256_file(source_path) == expected_source_sha
    for blocker, condition in {
        "SOURCE_SHA256_MANIFEST_MISMATCH": checks["sourceShaMatchesExpected"],
        "SOURCE_SHA256_REVIEW_MISMATCH": checks["reviewSourceShaMatchesExpected"],
        "SOURCE_FILE_MISSING": checks["sourceFilePresent"],
        "SOURCE_FILE_SHA256_MISMATCH": checks["sourceFileShaVerified"],
    }.items():
        if not condition:
            add_blocker(blockers, blocker)

    stem_entries = {
        str(item.get("name")): item
        for item in (reviewed_manifest.get("stems") or [])
        if isinstance(item, dict)
    }
    vocals = stem_entries.get("vocals") if isinstance(stem_entries.get("vocals"), dict) else {}
    vocals_path = Path(str(vocals.get("path") or ""))
    vocals_sha = normalized_sha(vocals.get("sha256"))
    reviewed_stem_hashes = review_inputs.get("verifiedStemSha256")
    if not isinstance(reviewed_stem_hashes, dict):
        reviewed_stem_hashes = {}
    review_vocals_sha = normalized_sha(reviewed_stem_hashes.get("vocals"))
    checks["vocalStemRecorded"] = bool(vocals)
    checks["vocalStemFilePresent"] = vocals_path.is_file()
    checks["vocalStemShaRecorded"] = bool(vocals_sha)
    checks["vocalStemShaVerified"] = vocals_path.is_file() and bool(vocals_sha) and sha256_file(vocals_path) == vocals_sha
    checks["reviewVocalStemShaMatches"] = bool(vocals_sha) and review_vocals_sha == vocals_sha
    for blocker, condition in {
        "VOCAL_STEM_NOT_RECORDED": checks["vocalStemRecorded"],
        "VOCAL_STEM_FILE_MISSING": checks["vocalStemFilePresent"],
        "VOCAL_STEM_SHA256_NOT_RECORDED": checks["vocalStemShaRecorded"],
        "VOCAL_STEM_SHA256_MISMATCH": checks["vocalStemShaVerified"],
        "REVIEW_VOCAL_STEM_SHA256_MISMATCH": checks["reviewVocalStemShaMatches"],
    }.items():
        if not condition:
            add_blocker(blockers, blocker)

    quality = reviewed_manifest.get("quality") if isinstance(reviewed_manifest.get("quality"), dict) else {}
    checks["qualityStatusPass"] = quality.get("status") == "PASS"
    checks["qualityTechnicalProofRecorded"] = quality.get("technicalStemQcProven") is True
    quality_path = Path(str(quality.get("reportPath") or ""))
    quality_sha = normalized_sha(quality.get("reportSha256"))
    checks["qualityReportPresent"] = quality_path.is_file()
    checks["qualityReportShaVerified"] = quality_path.is_file() and bool(quality_sha) and sha256_file(quality_path) == quality_sha
    for blocker, condition in {
        "TECHNICAL_QUALITY_STATUS_NOT_PASS": checks["qualityStatusPass"],
        "TECHNICAL_QUALITY_PROOF_NOT_RECORDED": checks["qualityTechnicalProofRecorded"],
        "TECHNICAL_QUALITY_REPORT_MISSING": checks["qualityReportPresent"],
        "TECHNICAL_QUALITY_REPORT_SHA256_MISMATCH": checks["qualityReportShaVerified"],
    }.items():
        if not condition:
            add_blocker(blockers, blocker)

    ready = not blockers
    result = {
        "schema": OUTPUT_SCHEMA,
        "status": "READY" if ready else "BLOCKED",
        "preparedAtUtc": utc_now(),
        "declaredUserSong": bool(args.declare_user_song),
        "source": {
            "path": str(source_path),
            "name": source_path.name if source_path.name else None,
            "sha256": source_sha or None,
        },
        "vocalInput": {
            "path": str(vocals_path),
            "name": vocals_path.name if vocals_path.name else None,
            "sha256": vocals_sha or None,
            "sampleRate": vocals.get("sampleRate"),
            "channels": vocals.get("channels"),
            "bitDepth": vocals.get("bitDepth"),
        },
        "evidence": {
            "reviewedSeparationManifestPath": str(reviewed_manifest_path),
            "reviewedSeparationManifestSha256": sha256_file(reviewed_manifest_path) if reviewed_manifest_path.is_file() else None,
            "listeningReviewPath": str(review_path),
            "listeningReviewSha256": review_sha or None,
            "technicalQualityReportPath": str(quality_path),
            "technicalQualityReportSha256": quality_sha or None,
            "reviewer": reviewer or None,
        },
        "checks": checks,
        "blockers": blockers,
        "truthBoundary": {
            "rvcInputManifestPrepared": ready,
            "approvedListeningReviewVerified": ready,
            "sourceAndVocalHashesVerified": ready,
            "audioCopied": False,
            "audioUploaded": False,
            "audioModified": False,
            "rvcRuntimeProven": False,
            "voiceModelProvisioned": False,
            "voiceModelInferenceProven": False,
            "voiceConversionProven": False,
            "convertedAudioGenerated": False,
            "requiresOperatorApproval": True,
            "executionAuthorized": False,
        },
    }
    write_json_atomic(output_path, result)
    print(
        f"EchoesRvcInputPreparation {result['status']} source={source_sha or 'invalid'} "
        f"vocals={vocals_sha or 'invalid'} copied=false uploaded=false converted=false output={output_path}"
    )
    return 0 if ready else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Echoes RVC input preparation failed: {error}", file=sys.stderr)
        raise SystemExit(2)
