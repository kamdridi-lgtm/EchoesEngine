#!/usr/bin/env python3
"""Prove that RVC input preparation requires authentic approved stem evidence."""
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
        raise AssertionError(f"Expected JSON object: {path}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run_python(tool: Path, arguments: list[str], expected_exit: int) -> subprocess.CompletedProcess[str]:
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
            f"Unexpected exit {completed.returncode}, expected {expected_exit}\n"
            f"stdout={completed.stdout}\nstderr={completed.stderr}"
        )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review-tool", type=Path, required=True)
    parser.add_argument("--preparation-tool", type=Path, required=True)
    parser.add_argument("--capability", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    review_tool = args.review_tool.resolve()
    preparation_tool = args.preparation_tool.resolve()
    capability = load_json(args.capability.resolve())
    require(capability.get("schema") == "echoes.rvc-input-preparation-capability.v1", "Capability schema mismatch")
    require(capability.get("status") == "PARTIAL", "Capability must remain PARTIAL")
    capability_truth = capability.get("truthBoundary") or {}
    require(capability_truth.get("productionRvcInputPrepared") is False, "Capability falsely claims production input")
    require(capability_truth.get("voiceConversionProven") is False, "Capability falsely claims conversion")
    require(capability_truth.get("executionAuthorized") is False, "Capability falsely authorizes execution")

    with tempfile.TemporaryDirectory(prefix="echoes-rvc-input-") as temporary:
        root = Path(temporary)
        source = root / "source.wav"
        source.write_bytes(b"echoes-rvc-source-fixture-v1\n")
        source_sha = sha256_file(source)

        stems_dir = root / "stems"
        stems_dir.mkdir()
        stems: list[dict[str, Any]] = []
        original_vocals = b""
        for index, name in enumerate(EXPECTED_STEMS, start=1):
            path = stems_dir / f"{name}.wav"
            payload = (f"echoes-rvc-{name}-fixture-{index}\n").encode("utf-8")
            path.write_bytes(payload)
            if name == "vocals":
                original_vocals = payload
            stems.append(
                {
                    "name": name,
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "sampleRate": 44100,
                    "channels": 2,
                    "bitDepth": 24,
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

        separation_path = root / "stem-separation-manifest.json"
        separation = {
            "schema": "echoes.stem-separation-run.v1",
            "status": "PASS",
            "source": {"path": str(source), "sha256": source_sha},
            "stems": stems,
            "quality": {
                "reportPath": str(quality_path),
                "reportSha256": sha256_file(quality_path),
                "status": "PASS",
                "technicalStemQcProven": True,
            },
            "checks": {"technicalStemQcPassed": True, "sourceAudioPreserved": True},
            "truthBoundary": {
                "stemSeparationProven": True,
                "technicalStemQcProven": True,
                "humanListeningReviewCompleted": False,
                "vocalIsolationQualityProven": False,
                "acapellaReady": False,
                "voiceConversionInputReady": False,
                "voiceConversionProven": False,
            },
        }
        write_json(separation_path, separation)

        approved_review = root / "approved-review.json"
        approved_manifest = root / "approved-manifest.json"
        run_python(
            review_tool,
            [
                "--separation-manifest", str(separation_path),
                "--quality-report", str(quality_path),
                "--expected-source-sha256", source_sha,
                "--decision", "approve",
                "--reviewer", "rvc-contract-operator",
                "--notes", "Controlled contract fixture only.",
                "--output", str(approved_review),
                "--reviewed-manifest-output", str(approved_manifest),
                "--confirm-listened-to-vocals",
                "--confirm-listened-to-instrumental",
            ],
            0,
        )

        ready_output = root / "rvc-input-ready.json"
        run_python(
            preparation_tool,
            [
                "--reviewed-separation-manifest", str(approved_manifest),
                "--listening-review", str(approved_review),
                "--expected-source-sha256", source_sha,
                "--output", str(ready_output),
            ],
            0,
        )
        ready = load_json(ready_output)
        require(ready.get("status") == "READY", "Approved evidence did not prepare input")
        ready_truth = ready.get("truthBoundary") or {}
        require(ready_truth.get("rvcInputManifestPrepared") is True, "RVC input manifest not prepared")
        require(ready_truth.get("approvedListeningReviewVerified") is True, "Approved review not verified")
        require(ready_truth.get("sourceAndVocalHashesVerified") is True, "Audio hashes not verified")
        require(ready_truth.get("audioCopied") is False, "Preparation copied audio")
        require(ready_truth.get("audioUploaded") is False, "Preparation uploaded audio")
        require(ready_truth.get("rvcRuntimeProven") is False, "Preparation overclaimed RVC runtime")
        require(ready_truth.get("voiceModelProvisioned") is False, "Preparation overclaimed voice model")
        require(ready_truth.get("voiceConversionProven") is False, "Preparation overclaimed conversion")
        require(ready_truth.get("executionAuthorized") is False, "Preparation authorized execution")

        rejected_review = root / "rejected-review.json"
        rejected_manifest = root / "rejected-manifest.json"
        run_python(
            review_tool,
            [
                "--separation-manifest", str(separation_path),
                "--quality-report", str(quality_path),
                "--expected-source-sha256", source_sha,
                "--decision", "reject",
                "--reviewer", "rvc-contract-operator",
                "--notes", "Rejected controlled fixture.",
                "--output", str(rejected_review),
                "--reviewed-manifest-output", str(rejected_manifest),
            ],
            0,
        )
        rejected_output = root / "rvc-input-rejected.json"
        run_python(
            preparation_tool,
            [
                "--reviewed-separation-manifest", str(rejected_manifest),
                "--listening-review", str(rejected_review),
                "--expected-source-sha256", source_sha,
                "--output", str(rejected_output),
            ],
            2,
        )
        rejected = load_json(rejected_output)
        require(rejected.get("status") == "BLOCKED", "Rejected review did not block")
        require("LISTENING_REVIEW_NOT_APPROVED" in (rejected.get("blockers") or []), "Rejected-review blocker missing")
        require((rejected.get("truthBoundary") or {}).get("voiceConversionProven") is False, "Rejected path overclaimed conversion")

        vocals_path = stems_dir / "vocals.wav"
        vocals_path.write_bytes(b"tampered-vocals\n")
        tampered_output = root / "rvc-input-tampered.json"
        run_python(
            preparation_tool,
            [
                "--reviewed-separation-manifest", str(approved_manifest),
                "--listening-review", str(approved_review),
                "--expected-source-sha256", source_sha,
                "--output", str(tampered_output),
            ],
            2,
        )
        tampered = load_json(tampered_output)
        require("VOCAL_STEM_SHA256_MISMATCH" in (tampered.get("blockers") or []), "Tampered vocals did not block")
        vocals_path.write_bytes(original_vocals)

        altered_review = root / "altered-review.json"
        altered_value = load_json(approved_review)
        altered_value["notes"] = "Evidence modified after manifest review reference was recorded."
        write_json(altered_review, altered_value)
        altered_output = root / "rvc-input-altered-review.json"
        run_python(
            preparation_tool,
            [
                "--reviewed-separation-manifest", str(approved_manifest),
                "--listening-review", str(altered_review),
                "--expected-source-sha256", source_sha,
                "--output", str(altered_output),
            ],
            2,
        )
        altered = load_json(altered_output)
        require("REVIEW_REFERENCE_SHA256_MISMATCH" in (altered.get("blockers") or []), "Altered review did not block")

        wrong_source_output = root / "rvc-input-wrong-source.json"
        run_python(
            preparation_tool,
            [
                "--reviewed-separation-manifest", str(approved_manifest),
                "--listening-review", str(approved_review),
                "--expected-source-sha256", "0" * 64,
                "--output", str(wrong_source_output),
            ],
            2,
        )
        wrong_source = load_json(wrong_source_output)
        require("SOURCE_SHA256_MANIFEST_MISMATCH" in (wrong_source.get("blockers") or []), "Wrong source SHA did not block")

        proof = {
            "schema": "echoes.rvc-input-preparation-contract-proof.v1",
            "status": "PASS",
            "capabilityStatus": capability.get("status"),
            "approvedReviewPreparedInput": True,
            "rejectedReviewBlocked": True,
            "tamperedVocalStemBlocked": True,
            "alteredReviewBlocked": True,
            "wrongSourceShaBlocked": True,
            "audioCopied": False,
            "audioUploaded": False,
            "productionRvcInputPrepared": False,
            "kamDridiVocalStemPrepared": False,
            "rvcRuntimeProven": False,
            "voiceModelProvisioned": False,
            "voiceConversionProven": False,
            "executionAuthorized": False,
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, proof)
    print(json.dumps(proof, separators=(",", ":")))
    print(
        "EchoesRvcInputPreparationContract PASS "
        "approved=ready rejected=blocked tamper=blocked altered-review=blocked wrong-source=blocked "
        "runtime=false model=false conversion=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
