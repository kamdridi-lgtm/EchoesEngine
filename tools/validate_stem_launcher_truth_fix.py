#!/usr/bin/env python3
"""Static contract for the Windows stem launcher truth fixes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    text = args.launcher.read_text(encoding="utf-8-sig")
    required_tokens = {
        "runtime manifest lookup": "stem-runtime-manifest.json",
        "recorded ffprobe lookup": ".ffmpeg.ffprobe",
        "ffprobe path validation": "Test-Path -LiteralPath $ffprobe",
        "PATH propagation": "$env:PATH = $ffprobeDirectory + [IO.Path]::PathSeparator + $env:PATH",
        "latest report lookup": "stem-autopilot-report-latest.json",
        "missing source count": "missingOrChangedSources",
        "truthful nonzero exit": "$exitCode = 2",
    }
    for label, token in required_tokens.items():
        require(token in text, f"Launcher lacks {label}: {token}")

    require(text.index("$env:PATH =") < text.index("& $python @arguments"), "ffprobe PATH must be set before execution")
    require(text.index("missingOrChangedSources") > text.index("& $python @arguments"), "missing-source evidence must be checked after execution")
    require("exit $exitCode" in text, "Launcher must propagate the corrected exit code")

    proof = {
        "schema": "echoes.stem-launcher-truth-fix-proof.v1",
        "status": "PASS",
        "truthBoundary": {
            "resolvedFfprobePropagationEnforced": True,
            "missingOrChangedSourceCannotReportSuccess": True,
            "realStemInferenceExecuted": False,
            "userAudioRead": False,
            "hpOmenExecutionProven": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("EchoesStemLauncherTruthFix PASS ffprobe=recorded missing-source=nonzero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
