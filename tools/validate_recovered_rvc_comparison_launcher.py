#!/usr/bin/env python3
"""Windows orchestration proof for the recovered RVC comparison launcher."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AssertionError(f"Expected JSON object: {path}")
    return value


def write_stub_core(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import sys
from pathlib import Path

args = sys.argv[1:]
if not args or args[0] != 'infer':
    raise SystemExit(2)
if '--help' in args:
    print('stub infer --input_path --output_path --pth_path --index_path')
    raise SystemExit(0)

def value(flag):
    index = args.index(flag)
    return args[index + 1]

for required in ('--input_path', '--output_path', '--pth_path', '--index_path'):
    if required not in args:
        raise SystemExit(3)
output = Path(value('--output_path'))
output.parent.mkdir(parents=True, exist_ok=True)
payload = b'RIFF' + (b'controlled-rvc-launcher-fixture-' * 8)
output.write_bytes(payload)
print(f'created {output}')
""",
        encoding="utf-8",
    )


def build_fixture(root: Path) -> dict[str, Path]:
    model_root = root / "Recovered Models"
    output_root = root / "Comparison Outputs"
    applio_root = root / "Applio Runtime"
    model_root.mkdir(parents=True)
    output_root.mkdir(parents=True)
    applio_root.mkdir(parents=True)

    for name in (
        "model_2_700e_63700s.pth",
        "model_2_1000e_91000s.pth",
        "model_2_1500e_136500s.pth",
    ):
        (model_root / name).write_bytes((name.encode("utf-8") + b"\n") * 32)
    (model_root / "model_2.index").write_bytes(b"shared-index-fixture\n" * 32)
    input_path = root / "approved vocals.wav"
    input_path.write_bytes(b"RIFF" + b"approved-vocal-fixture" * 8)
    core_path = applio_root / "core.py"
    write_stub_core(core_path)
    return {
        "modelRoot": model_root,
        "outputRoot": output_root,
        "applioRoot": applio_root,
        "input": input_path,
        "core": core_path,
    }


def invoke(script: Path, fixture: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-ModelRoot",
        str(fixture["modelRoot"]),
        "-OutputRoot",
        str(fixture["outputRoot"]),
        "-InputPath",
        str(fixture["input"]),
        "-ApplioRoot",
        str(fixture["applioRoot"]),
        "-PythonExecutable",
        sys.executable,
        "-CorePath",
        str(fixture["core"]),
        "-AllowNonDDrive",
        "-NoOpen",
    ]
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    script = args.script.resolve()
    require(script.is_file(), "Recovered RVC comparison launcher is missing")

    cases: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="echoes-recovered-rvc-launcher-") as temporary:
        root = Path(temporary)
        valid = build_fixture(root / "valid")
        completed = invoke(script, valid)
        require(completed.returncode == 0, f"Valid launcher fixture failed: {completed.stdout}\n{completed.stderr}")
        report_path = valid["outputRoot"] / "control" / "RECOVERED-RVC-COMPARISON-REPORT.json"
        require(report_path.is_file(), "Launcher did not create its report")
        report = load_json(report_path)
        require(report.get("status") == "PASS", "Launcher report did not pass")
        runs = report.get("runs") or []
        require([run.get("label") for run in runs] == ["700", "1000", "1500"], "Comparison order drifted")
        require(len({run.get("inputSha256") for run in runs}) == 1, "Input was not shared")
        require(len({run.get("indexSha256") for run in runs}) == 1, "Index was not shared")
        require(len({run.get("modelSha256") for run in runs}) == 3, "Models were not distinct")
        require(all(Path(str(run.get("outputPath"))).is_file() for run in runs), "A comparison output is missing")
        truth = report.get("truthBoundary") or {}
        require(truth.get("localApplioRuntimeExecuted") is True, "Stub Applio runtime was not executed")
        require(truth.get("threeConversionsExecuted") is True, "Three stub conversions were not executed")
        require(truth.get("bestModelSelected") is False, "Launcher selected a winner")
        require(truth.get("audioUploaded") is False, "Launcher claimed audio upload")
        cases["validOrchestration"] = {
            "status": report.get("status"),
            "labels": [run.get("label") for run in runs],
            "sharedInputSha256": runs[0].get("inputSha256"),
            "sharedIndexSha256": runs[0].get("indexSha256"),
        }

        blocked = build_fixture(root / "missing-model")
        (blocked["modelRoot"] / "model_2_1500e_136500s.pth").unlink()
        completed = invoke(script, blocked)
        require(completed.returncode != 0, "Missing recovered model did not block")
        require("Required recovered RVC file is missing" in completed.stdout + completed.stderr, "Missing-model blocker was not reported")
        cases["missingModel"] = "BLOCKED"

    proof = {
        "schema": "echoes.recovered-rvc-comparison-launcher-contract-proof.v1",
        "status": "PASS",
        "cases": cases,
        "truthBoundary": {
            "windowsLauncherOrchestrationProven": True,
            "sameInputAndIndexEnforced": True,
            "threeDistinctOutputsVerified": True,
            "realKamDridiModelsLoaded": False,
            "realKamDridiAudioRead": False,
            "realRvcInferenceProven": False,
            "realVoiceConversionProven": False,
            "bestModelSelected": False,
            "audioUploaded": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print("RecoveredRvcComparisonLauncherContract PASS cases=2 real_inference=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
