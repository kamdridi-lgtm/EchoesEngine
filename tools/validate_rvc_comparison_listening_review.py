#!/usr/bin/env python3
"""Cross-platform proof for manual RVC 700/1000/1500 listening review."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


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


def build_fixture(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    input_path = root / "approved vocals.wav"
    index_path = root / "model_2.index"
    input_path.write_bytes(b"RIFF" + b"approved-vocal" * 16)
    index_path.write_bytes(b"shared-index" * 64)
    runs = []
    for label in ("700", "1000", "1500"):
        model_path = root / f"model_2_{label}e.pth"
        output_path = root / f"approved-vocals_RVC_{label}E.wav"
        model_path.write_bytes((f"model-{label}".encode() + b"\n") * 64)
        output_path.write_bytes(b"RIFF" + (f"output-{label}".encode() * 32))
        runs.append(
            {
                "label": label,
                "status": "PASS",
                "modelPath": str(model_path),
                "modelSha256": sha256_file(model_path),
                "indexPath": str(index_path),
                "indexSha256": sha256_file(index_path),
                "inputPath": str(input_path),
                "inputSha256": sha256_file(input_path),
                "outputPath": str(output_path),
                "outputSha256": sha256_file(output_path),
                "outputSizeBytes": output_path.stat().st_size,
            }
        )
    report = {
        "schema": "echoes.recovered-rvc-comparison-run.v1",
        "status": "PASS",
        "fixedParameters": {
            "sameInput": True,
            "sameIndex": True,
            "pitchShiftSemitones": 0,
            "effectsApplied": False,
            "masteringApplied": False,
            "instrumentalMixed": False,
        },
        "runs": runs,
        "truthBoundary": {
            "localApplioRuntimeExecuted": True,
            "threeConversionsExecuted": True,
            "threeOutputFilesVerified": True,
            "bestModelSelected": False,
            "audioUploaded": False,
        },
    }
    report_path = root / "RECOVERED-RVC-COMPARISON-REPORT.json"
    write_json(report_path, report)
    return {"report": report_path, "tamper": Path(runs[1]["outputPath"])}


def invoke(tool: Path, report: Path, output: Path, include_all_confirmations: bool = True) -> tuple[int, dict[str, Any], str]:
    command = [
        sys.executable,
        str(tool),
        "--comparison-report",
        str(report),
        "--selected-label",
        "1000",
        "--reviewer",
        "Kam Dridi",
        "--notes",
        "Controlled listening fixture",
        "--output",
        str(output),
        "--confirm-listened-700",
        "--confirm-listened-1000",
        "--confirm-manual-selection",
    ]
    if include_all_confirmations:
        command.append("--confirm-listened-1500")
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    review = load_json(output) if output.is_file() else {}
    return completed.returncode, review, completed.stdout + completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tool = args.tool.resolve()
    require(tool.is_file(), "Listening review recorder is missing")

    cases: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="echoes-rvc-listening-review-") as temporary:
        root = Path(temporary)

        valid = build_fixture(root / "valid")
        valid_output = root / "valid-review.json"
        code, review, log = invoke(tool, valid["report"], valid_output, include_all_confirmations=True)
        require(code == 0, f"Valid listening review failed: {log}")
        require(review.get("status") == "APPROVED", "Valid review was not approved")
        require((review.get("decision") or {}).get("selectedLabel") == "1000", "Manual selection drifted")
        confirmations = review.get("listeningConfirmations") or {}
        require(confirmations.get("listenedTo700") is True, "700 confirmation missing")
        require(confirmations.get("listenedTo1000") is True, "1000 confirmation missing")
        require(confirmations.get("listenedTo1500") is True, "1500 confirmation missing")
        require(confirmations.get("automaticSelectionUsed") is False, "Automatic selection was used")
        truth = review.get("truthBoundary") or {}
        require(truth.get("humanCheckpointSelectionRecorded") is True, "Human selection truth boundary missing")
        require(truth.get("modelPromotionExecuted") is False, "Review falsely promoted a model")
        cases["validManualSelection"] = {
            "status": review.get("status"),
            "selectedLabel": (review.get("decision") or {}).get("selectedLabel"),
        }

        missing = build_fixture(root / "missing-confirmation")
        missing_output = root / "missing-confirmation-review.json"
        code, review, log = invoke(tool, missing["report"], missing_output, include_all_confirmations=False)
        require(code == 2, f"Missing listening confirmation did not block: {log}")
        require(not review, "Missing confirmation unexpectedly emitted a review")
        require("LISTENING_CONFIRMATION_MISSING_1500" in log, "Missing confirmation blocker was not reported")
        cases["missingListeningConfirmation"] = "BLOCKED"

        tampered = build_fixture(root / "tampered-output")
        tampered["tamper"].write_bytes(b"tampered-output")
        tampered_output = root / "tampered-output-review.json"
        code, review, log = invoke(tool, tampered["report"], tampered_output, include_all_confirmations=True)
        require(code == 2, f"Tampered output did not block: {log}")
        require(not review, "Tampered output unexpectedly emitted a review")
        require("output SHA-256 mismatch for 1000" in log, "Tampered output blocker was not reported")
        cases["tamperedOutput"] = "BLOCKED"

    proof = {
        "schema": "echoes.rvc-comparison-listening-review-contract-proof.v1",
        "status": "PASS",
        "cases": cases,
        "truthBoundary": {
            "manualReviewContractProven": True,
            "allThreeConfirmationsRequired": True,
            "outputIntegrityEnforced": True,
            "realKamDridiAudioPlayed": False,
            "realHumanListeningPerformed": False,
            "automaticWinnerSelectionUsed": False,
            "modelPromotionExecuted": False,
            "audioUploaded": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, proof)
    print("RvcComparisonListeningReviewContract PASS cases=3 human_fixture_only=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
