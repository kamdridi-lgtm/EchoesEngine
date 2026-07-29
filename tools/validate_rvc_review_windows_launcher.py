#!/usr/bin/env python3
"""Controlled Windows proof for the recovered RVC listening-review wrapper."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

LABELS = ("700", "1000", "1500")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def build_fixture(root: Path) -> Path:
    model_root = root / "models"
    output_root = root / "comparison_output"
    control_root = output_root / "control"
    model_root.mkdir(parents=True)
    control_root.mkdir(parents=True)

    input_path = root / "vocals.wav"
    input_path.write_bytes(b"RIFF" + b"v" * 160)
    index_path = model_root / "model_2.index"
    index_path.write_bytes(b"index-fixture")

    runs: list[dict[str, Any]] = []
    steps = {"700": "63700", "1000": "91000", "1500": "136500"}
    for label in LABELS:
        model_path = model_root / f"model_2_{label}e_{steps[label]}s.pth"
        model_path.write_bytes((f"model-{label}-" * 20).encode())
        output_path = output_root / f"vocals_RVC_{label}E.wav"
        output_path.write_bytes(b"RIFF" + (f"audio-{label}-" * 30).encode())
        runs.append(
            {
                "label": label,
                "status": "PASS",
                "modelPath": str(model_path),
                "modelSha256": sha256(model_path),
                "indexPath": str(index_path),
                "indexSha256": sha256(index_path),
                "inputPath": str(input_path),
                "inputSha256": sha256(input_path),
                "outputPath": str(output_path),
                "outputSha256": sha256(output_path),
                "outputSizeBytes": output_path.stat().st_size,
                "exitCode": 0,
            }
        )

    playlist = output_root / "ECOUTER-700-1000-1500.m3u8"
    playlist.write_text("#EXTM3U\n" + "\n".join(run["outputPath"] for run in runs), encoding="utf-8")
    report = {
        "schema": "echoes.recovered-rvc-comparison-run.v1",
        "status": "PASS",
        "runtime": {"python": sys.executable},
        "fixedParameters": {
            "sameInput": True,
            "sameIndex": True,
            "pitchShiftSemitones": 0,
            "effectsApplied": False,
            "masteringApplied": False,
            "instrumentalMixed": False,
        },
        "runs": runs,
        "playlistPath": str(playlist),
    }
    report_path = control_root / "RECOVERED-RVC-COMPARISON-REPORT.json"
    write_json(report_path, report)
    return report_path


def invoke(script: Path, tool: Path, report: Path, confirm: bool) -> subprocess.CompletedProcess[str]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ComparisonReport",
        str(report),
        "-ToolPath",
        str(tool),
        "-SelectedLabel",
        "1000",
        "-Reviewer",
        "CI Controlled Review",
        "-Notes",
        "fixture",
        "-NoOpen",
    ]
    if confirm:
        command.append("-ConfirmAllListened")
    return subprocess.run(command, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    script = args.script.resolve()
    tool = args.tool.resolve()
    require(script.is_file(), "Review PowerShell launcher is missing")
    require(tool.is_file(), "Review recorder tool is missing")

    with tempfile.TemporaryDirectory(prefix="echoes-rvc-review-") as temp:
        root = Path(temp)
        report = build_fixture(root)
        completed = invoke(script, tool, report, confirm=True)
        require(completed.returncode == 0, f"Valid review wrapper failed:\n{completed.stdout}\n{completed.stderr}")

        review_path = report.parent / "RVC-COMPARISON-LISTENING-REVIEW.json"
        summary_path = report.parent / "SELECTED-RVC-MODEL.txt"
        require(review_path.is_file(), "Review JSON was not created")
        require(summary_path.is_file(), "Selection summary was not created")
        review = json.loads(review_path.read_text(encoding="utf-8-sig"))
        require(review.get("status") == "APPROVED", "Review status is not APPROVED")
        require(review["decision"]["selectedLabel"] == "1000", "Manual selection drifted")
        require(review["truthBoundary"]["modelPromotionExecuted"] is False, "Wrapper promoted a model")
        require("SELECTED CHECKPOINT: 1000 EPOCHS" in summary_path.read_text(encoding="utf-8-sig"), "Summary omitted selection")

        review_path.unlink()
        summary_path.unlink()
        blocked = invoke(script, tool, report, confirm=False)
        require(blocked.returncode != 0, "Missing listening confirmation was not blocked")

        proof = {
            "schema": "echoes.rvc-review-windows-launcher-proof.v1",
            "status": "PASS",
            "cases": {"confirmedManualSelection": "APPROVED", "missingConfirmation": "BLOCKED"},
            "truthBoundary": {
                "windowsWrapperExecuted": True,
                "threeListeningConfirmationsEnforced": True,
                "manualSelectionRecorded": True,
                "selectionSummaryCreated": True,
                "realKamDridiAudioPlayed": False,
                "realHumanListeningPerformed": False,
                "modelPromotionExecuted": False,
            },
        }
        write_json(args.output.resolve(), proof)

    print("EchoesRvcReviewWindowsLauncher PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
