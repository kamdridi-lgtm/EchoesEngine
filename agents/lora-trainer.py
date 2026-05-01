"""
STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
Purpose: Future LoRA dataset preparation from QC-passed renders.
Safe to commit. No training. No filesystem writes on import/direct init.
Activation requires explicit dataset path and manual method calls.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable


@dataclass(frozen=True)
class LoraTrainingCandidate:
    job_id: str
    video_path: str
    prompt: str
    qc_score: float
    style: str = "dark_cinematic"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class LoraDatasetPlan:
    accepted: list[LoraTrainingCandidate]
    rejected: list[LoraTrainingCandidate]
    min_qc_score: float

    def to_dict(self) -> dict:
        return {
            "accepted": [item.to_dict() for item in self.accepted],
            "rejected": [item.to_dict() for item in self.rejected],
            "min_qc_score": self.min_qc_score,
        }


class LoraTrainerDormant:
    """Dormant planner for a future LoRA trainer. It does not read files or train."""

    def __init__(self, min_qc_score: float = 85.0) -> None:
        self.min_qc_score = min_qc_score

    def init(self) -> None:
        print("[LoraTrainer] Initialized (dormant mode)")

    def plan_dataset(self, candidates: Iterable[LoraTrainingCandidate]) -> LoraDatasetPlan:
        accepted: list[LoraTrainingCandidate] = []
        rejected: list[LoraTrainingCandidate] = []
        for candidate in candidates:
            if candidate.qc_score >= self.min_qc_score:
                accepted.append(candidate)
            else:
                rejected.append(candidate)
        return LoraDatasetPlan(accepted=accepted, rejected=rejected, min_qc_score=self.min_qc_score)


if __name__ == "__main__":
    LoraTrainerDormant().init()
