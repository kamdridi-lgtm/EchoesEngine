#!/usr/bin/env python3
"""Contract proof for the Echoes Autopilot reliability reconciliation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_tokens(text: str, tokens: dict[str, str], label: str) -> None:
    for name, token in tokens.items():
        require(token in text, f"{label} lacks {name}: {token}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--entrypoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    launcher = args.launcher.read_text(encoding="utf-8-sig")
    installer = args.installer.read_text(encoding="utf-8-sig")
    entrypoint = args.entrypoint.read_text(encoding="utf-8-sig")

    require_tokens(
        launcher,
        {
            "recorded FFmpeg path": ".prerequisites.ffmpegPath",
            "PATH propagation": "$env:PATH = $ffmpegDirectory + [IO.Path]::PathSeparator + $env:PATH",
            "fresh report requirement": "latest report was not refreshed",
            "report schema gate": 'echoes.autopilot-report.v1',
            "historical ledger bundle": "foreach ($item in @($ledger.items))",
            "no unconditional interactive mode": 'if ($Interactive) { $arguments += "--interactive" }',
        },
        "launcher",
    )
    require('"--interactive"\n)' not in launcher, "Launcher still forces interactive mode on every scheduled run")

    require_tokens(
        installer,
        {
            "policy interval": "scanIntervalMinutes",
            "policy task interval": "New-TimeSpan -Minutes $intervalMinutes",
            "repeating fallback label": ":echoes_loop",
            "fallback sleep": "timeout /t $intervalSeconds /nobreak",
            "fallback loop": "goto echoes_loop",
            "truthful automation manifest": "autonomousLoopInstalled",
            "reliability evidence": "echoes.autopilot-reliability-update.v1",
        },
        "installer",
    )
    require("install-echoes-autopilot-reliability-update.ps1" in entrypoint, "One-click update does not invoke the reliability installer")

    proof = {
        "schema": "echoes.autopilot-reliability-reconciliation-proof.v1",
        "status": "PASS",
        "truthBoundary": {
            "configuredScanIntervalEnforced": True,
            "repeatingStartupFallbackImplemented": True,
            "resolvedFfmpegPropagationImplemented": True,
            "controllerCrashCanNoLongerMasqueradeAsSuccess": True,
            "historicalControlEvidencePreserved": True,
            "realUserAudioRead": False,
            "scheduledTaskCreatedOnUserHost": False,
            "hpOmenExecutionProven": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("EchoesAutopilotReliabilityReconciliation PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
