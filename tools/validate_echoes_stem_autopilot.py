#!/usr/bin/env python3
"""Validate Echoes stem runtime, truthful technical QC and no-audio bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

MODEL_SHA256 = "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4"
AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}
EXPECTED_STEMS = {"vocals", "drums", "bass", "other"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expect-attempted", type=int, required=True)
    parser.add_argument("--expect-success", type=int, required=True)
    parser.add_argument("--expect-already", type=int, required=True)
    args = parser.parse_args()

    runtime = args.runtime_root.resolve()
    control = args.control_root.resolve()
    source = args.source.resolve()
    expected_sha = args.expected_source_sha256.lower()
    require(source.is_file(), "Source audio is missing")
    require(sha256_file(source) == expected_sha, "Source audio SHA changed")

    runtime_manifest = load(runtime / "stem-runtime-manifest.json")
    require(runtime_manifest.get("schema") == "echoes.stem-runtime-installation.v1", "Runtime schema mismatch")
    require(runtime_manifest.get("status") == "PASS", "Runtime status is not PASS")
    packages = runtime_manifest.get("packages") or {}
    require(packages.get("demucs") == "4.1.0", "Demucs version mismatch")
    require(str(packages.get("torch", "")).startswith("2.7.1"), "Torch version mismatch")
    require(str(packages.get("torchaudio", "")).startswith("2.7.1"), "Torchaudio version mismatch")
    model = runtime_manifest.get("model") or {}
    require(model.get("sha256") == MODEL_SHA256, "Model SHA mismatch")
    runtime_truth = runtime_manifest.get("truthBoundary") or {}
    require(runtime_truth.get("fullModelSha256Verified") is True, "Model verification not recorded")
    require(runtime_truth.get("sourceAudioDeleted") is False, "Runtime claims source deletion")
    require(runtime_truth.get("sourceAudioUploaded") is False, "Runtime claims source upload")
    require(runtime_truth.get("vocalIsolationProven") is False, "Runtime falsely claims vocal isolation")
    require(runtime_truth.get("vocalIsolationQualityProven") is False, "Runtime falsely claims vocal isolation quality")
    require(runtime_truth.get("technicalStemQcProven") is True, "Runtime technical QC not proven")
    require(runtime_truth.get("acapellaReady") is False, "Runtime falsely claims acapella readiness")
    require((runtime_manifest.get("qcUpdate") or {}).get("status") == "INSTALLED", "QC update not installed")

    report = load(control / "stem-autopilot-report-latest.json")
    require(report.get("schema") == "echoes.stem-autopilot-report.v1", "Report schema mismatch")
    require(report.get("status") == "PASS", "Report status is not PASS")
    summary = report.get("summary") or {}
    require(summary.get("attemptedFiles") == args.expect_attempted, "Unexpected attempted count")
    require(summary.get("successfulFiles") == args.expect_success, "Unexpected success count")
    require(summary.get("alreadySeparatedFiles") == args.expect_already, "Unexpected already-separated count")
    truth = report.get("truthBoundary") or {}
    require(truth.get("scheduledExecutionObserved") is False, "CI must not claim scheduled execution")
    require(truth.get("sourceAudioDeleted") is False, "Report claims source deletion")
    require(truth.get("sourceAudioUploaded") is False, "Report claims source upload")
    require(truth.get("vocalStemGenerated") is True, "Report does not record generated vocal stem")
    require(truth.get("vocalIsolationProven") is False, "Report falsely claims vocal isolation")
    require(truth.get("vocalIsolationQualityProven") is False, "Report falsely claims vocal quality")
    require(truth.get("technicalStemQcProven") is True, "Report technical QC not proven")
    require(truth.get("humanListeningReviewCompleted") is False, "CI falsely claims listening review")
    require(truth.get("acapellaReady") is False, "CI falsely claims acapella readiness")
    technical_qc = report.get("technicalQc") or {}
    require(technical_qc.get("allPass") is True, "Technical QC summary not PASS")
    require(technical_qc.get("blocked") == 0, "Technical QC contains blocked sets")

    ledger = load(control / "stem-autopilot-ledger.json")
    require(ledger.get("schema") == "echoes.stem-autopilot-ledger.v1", "Stem ledger schema mismatch")
    pass_items = [item for item in (ledger.get("items") or []) if isinstance(item, dict) and item.get("status") == "PASS"]
    require(len(pass_items) == 1, "Expected exactly one PASS stem ledger item")
    item = pass_items[0]
    require(item.get("sourceSha256") == expected_sha, "Stem ledger source SHA mismatch")
    require(item.get("technicalStemQcProven") is True, "Ledger technical QC not proven")
    require(item.get("vocalIsolationQualityProven") is False, "Ledger falsely claims vocal quality")

    manifest_path = Path(str(item.get("manifestPath")))
    quality_path = Path(str(item.get("qualityReportPath")))
    require(manifest_path.is_file(), "Stem manifest is missing")
    require(quality_path.is_file(), "Stem quality report is missing")
    require(sha256_file(quality_path) == item.get("qualityReportSha256"), "Quality report SHA mismatch")

    manifest = load(manifest_path)
    require(manifest.get("schema") == "echoes.stem-separation-run.v1", "Stem manifest schema mismatch")
    require(manifest.get("status") == "PASS", "Stem manifest status mismatch")
    require((manifest.get("source") or {}).get("sha256") == expected_sha, "Stem source SHA mismatch")
    require((manifest.get("model") or {}).get("sha256") == MODEL_SHA256, "Stem model SHA mismatch")
    checks = manifest.get("checks") or {}
    for key in (
        "sourceShaVerified", "modelShaVerified", "fourExpectedStemsPresent",
        "allStemHashesRecorded", "allStemsStereo44100", "allStemDurationsMatchSource",
        "sourceAudioPreserved", "technicalStemQcPassed", "qualityReportHashRecorded",
    ):
        require(checks.get(key) is True, f"Stem check failed: {key}")
    stems = manifest.get("stems") or []
    require({entry.get("name") for entry in stems if isinstance(entry, dict)} == EXPECTED_STEMS, "Stem names mismatch")
    for entry in stems:
        stem_path = Path(str(entry.get("path")))
        require(stem_path.is_file(), f"Stem file is missing: {stem_path}")
        require(sha256_file(stem_path) == entry.get("sha256"), f"Stem SHA mismatch: {stem_path}")
        require(entry.get("sampleRate") == 44100 and entry.get("channels") == 2, f"Stem format mismatch: {stem_path}")
    manifest_truth = manifest.get("truthBoundary") or {}
    require(manifest_truth.get("vocalStemGenerated") is True, "Manifest lacks vocal stem evidence")
    require(manifest_truth.get("vocalIsolationProven") is False, "Manifest falsely claims vocal isolation")
    require(manifest_truth.get("vocalIsolationQualityProven") is False, "Manifest falsely claims vocal quality")
    require(manifest_truth.get("technicalStemQcProven") is True, "Manifest technical QC not proven")
    require(manifest_truth.get("humanListeningReviewCompleted") is False, "Manifest falsely claims listening review")
    require(manifest_truth.get("acapellaReady") is False, "Manifest falsely claims acapella readiness")
    require(manifest_truth.get("stemSeparationProven") is True, "Stem separation not proven")
    require(manifest_truth.get("voiceConversionProven") is False, "Manifest falsely claims voice conversion")

    quality = load(quality_path)
    require(quality.get("schema") == "echoes.stem-technical-quality.v1", "Quality schema mismatch")
    require(quality.get("status") == "PASS", "Quality status is not PASS")
    quality_truth = quality.get("truthBoundary") or {}
    require(quality_truth.get("technicalStemQcProven") is True, "Quality proof not recorded")
    require(quality_truth.get("vocalIsolationQualityProven") is False, "Quality falsely claims vocal isolation")
    require(quality_truth.get("musicalUsabilityProven") is False, "Quality falsely claims musical usability")
    require(quality_truth.get("humanListeningReviewCompleted") is False, "Quality falsely claims listening review")
    require(quality_truth.get("acapellaReady") is False, "Quality falsely claims acapella readiness")
    require((quality.get("checks") or {}).get("allFiveStreamsDecoded") is True, "Quality did not decode all streams")
    require((quality.get("checks") or {}).get("allSignalsFinite") is True, "Quality found non-finite samples")

    bundle = control / "Echoes-Stem-Control-Bundle-Latest.zip"
    require(bundle.is_file(), "Stem control bundle is missing")
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        require(names, "Stem control bundle is empty")
        require(not any(Path(name).suffix.lower() in AUDIO_EXTENSIONS for name in names), "Stem control bundle contains audio")
        require(any(name.endswith("stem-separation-manifest.json") for name in names), "Bundle lacks stem manifest")
        require(any(name.endswith("stem-quality-report.json") for name in names), "Bundle lacks quality report")

    print(
        "EchoesStemAutopilotValidation PASS "
        f"source={expected_sha} attempted={args.expect_attempted} success={args.expect_success} "
        f"already={args.expect_already} technical-qc=true vocal-quality=false acapella=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
