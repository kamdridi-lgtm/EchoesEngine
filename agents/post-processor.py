"""
STATUS: DORMANT / v2.4.0-PREP / NOT ACTIVE
Purpose: Future post-processing worker plan for Real-ESRGAN/RIFE/FFmpeg sync.
Safe to commit. No server auto-start. No filesystem writes on import/direct init.
Activation requires explicit worker implementation and manual launch.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Literal

Scale = Literal["2x", "4x"]


@dataclass(frozen=True)
class PostProcessPlan:
    job_id: str
    source_video: str
    source_audio: str | None = None
    target_fps: int = 60
    upscale: Scale = "2x"
    loudness_lufs: float = -14.0
    vram_safe: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


class PostProcessorDormant:
    """Dormant placeholder for the future :8082 post-processing worker."""

    def __init__(self, worker_name: str = "post-processor-8082") -> None:
        self.worker_name = worker_name

    def init(self) -> None:
        print("[PostProcessor] Initialized (dormant mode)")

    def plan(self, job_id: str, source_video: str, source_audio: str | None = None, upscale: Scale = "2x") -> PostProcessPlan:
        return PostProcessPlan(
            job_id=job_id,
            source_video=source_video,
            source_audio=source_audio,
            upscale=upscale,
        )


if __name__ == "__main__":
    PostProcessorDormant().init()
