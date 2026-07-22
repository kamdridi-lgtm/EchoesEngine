#!/usr/bin/env python3
"""Choose an Echoes Cinema provider candidate without claiming a successful load.

The selector uses measured hardware/storage evidence and the requested usage mode.
It never reports a render backend as REAL. It only emits a candidate or a blocker.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROOF_MODEL_ID = "ali-vilab/text-to-video-ms-1.7b"
PROOF_PROVIDER = "providers/modelscope_low_vram_provider.py"
PROOF_MIN_VRAM_GIB = 5.5
PROOF_MIN_DISK_GIB = 35.0

COMMERCIAL_MODEL_ID = "zai-org/CogVideoX-2b"
COMMERCIAL_MODEL_REVISION = "102080da924c0ab684abeeca4b061ec7dfb7d40c"
COMMERCIAL_PROVIDER = "providers/cogvideox_commercial_provider.py"
COMMERCIAL_MIN_VRAM_GIB = 12.0
COMMERCIAL_MIN_RAM_GIB = 32.0
COMMERCIAL_MIN_DISK_GIB = 50.0


@dataclass(frozen=True)
class Evidence:
    cuda_available: bool
    gpu_name: str
    vram_gib: float
    system_ram_gib: float
    workspace_free_gib: float
    workspace_drive: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Evidence":
        gpu = payload.get("gpu") if isinstance(payload.get("gpu"), dict) else payload
        storage = payload.get("storage") if isinstance(payload.get("storage"), dict) else payload
        system = payload.get("system") if isinstance(payload.get("system"), dict) else payload
        return cls(
            cuda_available=bool(
                gpu.get("available", payload.get("cudaAvailable", payload.get("available", False)))
            ),
            gpu_name=str(gpu.get("name", payload.get("gpuName", "unknown"))),
            vram_gib=float(gpu.get("vramGiB", payload.get("vramGiB", 0.0)) or 0.0),
            system_ram_gib=float(
                system.get("ramGiB", payload.get("systemRamGiB", payload.get("ramGiB", 0.0))) or 0.0
            ),
            workspace_free_gib=float(
                storage.get(
                    "workspaceFreeGiB",
                    payload.get("workspaceFreeGiB", payload.get("freeDiskGiB", 0.0)),
                )
                or 0.0
            ),
            workspace_drive=str(
                storage.get("workspaceDrive", payload.get("workspaceDrive", "unknown"))
            ),
        )


def candidate_payload(evidence: Evidence, usage: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "echoes.provider-selection.v1",
        "status": "PARTIAL",
        "classification": "CANDIDATE_ONLY",
        "usage": usage,
        "realModelLoaded": False,
        "renderCompleted": False,
        "evidence": {
            "cudaAvailable": evidence.cuda_available,
            "gpuName": evidence.gpu_name,
            "vramGiB": evidence.vram_gib,
            "systemRamGiB": evidence.system_ram_gib,
            "workspaceFreeGiB": evidence.workspace_free_gib,
            "workspaceDrive": evidence.workspace_drive,
        },
    }

    if not evidence.cuda_available:
        base.update(
            {
                "decision": "BLOCKED",
                "blocker": "CUDA is unavailable; no local real-model provider can be selected",
            }
        )
        return base

    if usage == "proof":
        if evidence.workspace_free_gib < PROOF_MIN_DISK_GIB:
            base.update(
                {
                    "decision": "BLOCKED",
                    "blocker": (
                        f"proof provider requires at least {PROOF_MIN_DISK_GIB:.0f} GiB free "
                        f"on the selected workspace drive"
                    ),
                }
            )
            return base
        if evidence.vram_gib < PROOF_MIN_VRAM_GIB:
            base.update(
                {
                    "decision": "BLOCKED",
                    "blocker": (
                        f"local proof profile requires at least {PROOF_MIN_VRAM_GIB:.1f} GiB VRAM; "
                        f"detected {evidence.vram_gib:.2f} GiB"
                    ),
                }
            )
            return base
        base.update(
            {
                "decision": "LOCAL_PROOF_CANDIDATE",
                "provider": PROOF_PROVIDER,
                "modelId": PROOF_MODEL_ID,
                "commercialUseAllowed": False,
                "profile": {
                    "width": 384,
                    "height": 216,
                    "fps": 4,
                    "frames": 16,
                    "inferenceSteps": 15,
                    "offload": "sequential-cpu-offload" if evidence.vram_gib <= 6.5 else "model-cpu-offload",
                },
                "nextProofRequired": "model-load-render-qc-sha256",
            }
        )
        return base

    local_commercial_ready = (
        evidence.vram_gib >= COMMERCIAL_MIN_VRAM_GIB
        and evidence.system_ram_gib >= COMMERCIAL_MIN_RAM_GIB
        and evidence.workspace_free_gib >= COMMERCIAL_MIN_DISK_GIB
    )
    commercial_common = {
        "provider": COMMERCIAL_PROVIDER,
        "modelId": COMMERCIAL_MODEL_ID,
        "modelRevision": COMMERCIAL_MODEL_REVISION,
        "license": "Apache-2.0",
        "commercialUseAllowed": True,
        "nextProofRequired": "exact-revision-model-load-render-qc-sha256",
    }
    if local_commercial_ready:
        base.update(
            {
                "decision": "LOCAL_COMMERCIAL_CANDIDATE",
                **commercial_common,
                "profile": {
                    "width": 720,
                    "height": 480,
                    "fps": 8,
                    "frames": 49,
                    "inferenceSteps": 50,
                    "offload": "model-cpu-offload",
                },
            }
        )
    else:
        missing: list[str] = []
        if evidence.vram_gib < COMMERCIAL_MIN_VRAM_GIB:
            missing.append(
                f"VRAM {evidence.vram_gib:.2f}/{COMMERCIAL_MIN_VRAM_GIB:.0f} GiB"
            )
        if evidence.system_ram_gib < COMMERCIAL_MIN_RAM_GIB:
            missing.append(
                f"system RAM {evidence.system_ram_gib:.2f}/{COMMERCIAL_MIN_RAM_GIB:.0f} GiB"
            )
        if evidence.workspace_free_gib < COMMERCIAL_MIN_DISK_GIB:
            missing.append(
                f"workspace free {evidence.workspace_free_gib:.2f}/{COMMERCIAL_MIN_DISK_GIB:.0f} GiB"
            )
        base.update(
            {
                "decision": "REMOTE_COMMERCIAL_REQUIRED",
                **commercial_common,
                "localBlockers": missing,
                "remoteProviderRequired": True,
            }
        )
    return base


def self_test() -> int:
    rtx2060 = Evidence(True, "NVIDIA GeForce RTX 2060", 6.0, 16.0, 300.0, "D:\\")
    proof = candidate_payload(rtx2060, "proof")
    assert proof["decision"] == "LOCAL_PROOF_CANDIDATE"
    assert proof["commercialUseAllowed"] is False
    assert proof["realModelLoaded"] is False

    commercial_on_rtx2060 = candidate_payload(rtx2060, "commercial")
    assert commercial_on_rtx2060["decision"] == "REMOTE_COMMERCIAL_REQUIRED"
    assert commercial_on_rtx2060["commercialUseAllowed"] is True

    production = Evidence(True, "Production GPU", 16.0, 64.0, 200.0, "D:\\")
    commercial = candidate_payload(production, "commercial")
    assert commercial["decision"] == "LOCAL_COMMERCIAL_CANDIDATE"
    assert commercial["modelRevision"] == COMMERCIAL_MODEL_REVISION

    no_cuda = candidate_payload(Evidence(False, "none", 0.0, 64.0, 200.0, "D:\\"), "proof")
    assert no_cuda["decision"] == "BLOCKED"

    print("ProviderSelector PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--usage", choices=("proof", "commercial"), default="proof")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.evidence is None or not args.evidence.is_file():
        raise SystemExit("--evidence must point to a JSON hardware/storage report")

    payload = json.loads(args.evidence.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit("evidence JSON must be an object")
    decision = candidate_payload(Evidence.from_payload(payload), args.usage)
    rendered = json.dumps(decision, indent=2, ensure_ascii=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if decision["decision"] != "BLOCKED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
