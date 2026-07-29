#!/usr/bin/env python3
"""Record a human listening decision for a verified RVC 700/1000/1500 run."""
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

REPORT_SCHEMA = "echoes.recovered-rvc-comparison-run.v1"
OUTPUT_SCHEMA = "echoes.rvc-comparison-listening-review.v1"
LABELS = ("700", "1000", "1500")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    require(isinstance(value, dict), f"Expected JSON object: {path}")
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


def validate_run(label: str, run: dict[str, Any]) -> dict[str, Any]:
    require(str(run.get("label")) == label, f"Comparison run order drifted at {label}")
    require(run.get("status") == "PASS", f"Comparison run {label} is not PASS")

    output_path = Path(str(run.get("outputPath") or "")).resolve()
    output_sha = normalized_sha(run.get("outputSha256"))
    require(output_path.is_file(), f"Comparison output is missing for {label}")
    require(bool(output_sha), f"Comparison output SHA-256 is invalid for {label}")
    require(sha256_file(output_path) == output_sha, f"Comparison output SHA-256 mismatch for {label}")
    require(output_path.stat().st_size > 44, f"Comparison output is too small for {label}")

    model_path = Path(str(run.get("modelPath") or "")).resolve()
    model_sha = normalized_sha(run.get("modelSha256"))
    require(model_path.is_file(), f"Comparison model is missing for {label}")
    require(bool(model_sha), f"Comparison model SHA-256 is invalid for {label}")
    require(sha256_file(model_path) == model_sha, f"Comparison model SHA-256 mismatch for {label}")

    input_path = Path(str(run.get("inputPath") or "")).resolve()
    input_sha = normalized_sha(run.get("inputSha256"))
    require(input_path.is_file(), f"Comparison input is missing for {label}")
    require(bool(input_sha), f"Comparison input SHA-256 is invalid for {label}")
    require(sha256_file(input_path) == input_sha, f"Comparison input SHA-256 mismatch for {label}")

    index_path = Path(str(run.get("indexPath") or "")).resolve()
    index_sha = normalized_sha(run.get("indexSha256"))
    require(index_path.is_file(), f"Comparison index is missing for {label}")
    require(bool(index_sha), f"Comparison index SHA-256 is invalid for {label}")
    require(sha256_file(index_path) == index_sha, f"Comparison index SHA-256 mismatch for {label}")

    return {
        "label": label,
        "outputPath": str(output_path),
        "outputSha256": output_sha,
        "outputSizeBytes": output_path.stat().st_size,
        "modelPath": str(model_path),
        "modelSha256": model_sha,
        "inputPath": str(input_path),
        "inputSha256": input_sha,
        "indexPath": str(index_path),
        "indexSha256": index_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison-report", type=Path, required=True)
    parser.add_argument("--selected-label", choices=LABELS, required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-listened-700", action="store_true")
    parser.add_argument("--confirm-listened-1000", action="store_true")
    parser.add_argument("--confirm-listened-1500", action="store_true")
    parser.add_argument("--confirm-manual-selection", action="store_true")
    args = parser.parse_args()

    confirmations = {
        "700": bool(args.confirm_listened_700),
        "1000": bool(args.confirm_listened_1000),
        "1500": bool(args.confirm_listened_1500),
    }
    for label, confirmed in confirmations.items():
        require(confirmed, f"LISTENING_CONFIRMATION_MISSING_{label}")
    require(args.confirm_manual_selection, "MANUAL_SELECTION_CONFIRMATION_MISSING")

    reviewer = args.reviewer.strip()
    require(bool(reviewer), "REVIEWER_IDENTITY_MISSING")

    report_path = args.comparison_report.resolve()
    require(report_path.is_file(), "COMPARISON_REPORT_MISSING")
    report = load_json(report_path)
    require(report.get("schema") == REPORT_SCHEMA, "COMPARISON_REPORT_SCHEMA_INVALID")
    require(report.get("status") == "PASS", "COMPARISON_REPORT_NOT_PASS")

    fixed = report.get("fixedParameters") if isinstance(report.get("fixedParameters"), dict) else {}
    require(fixed.get("sameInput") is True, "COMPARISON_INPUT_NOT_SHARED")
    require(fixed.get("sameIndex") is True, "COMPARISON_INDEX_NOT_SHARED")
    require(fixed.get("pitchShiftSemitones") == 0, "COMPARISON_PITCH_DRIFTED")
    require(fixed.get("effectsApplied") is False, "COMPARISON_EFFECTS_DRIFTED")
    require(fixed.get("masteringApplied") is False, "COMPARISON_MASTERING_DRIFTED")
    require(fixed.get("instrumentalMixed") is False, "COMPARISON_MIX_DRIFTED")

    raw_runs = report.get("runs")
    require(isinstance(raw_runs, list) and len(raw_runs) == 3, "COMPARISON_RUN_COUNT_INVALID")
    verified_runs = [validate_run(label, raw_runs[index]) for index, label in enumerate(LABELS)]
    require(len({item["modelSha256"] for item in verified_runs}) == 3, "COMPARISON_MODELS_NOT_DISTINCT")
    require(len({item["inputSha256"] for item in verified_runs}) == 1, "COMPARISON_INPUT_HASH_DRIFTED")
    require(len({item["indexSha256"] for item in verified_runs}) == 1, "COMPARISON_INDEX_HASH_DRIFTED")

    selected = next(item for item in verified_runs if item["label"] == args.selected_label)
    result = {
        "schema": OUTPUT_SCHEMA,
        "version": "1.0.0",
        "status": "APPROVED",
        "reviewedAtUtc": utc_now(),
        "reviewer": reviewer,
        "decision": {
            "selectedLabel": args.selected_label,
            "selectedModelPath": selected["modelPath"],
            "selectedModelSha256": selected["modelSha256"],
            "selectedOutputPath": selected["outputPath"],
            "selectedOutputSha256": selected["outputSha256"],
            "notes": args.notes.strip() or None,
        },
        "listeningConfirmations": {
            "listenedTo700": confirmations["700"],
            "listenedTo1000": confirmations["1000"],
            "listenedTo1500": confirmations["1500"],
            "manualSelectionConfirmed": True,
            "automaticSelectionUsed": False,
        },
        "comparison": {
            "reportPath": str(report_path),
            "reportSha256": sha256_file(report_path),
            "runs": verified_runs,
        },
        "truthBoundary": {
            "allThreeOutputsIntegrityVerified": True,
            "allThreeListeningConfirmationsRecorded": True,
            "humanCheckpointSelectionRecorded": True,
            "automaticWinnerSelectionUsed": False,
            "modelPromotionExecuted": False,
            "modelFilesModified": False,
            "audioFilesModified": False,
            "audioUploaded": False,
            "productionDeploymentAuthorized": False,
        },
    }
    write_json_atomic(args.output.resolve(), result)
    print(
        "EchoesRvcComparisonListeningReview APPROVED "
        f"reviewer={reviewer!r} selected={args.selected_label} automatic=false promotion=false"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"EchoesRvcComparisonListeningReview BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2)
