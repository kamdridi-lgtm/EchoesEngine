#!/usr/bin/env python3
"""Prove the Echoes operator listening gate without claiming a production review."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
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


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AssertionError(f"Expected object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_tool(tool: Path, arguments: list[str], expected_exit: int) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, str(tool), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != expected_exit:
        raise AssertionError(
            f"Unexpected review-tool exit {completed.returncode}, expected {expected_exit}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--capability", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    tool = args.tool.resolve()
    capability = load_json(args.capability.resolve())
    require(capability.get("schema") == "echoes.stem-listening-review-capability.v1", "Capability schema mismatch")
    require(capability.get("status") == "PARTIAL", "Capability must remain PARTIAL before production review")
    contract = capability.get("reviewContract") or {}
    require(contract.get("automaticApprovalForbidden") is True, "Automatic approval must be forbidden")
    capability_truth = capability.get("truthBoundary") or {}
    require(capability_truth.get("productionListeningReviewCompleted") is False, "Capability falsely claims production review")
    require(capability_truth.get("kamDridiVocalStemApproved") is False, "Capability falsely claims Kam Dridi approval")
    require(capability_truth.get("voiceConversionProven") is False, "Capability falsely claims voice conversion")

    with tempfile.TemporaryDirectory(prefix="echoes-stem-review-") as temporary:
        root = Path(temporary)
        source = root / "source.wav"
        source.write_bytes(b"echoes-source-fixture-v1\n")
        source_sha = sha256_file(source)

        stems_dir = root / "stems"
        stems_dir.mkdir()
        stem_entries: list[dict[str, Any]] = []
        original_stem_payloads: dict[str, bytes] = {}
        for index, name in enumerate(EXPECTED_STEMS, start=1):
            path = stems_dir / f"{name}.wav"
            payload = (f"echoes-{name}-fixture-{index}\n").encode("utf-8")
            original_stem_payloads[name] = payload
            path.write_bytes(payload)
            stem_entries.append(
                {
                    "name": name,
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "sampleRate": 44100,
                    "channels": 2,
                }
            )

        quality_path = root / "stem-quality-report.json"
        quality = {
            "schema": "echoes.stem-technical-quality.v1",
            "status": "PASS",
            "advisoryStatus": "PASS",
            "source": {"path": str(source), "sha256": source_sha},
            "checks": {"allFiveStreamsDecoded": True, "allSignalsFinite": True},
            "blockers": [],
            "truthBoundary": {
                "technicalStemQcProven": True,
                "vocalIsolationQualityProven": False,
                "humanListeningReviewCompleted": False,
                "acapellaReady": False,
                "voiceConversionProven": False,
            },
        }
        write_json(quality_path, quality)

        manifest_path = root / "stem-separation-manifest.json"
        manifest = {
            "schema": "echoes.stem-separation-run.v1",
            "status": "PASS",
            "source": {"path": str(source), "sha256": source_sha},
            "stems": stem_entries,
            "quality": {
                "reportPath": str(quality_path),
                "reportSha256": sha256_file(quality_path),
                "status": "PASS",
                "technicalStemQcProven": True,
            },
            "checks": {
                "technicalStemQcPassed": True,
                "sourceAudioPreserved": True,
            },
            "truthBoundary": {
                "stemSeparationProven": True,
                "technicalStemQcProven": True,
                "humanListeningReviewCompleted": False,
                "vocalIsolationQualityProven": False,
                "acapellaReady": False,
                "voiceConversionProven": False,
            },
        }
        write_json(manifest_path, manifest)
        original_manifest_sha = sha256_file(manifest_path)

        approved_review = root / "approved-review.json"
        approved_manifest = root / "approved-manifest-copy.json"
        run_tool(
            tool,
            [
                "--separation-manifest", str(manifest_path),
                "--quality-report", str(quality_path),
                "--expected-source-sha256", source_sha,
                "--decision", "approve",
                "--reviewer", "contract-test-operator",
                "--notes", "Controlled contract fixture only; not a Kam Dridi production review.",
                "--output", str(approved_review),
                "--reviewed-manifest-output", str(approved_manifest),
                "--confirm-listened-to-vocals",
                "--confirm-listened-to-instrumental",
            ],
            0,
        )
        approved = load_json(approved_review)
        approved_copy = load_json(approved_manifest)
        require(approved.get("status") == "APPROVED", "Approved fixture status mismatch")
        approved_truth = approved.get("truthBoundary") or {}
        require(approved_truth.get("humanListeningReviewCompleted") is True, "Approved review not completed")
        require(approved_truth.get("acapellaReady") is True, "Approved review did not open acapella gate")
        require(approved_truth.get("voiceConversionInputReady") is True, "Approved review did not open input gate")
        require(approved_truth.get("vocalIsolationQualityProven") is False, "Subjective quality was overclaimed")
        require(approved_truth.get("voiceConversionProven") is False, "Voice conversion was overclaimed")
        require((approved_copy.get("truthBoundary") or {}).get("acapellaReady") is True, "Reviewed copy not updated")
        require(sha256_file(manifest_path) == original_manifest_sha, "Original manifest was mutated")

        rejected_review = root / "rejected-review.json"
        run_tool(
            tool,
            [
                "--separation-manifest", str(manifest_path),
                "--quality-report", str(quality_path),
                "--expected-source-sha256", source_sha,
                "--decision", "reject",
                "--reviewer", "contract-test-operator",
                "--notes", "Rejected controlled fixture.",
                "--output", str(rejected_review),
            ],
            0,
        )
        rejected = load_json(rejected_review)
        require(rejected.get("status") == "REJECTED", "Rejected fixture status mismatch")
        rejected_truth = rejected.get("truthBoundary") or {}
        require(rejected_truth.get("humanListeningReviewCompleted") is True, "Rejected review not recorded")
        require(rejected_truth.get("acapellaReady") is False, "Rejected review opened acapella gate")
        require(rejected_truth.get("voiceConversionInputReady") is False, "Rejected review opened conversion input gate")

        missing_confirmation = root / "missing-confirmation-review.json"
        run_tool(
            tool,
            [
                "--separation-manifest", str(manifest_path),
                "--quality-report", str(quality_path),
                "--expected-source-sha256", source_sha,
                "--decision", "approve",
                "--reviewer", "contract-test-operator",
                "--output", str(missing_confirmation),
            ],
            2,
        )
        missing = load_json(missing_confirmation)
        require(missing.get("status") == "BLOCKED", "Missing confirmation did not block")
        require("LISTENING_CONFIRMATION_MISSING:vocals" in (missing.get("blockers") or []), "Vocal confirmation blocker missing")
        require("LISTENING_CONFIRMATION_MISSING:instrumental" in (missing.get("blockers") or []), "Instrumental confirmation blocker missing")

        vocals_path = stems_dir / "vocals.wav"
        vocals_path.write_bytes(b"tampered-vocal-stem\n")
        tampered_review = root / "tampered-review.json"
        run_tool(
            tool,
            [
                "--separation-manifest", str(manifest_path),
                "--quality-report", str(quality_path),
                "--expected-source-sha256", source_sha,
                "--decision", "reject",
                "--reviewer", "contract-test-operator",
                "--output", str(tampered_review),
            ],
            2,
        )
        tampered = load_json(tampered_review)
        require("STEM_SHA256_MISMATCH:vocals" in (tampered.get("blockers") or []), "Tampered stem was not blocked")
        vocals_path.write_bytes(original_stem_payloads["vocals"])

        blocked_quality_path = root / "blocked-quality.json"
        blocked_quality = dict(quality)
        blocked_quality["status"] = "BLOCKED"
        blocked_quality["blockers"] = ["CONTROLLED_QC_FAILURE"]
        blocked_quality["truthBoundary"] = dict(quality["truthBoundary"])
        blocked_quality["truthBoundary"]["technicalStemQcProven"] = False
        write_json(blocked_quality_path, blocked_quality)
        blocked_manifest_path = root / "blocked-manifest.json"
        blocked_manifest = json.loads(json.dumps(manifest))
        blocked_manifest["quality"]["reportPath"] = str(blocked_quality_path)
        blocked_manifest["quality"]["reportSha256"] = sha256_file(blocked_quality_path)
        blocked_manifest["quality"]["status"] = "BLOCKED"
        blocked_manifest["quality"]["technicalStemQcProven"] = False
        blocked_manifest["checks"]["technicalStemQcPassed"] = False
        blocked_manifest["truthBoundary"]["technicalStemQcProven"] = False
        write_json(blocked_manifest_path, blocked_manifest)
        blocked_review = root / "blocked-quality-review.json"
        run_tool(
            tool,
            [
                "--separation-manifest", str(blocked_manifest_path),
                "--quality-report", str(blocked_quality_path),
                "--expected-source-sha256", source_sha,
                "--decision", "approve",
                "--reviewer", "contract-test-operator",
                "--output", str(blocked_review),
                "--confirm-listened-to-vocals",
                "--confirm-listened-to-instrumental",
            ],
            2,
        )
        blocked = load_json(blocked_review)
        require("TECHNICAL_QUALITY_NOT_PASS" in (blocked.get("blockers") or []), "Blocked QC status was not enforced")
        require("TECHNICAL_STEM_QC_NOT_PROVEN" in (blocked.get("blockers") or []), "Missing QC proof was not enforced")
        require((blocked.get("truthBoundary") or {}).get("acapellaReady") is False, "Blocked QC opened acapella gate")

        proof = {
            "schema": "echoes.stem-listening-review-contract-proof.v1",
            "status": "PASS",
            "capabilityStatus": capability.get("status"),
            "approvalContractPassed": True,
            "rejectionContractPassed": True,
            "missingConfirmationBlocked": True,
            "tamperedStemBlocked": True,
            "failedTechnicalQcBlocked": True,
            "originalManifestPreserved": sha256_file(manifest_path) == original_manifest_sha,
            "automaticApprovalUsed": False,
            "productionListeningReviewCompleted": False,
            "kamDridiVocalStemApproved": False,
            "vocalIsolationQualityProven": False,
            "voiceConversionProven": False,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, proof)
    print(json.dumps(proof, separators=(",", ":")))
    print(
        "EchoesStemListeningReviewContract PASS "
        "approve=true reject=true missing-confirmation=blocked tamper=blocked qc-failure=blocked "
        "production-review=false voice-conversion=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
