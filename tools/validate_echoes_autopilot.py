#!/usr/bin/env python3
"""Validate the one-click Echoes Autopilot Windows proof fail-closed."""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return value


def require(condition: bool, message: str, blockers: list[str]) -> None:
    if not condition:
        blockers.append(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installation", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    installation = load(args.installation)
    report = load(args.report)
    ledger = load(args.ledger)
    blockers: list[str] = []

    require(installation.get("schema") == "echoes.autopilot-installation.v1", "INSTALL_SCHEMA_INVALID", blockers)
    require(installation.get("status") == "PASS", "INSTALL_NOT_PASS", blockers)
    require(installation.get("version") == "1.0.0", "INSTALL_VERSION_DRIFT", blockers)
    require(installation.get("truthBoundary", {}).get("packageInstalledOnCurrentHost") is True, "PACKAGE_NOT_INSTALLED", blockers)
    require(installation.get("truthBoundary", {}).get("audioUploadAuthorized") is False, "AUDIO_UPLOAD_AUTHORIZED", blockers)
    require(installation.get("truthBoundary", {}).get("sourceDeletionAuthorized") is False, "SOURCE_DELETE_AUTHORIZED", blockers)
    require(installation.get("truthBoundary", {}).get("arbitraryRemoteCommandsAuthorized") is False, "ARBITRARY_REMOTE_COMMANDS_AUTHORIZED", blockers)
    require(installation.get("truthBoundary", {}).get("hpOmenExecutionProven") is False, "HP_OMEN_FALSE_BOUNDARY_DRIFT", blockers)

    require(report.get("schema") == "echoes.autopilot-report.v1", "REPORT_SCHEMA_INVALID", blockers)
    require(report.get("status") == "PASS", "REPORT_NOT_PASS", blockers)
    summary = report.get("summary", {})
    require(int(summary.get("discoveredFiles", 0)) == 2, "DISCOVERED_COUNT_DRIFT", blockers)
    require(int(summary.get("attemptedFiles", 0)) == 1, "ATTEMPT_COUNT_DRIFT", blockers)
    require(int(summary.get("successfulFiles", 0)) == 1, "SUCCESS_COUNT_DRIFT", blockers)
    require(int(summary.get("failedFiles", -1)) == 0, "FAILURE_COUNT_DRIFT", blockers)
    require(int(summary.get("duplicateHashFiles", 0)) == 1, "DUPLICATE_HASH_NOT_BLOCKED", blockers)
    truth = report.get("truthBoundary", {})
    require(truth.get("currentHostControllerExecuted") is True, "CONTROLLER_EXECUTION_NOT_PROVEN", blockers)
    require(truth.get("userSongAnalyzed") is True, "USER_SONG_NOT_DECLARED_IN_PROOF", blockers)
    require(truth.get("sourceAudioDeleted") is False, "SOURCE_AUDIO_DELETED", blockers)
    require(truth.get("sourceAudioUploaded") is False, "SOURCE_AUDIO_UPLOADED", blockers)
    for field in (
        "hpOmenExecutionProven",
        "instrumentalClassificationProven",
        "vocalIsolationProven",
        "stemSeparationProven",
        "voiceConversionProven",
        "gpuInferenceProven",
        "tensorRtInferenceProven",
    ):
        require(truth.get(field) is False, f"FALSE_BOUNDARY_DRIFT:{field}", blockers)

    require(ledger.get("schema") == "echoes.autopilot-ledger.v1", "LEDGER_SCHEMA_INVALID", blockers)
    items = ledger.get("items") if isinstance(ledger.get("items"), list) else []
    require(len(items) == 1, "LEDGER_ITEM_COUNT_DRIFT", blockers)
    if items:
        require(items[0].get("status") == "PASS", "LEDGER_ITEM_NOT_PASS", blockers)
        require(int(items[0].get("attempts", 0)) == 1, "LEDGER_ATTEMPTS_DRIFT", blockers)
        require(bool(items[0].get("canonicalTimelineSha256")), "TIMELINE_DIGEST_MISSING", blockers)

    require(args.source.is_file(), "SOURCE_FILE_MISSING_AFTER_RUN", blockers)
    require(args.bundle.is_file(), "CONTROL_BUNDLE_MISSING", blockers)
    bundle_names: list[str] = []
    if args.bundle.is_file():
        with zipfile.ZipFile(args.bundle) as archive:
            bundle_names = sorted(archive.namelist())
            require(any(name.endswith("autopilot-report-latest.json") for name in bundle_names), "BUNDLE_REPORT_MISSING", blockers)
            require(any(name.endswith("autopilot-ledger.json") for name in bundle_names), "BUNDLE_LEDGER_MISSING", blockers)
            require(any(name.endswith("analysis-run-manifest.json") for name in bundle_names), "BUNDLE_ANALYSIS_MANIFEST_MISSING", blockers)
            require(any(name.endswith("song-activity-timeline.json") for name in bundle_names), "BUNDLE_TIMELINE_JSON_MISSING", blockers)
            require(not any(Path(name).suffix.lower() in AUDIO_EXTENSIONS for name in bundle_names), "SOURCE_AUDIO_PRESENT_IN_BUNDLE", blockers)

    result = {
        "schema": "echoes.autopilot-one-click-proof.v1",
        "status": "PASS" if not blockers else "BLOCKED",
        "discoveredFiles": summary.get("discoveredFiles"),
        "attemptedFiles": summary.get("attemptedFiles"),
        "successfulFiles": summary.get("successfulFiles"),
        "duplicateHashFiles": summary.get("duplicateHashFiles"),
        "ledgerItems": len(items),
        "controlBundleFiles": bundle_names,
        "sourceFilePreserved": args.source.is_file(),
        "audioPresentInControlBundle": any(Path(name).suffix.lower() in AUDIO_EXTENSIONS for name in bundle_names),
        "scheduledTaskInstalledInCi": installation.get("automation", {}).get("scheduledTaskInstalled"),
        "hpOmenExecutionProven": False,
        "audioUploadAuthorized": False,
        "sourceDeletionAuthorized": False,
        "blockers": blockers,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, separators=(",", ":")))
    if blockers:
        raise SystemExit(2)
    print("EchoesAutopilotOneClickProof PASS install=verified analysis=real duplicate=blocked source=preserved bundle=no-audio hp-omen=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
