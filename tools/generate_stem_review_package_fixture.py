#!/usr/bin/env python3
"""Generate a deterministic local fixture for the Windows stem-review package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_STEMS = ("vocals", "drums", "bass", "other")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    runtime = root / "Stem Runtime"
    control = root / "Control"
    results = root / "Results"
    job_dir = results / "analysis-review-fixture" / "stem-separation" / "stem-review-fixture-a1"
    stems_dir = job_dir / "stems"
    for directory in (runtime / "tools", runtime / "config", control / "logs", stems_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source = root / "Kam Dridi Review Fixture.wav"
    source.write_bytes(b"echoes-stem-review-source-fixture-v1\n")
    source_sha = sha256_file(source)

    stem_entries: list[dict[str, Any]] = []
    for index, name in enumerate(EXPECTED_STEMS, start=1):
        path = stems_dir / f"{name}.wav"
        path.write_bytes((f"echoes-stem-review-{name}-fixture-{index}\n").encode("utf-8"))
        stem_entries.append(
            {
                "name": name,
                "path": str(path),
                "sha256": sha256_file(path),
                "sizeBytes": path.stat().st_size,
                "sampleRate": 44100,
                "channels": 2,
                "bitDepth": 24,
                "durationSeconds": 2.0,
            }
        )

    quality_path = job_dir / "stem-quality-report.json"
    quality = {
        "schema": "echoes.stem-technical-quality.v1",
        "status": "PASS",
        "advisoryStatus": "PASS",
        "source": {
            "path": str(source),
            "name": source.name,
            "sha256": source_sha,
            "declaredUserSong": False,
        },
        "checks": {
            "sourceShaVerified": True,
            "allFiveStreamsDecoded": True,
            "allSignalsFinite": True,
            "allSignalsNonEmpty": True,
            "durationAgreementWithin250Ms": True,
            "fourStemFilesPresent": True,
        },
        "blockers": [],
        "advisories": [],
        "truthBoundary": {
            "technicalStemQcExecuted": True,
            "technicalStemQcProven": True,
            "vocalStemGenerated": True,
            "fourStemFilesGenerated": True,
            "subjectiveIsolationQualityProven": False,
            "vocalIsolationQualityProven": False,
            "musicalUsabilityProven": False,
            "acapellaReady": False,
            "instrumentalMixReady": False,
            "voiceConversionProven": False,
            "humanListeningReviewCompleted": False,
        },
    }
    write_json(quality_path, quality)

    manifest_path = job_dir / "stem-separation-manifest.json"
    manifest = {
        "schema": "echoes.stem-separation-run.v1",
        "status": "PASS",
        "source": {
            "path": str(source),
            "name": source.name,
            "sha256": source_sha,
            "sizeBytes": source.stat().st_size,
        },
        "model": {
            "id": "htdemucs",
            "sha256": "8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4",
        },
        "stems": stem_entries,
        "quality": {
            "reportPath": str(quality_path),
            "reportSha256": sha256_file(quality_path),
            "status": "PASS",
            "advisoryStatus": "PASS",
            "technicalStemQcProven": True,
            "vocalIsolationQualityProven": False,
            "humanListeningReviewCompleted": False,
        },
        "checks": {
            "sourceShaVerified": True,
            "modelShaVerified": True,
            "fourExpectedStemsPresent": True,
            "allStemHashesRecorded": True,
            "allStemsStereo44100": True,
            "allStemDurationsMatchSource": True,
            "sourceAudioPreserved": True,
            "technicalStemQcPassed": True,
            "qualityReportHashRecorded": True,
        },
        "truthBoundary": {
            "userSongSeparated": False,
            "vocalStemGenerated": True,
            "vocalIsolationProven": False,
            "vocalIsolationQualityProven": False,
            "technicalStemQcProven": True,
            "humanListeningReviewCompleted": False,
            "acapellaReady": False,
            "instrumentalMixCreated": False,
            "stemSeparationProven": True,
            "voiceConversionInputReady": False,
            "voiceConversionProven": False,
        },
    }
    write_json(manifest_path, manifest)

    ledger_path = control / "stem-autopilot-ledger.json"
    ledger = {
        "schema": "echoes.stem-autopilot-ledger.v1",
        "version": 1,
        "items": [
            {
                "sourceSha256": source_sha,
                "sourcePath": str(source),
                "sourceName": source.name,
                "analysisJobId": "analysis-review-fixture",
                "stemJobId": "stem-review-fixture-a1",
                "status": "PASS",
                "attempts": 1,
                "manifestPath": str(manifest_path),
                "qualityReportPath": str(quality_path),
                "technicalStemQcProven": True,
                "vocalIsolationQualityProven": False,
                "usedDevice": "cpu",
            }
        ],
    }
    write_json(ledger_path, ledger)

    runtime_manifest_path = runtime / "stem-runtime-manifest.json"
    runtime_manifest = {
        "schema": "echoes.stem-runtime-installation.v1",
        "status": "PASS",
        "installRoot": str(runtime),
        "installedFileSha256": {},
        "truthBoundary": {
            "fullModelSha256Verified": True,
            "sourceAudioDeleted": False,
            "sourceAudioUploaded": False,
            "technicalStemQcProven": True,
            "humanListeningReviewCompleted": False,
            "acapellaReady": False,
            "voiceConversionProven": False,
        },
    }
    write_json(runtime_manifest_path, runtime_manifest)

    fixture = {
        "schema": "echoes.stem-review-package-fixture.v1",
        "root": str(root),
        "runtimeRoot": str(runtime),
        "controlRoot": str(control),
        "resultsRoot": str(results),
        "sourcePath": str(source),
        "sourceSha256": source_sha,
        "stemJobId": "stem-review-fixture-a1",
        "manifestPath": str(manifest_path),
        "manifestSha256": sha256_file(manifest_path),
        "qualityReportPath": str(quality_path),
        "qualityReportSha256": sha256_file(quality_path),
        "ledgerPath": str(ledger_path),
        "runtimeManifestPath": str(runtime_manifest_path),
    }
    write_json(args.output.resolve(), fixture)
    print(json.dumps(fixture, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
