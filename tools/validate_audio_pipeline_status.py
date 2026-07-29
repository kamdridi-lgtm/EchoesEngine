#!/usr/bin/env python3
"""Controlled contract for the Echoes audio pipeline status board."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = "1" * 64
    vocal = "2" * 64
    with tempfile.TemporaryDirectory(prefix="echoes-pipeline-board-") as temp:
        root = Path(temp)
        write(root / "analysis.json", {
            "schema": "echoes.autopilot-report.v1",
            "status": "PASS",
            "items": [{"status": "PASS", "sourceSha256": source, "sourcePath": "War Machines.wav", "jobId": "wm"}],
        })
        write(root / "separation.json", {
            "schema": "echoes.stem-separation-run.v1", "status": "PASS",
            "source": {"name": "War Machines.wav", "sha256": source},
        })
        write(root / "quality.json", {
            "schema": "echoes.stem-technical-quality.v1", "status": "PASS",
            "source": {"name": "War Machines.wav", "sha256": source},
        })
        write(root / "review.json", {
            "schema": "echoes.stem-listening-review.v1", "status": "APPROVED",
            "reviewer": "Kam Dridi", "decision": "approve",
            "inputs": {"sourceSha256": source, "verifiedStemSha256": {"vocals": vocal}},
        })
        write(root / "input.json", {
            "schema": "echoes.rvc-input-manifest.v1", "status": "READY",
            "source": {"name": "War Machines.wav", "sha256": source},
            "vocalInput": {"name": "vocals.wav", "sha256": vocal},
        })
        write(root / "plan.json", {
            "schema": "echoes.rvc-model-comparison-plan.v1", "status": "READY",
            "input": {"vocalSha256": vocal},
        })
        runs = [
            {"label": label, "status": "PASS", "inputSha256": vocal}
            for label in ("700", "1000", "1500")
        ]
        write(root / "run.json", {
            "schema": "echoes.recovered-rvc-comparison-run.v1", "status": "PASS", "runs": runs,
        })
        write(root / "selection.json", {
            "schema": "echoes.rvc-comparison-listening-review.v1", "status": "APPROVED",
            "reviewer": "Kam Dridi",
            "decision": {"selectedLabel": "1000"},
            "comparison": {"runs": runs},
        })

        report_path = root / "status.json"
        text_path = root / "STATUS.txt"
        completed = subprocess.run(
            [sys.executable, str(args.tool), "--root", str(root), "--output", str(report_path), "--text-output", str(text_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        require(completed.returncode == 0, completed.stdout + completed.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        require(report["schema"] == "echoes.audio-pipeline-status-board.v1", "Schema drifted")
        require(report["summary"]["songsDiscovered"] == 1, "Hash linking failed")
        require(report["summary"]["pipelinesComplete"] == 1, "Complete pipeline not detected")
        song = report["songs"][0]
        require(song["currentStage"] == "human_model_selection", "Current stage incorrect")
        require(song["nextRequiredStage"] is None, "Complete pipeline has a next stage")
        require(song["selectedRvcLabel"] == "1000", "Human selection not surfaced")
        truth = report["truthBoundary"]
        for field in ("audioFilesRead", "audioHashesRecomputed", "humanListeningPerformed", "rvcInferenceExecuted", "voiceConversionProvenByThisReport", "executionAuthorized"):
            require(truth[field] is False, f"False capability promoted: {field}")
        require(text_path.is_file() and "selected=1000" in text_path.read_text(encoding="utf-8"), "Readable status missing")

    proof = {
        "schema": "echoes.audio-pipeline-status-board-contract.v1",
        "status": "PASS",
        "truthBoundary": {
            "crossStageHashLinkingProven": True,
            "completePipelineDetectionProven": True,
            "humanSelectionSurfaced": True,
            "realUserAudioRead": False,
            "rvcInferenceExecuted": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("EchoesAudioPipelineStatusContract PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
