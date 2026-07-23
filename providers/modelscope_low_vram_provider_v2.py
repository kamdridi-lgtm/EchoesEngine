#!/usr/bin/env python3
"""Pinned, fail-closed low-VRAM provider for the first real Cinema proof.

The selected checkpoint is non-commercial. Model loading is pinned to an exact
revision and tries only safetensors strategies. Every attempt is exposed through
health so a failed local run reports the exact compatibility blocker instead of
silently falling back to unsafe pickle weights or pretending the model loaded.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_MODEL_ID = "ali-vilab/text-to-video-ms-1.7b"
DEFAULT_MODEL_REVISION = "0951da43c60d797968ddbdb157bdf1d9d38704bc"
NON_COMMERCIAL_LICENSE = "CC-BY-NC-4.0"


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))


def choose_offload_strategy(vram_gib: float) -> str:
    return "sequential-cpu-offload" if vram_gib <= 6.5 else "model-cpu-offload"


def is_cuda_oom(error: BaseException, torch_module: Any) -> bool:
    oom_type = getattr(getattr(torch_module, "cuda", None), "OutOfMemoryError", None)
    if oom_type is not None and isinstance(error, oom_type):
        return True
    text = str(error).lower()
    return "cuda" in text and "out of memory" in text


def run_checked(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[-4000:] or f"command failed with exit code {completed.returncode}")


def safe_load_attempts(dtype: Any, revision: str) -> list[tuple[str, dict[str, Any]]]:
    """Return ordered safe loading strategies; pickle fallback is forbidden."""
    common = {
        "revision": revision,
        "torch_dtype": dtype,
        "use_safetensors": True,
    }
    return [
        (
            "fp16-safetensors-low-cpu-memory",
            {**common, "variant": "fp16", "low_cpu_mem_usage": True},
        ),
        (
            "fp16-safetensors-standard-loader",
            {**common, "variant": "fp16", "low_cpu_mem_usage": False},
        ),
        (
            "base-safetensors-low-cpu-memory",
            {**common, "low_cpu_mem_usage": True},
        ),
    ]


def compact_error(error: BaseException) -> str:
    text = " ".join(str(error).strip().split())
    return text[-3000:] if text else type(error).__name__


@dataclass(frozen=True)
class Settings:
    token: str
    model_id: str
    model_revision: str
    host: str
    port: int
    device: str
    width: int
    height: int
    fps: int
    inference_steps: int
    guidance_scale: float
    max_frames: int

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Settings":
        token = args.token or os.getenv("ECHOES_RENDER_TOKEN", "")
        if not token:
            raise ValueError("ECHOES_RENDER_TOKEN or --token is required")
        if args.port <= 0 or args.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if args.width <= 0 or args.height <= 0 or args.fps <= 0:
            raise ValueError("width, height, and fps must be positive")
        if args.width % 8 or args.height % 8:
            raise ValueError("width and height must be divisible by 8")
        if args.inference_steps <= 0 or args.max_frames <= 0:
            raise ValueError("inference steps and max frames must be positive")
        revision = str(args.model_revision or DEFAULT_MODEL_REVISION).strip()
        if len(revision) != 40 or any(character not in "0123456789abcdefABCDEF" for character in revision):
            raise ValueError("model revision must be an exact 40-character Git commit SHA")
        return cls(
            token=token,
            model_id=args.model_id or DEFAULT_MODEL_ID,
            model_revision=revision.lower(),
            host=args.host,
            port=args.port,
            device=args.device,
            width=args.width,
            height=args.height,
            fps=args.fps,
            inference_steps=args.inference_steps,
            guidance_scale=args.guidance_scale,
            max_frames=args.max_frames,
        )


class LowVramModelScopeEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pipeline: Any | None = None
        self.torch: Any | None = None
        self.export_to_video: Any | None = None
        self.device = "unresolved"
        self.dtype = "unresolved"
        self.load_error = ""
        self.load_strategy = "unresolved"
        self.load_attempts: list[dict[str, Any]] = []
        self.offload_strategy = "unresolved"
        self.vram_gib = 0.0
        self.last_render_profile: dict[str, Any] | None = None
        self.lock = threading.Lock()

    @property
    def real_model_loaded(self) -> bool:
        return self.pipeline is not None and self.torch is not None and self.export_to_video is not None

    def _record_attempt(self, name: str, status: str, error: str | None = None) -> None:
        record: dict[str, Any] = {
            "strategy": name,
            "status": status,
            "safeTensorOnly": True,
            "modelRevision": self.settings.model_revision,
        }
        if error:
            record["error"] = error
        self.load_attempts.append(record)

    def load(self) -> None:
        self.load_attempts = []
        self.load_strategy = "unresolved"
        self.load_error = ""
        try:
            import torch  # type: ignore
            from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler  # type: ignore
            from diffusers.utils import export_to_video  # type: ignore

            self.torch = torch
            requested = self.settings.device.strip().lower()
            if requested not in {"cuda", "auto"}:
                raise RuntimeError("the first real proof requires CUDA")
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")

            properties = torch.cuda.get_device_properties(0)
            self.vram_gib = round(properties.total_memory / (1024**3), 2)
            self.offload_strategy = choose_offload_strategy(self.vram_gib)
            self.device = "cuda"
            self.dtype = "float16"
            torch.backends.cuda.matmul.allow_tf32 = True

            pipeline: Any | None = None
            failures: list[str] = []
            for strategy, kwargs in safe_load_attempts(torch.float16, self.settings.model_revision):
                try:
                    pipeline = DiffusionPipeline.from_pretrained(self.settings.model_id, **kwargs)
                    self._record_attempt(strategy, "PASS")
                    self.load_strategy = strategy
                    break
                except Exception as error:  # noqa: BLE001 - all load blockers belong in health
                    detail = compact_error(error)
                    failures.append(f"{strategy}: {detail}")
                    self._record_attempt(strategy, "FAILED", detail)
                    pipeline = None
                    gc.collect()
                    torch.cuda.empty_cache()

            if pipeline is None:
                raise RuntimeError("all safe model load strategies failed | " + " | ".join(failures))

            pipeline.scheduler = DPMSolverMultistepScheduler.from_config(pipeline.scheduler.config)
            if self.offload_strategy == "sequential-cpu-offload":
                if not hasattr(pipeline, "enable_sequential_cpu_offload"):
                    raise RuntimeError("loaded pipeline does not support sequential CPU offload")
                pipeline.enable_sequential_cpu_offload()
            else:
                if not hasattr(pipeline, "enable_model_cpu_offload"):
                    raise RuntimeError("loaded pipeline does not support model CPU offload")
                pipeline.enable_model_cpu_offload()

            if hasattr(pipeline, "enable_vae_slicing"):
                pipeline.enable_vae_slicing()
            if hasattr(pipeline, "enable_vae_tiling"):
                pipeline.enable_vae_tiling()
            if hasattr(pipeline, "enable_attention_slicing"):
                pipeline.enable_attention_slicing("max")
            unet = getattr(pipeline, "unet", None)
            if unet is not None and hasattr(unet, "enable_forward_chunking"):
                unet.enable_forward_chunking(chunk_size=1, dim=1)
            if hasattr(pipeline, "set_progress_bar_config"):
                pipeline.set_progress_bar_config(disable=False)

            self.pipeline = pipeline
            self.export_to_video = export_to_video
            self.load_error = ""
        except Exception as error:  # noqa: BLE001 - health must retain the exact blocker
            self.pipeline = None
            self.export_to_video = None
            self.load_error = compact_error(error)

    def health(self) -> dict[str, Any]:
        gpu: dict[str, Any] = {}
        if self.torch is not None and self.torch.cuda.is_available():
            properties = self.torch.cuda.get_device_properties(0)
            gpu = {
                "name": self.torch.cuda.get_device_name(0),
                "vramBytes": int(properties.total_memory),
                "vramGiB": round(properties.total_memory / (1024**3), 2),
            }
        return {
            "schema": "echoes.render-provider-health.v1",
            "status": "PASS",
            "backend": "diffusers-modelscope-low-vram-proof-v2",
            "realModelLoaded": self.real_model_loaded,
            "modelId": self.settings.model_id,
            "modelRevision": self.settings.model_revision,
            "modelSourcePinned": True,
            "loadStrategy": self.load_strategy,
            "loadAttempts": list(self.load_attempts),
            "safeTensorOnly": True,
            "device": self.device,
            "dtype": self.dtype,
            "loadError": self.load_error or None,
            "license": NON_COMMERCIAL_LICENSE,
            "commercialUseAllowed": False,
            "purpose": "first-real-ai-video-proof-only",
            "offloadStrategy": self.offload_strategy,
            "gpu": gpu,
            "defaultProfile": {
                "width": self.settings.width,
                "height": self.settings.height,
                "fps": self.settings.fps,
                "maxFrames": self.settings.max_frames,
                "inferenceSteps": self.settings.inference_steps,
            },
            "lastRenderProfile": self.last_render_profile,
            "capabilities": {
                "textToVideo": True,
                "referenceImage": False,
                "subjectIdentity": False,
                "seed": True,
                "h264": True,
                "pixelFormat": "yuv420p",
                "oomRetry": True,
            },
        }

    @staticmethod
    def _extract_frames(result: Any) -> Any:
        frames = getattr(result, "frames", None)
        if frames is None and isinstance(result, tuple) and result:
            frames = result[0]
        if isinstance(frames, (list, tuple)) and len(frames) == 1:
            frames = frames[0]
        if not isinstance(frames, (list, tuple)) or not frames:
            raise RuntimeError("Diffusers pipeline returned no video frames")
        return frames

    def _generate_frames(self, prompt: str, seed: int, profile: dict[str, int], guidance: float) -> Any:
        assert self.pipeline is not None
        assert self.torch is not None
        generator = self.torch.Generator(device="cpu").manual_seed(seed)
        result = self.pipeline(
            prompt=prompt,
            num_frames=profile["frames"],
            num_inference_steps=profile["steps"],
            guidance_scale=guidance,
            height=profile["height"],
            width=profile["width"],
            generator=generator,
        )
        return self._extract_frames(result)

    def render(self, task: dict[str, Any]) -> bytes:
        if not self.real_model_loaded:
            raise RuntimeError("provider has no verified real model loaded")
        prompt = str(task.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("render task prompt is required")
        if len(prompt) > 2000:
            raise ValueError("render task prompt exceeds 2000 characters")
        duration = float(task.get("durationSeconds", 0.0))
        if duration <= 0:
            raise ValueError("render task duration must be positive")

        seed = clamp_int(task.get("seed"), 0, 0xFFFFFFFF, 0)
        render_options = task.get("renderOptions") if isinstance(task.get("renderOptions"), dict) else {}
        width = clamp_int(render_options.get("width"), 256, 768, self.settings.width)
        height = clamp_int(render_options.get("height"), 184, 768, self.settings.height)
        width -= width % 8
        height -= height % 8
        fps = clamp_int(render_options.get("fps"), 4, 12, self.settings.fps)
        requested_frames = max(16, int(round(duration * fps)))
        frames = min(requested_frames, self.settings.max_frames)
        steps = clamp_int(render_options.get("inferenceSteps"), 10, 50, self.settings.inference_steps)
        guidance = float(render_options.get("guidanceScale", self.settings.guidance_scale))

        primary = {"width": width, "height": height, "fps": fps, "frames": frames, "steps": steps, "attempt": 1}
        retry = {
            "width": 320,
            "height": 184,
            "fps": 4,
            "frames": min(16, frames),
            "steps": min(12, steps),
            "attempt": 2,
        }
        assert self.torch is not None
        assert self.export_to_video is not None

        with self.lock, tempfile.TemporaryDirectory(prefix="echoes-real-proof-") as temp_dir:
            raw_path = Path(temp_dir) / "raw.mp4"
            final_path = Path(temp_dir) / "final.mp4"
            try:
                generated = self._generate_frames(prompt, seed, primary, guidance)
                used_profile = primary
            except BaseException as error:  # noqa: BLE001 - OOM compatibility varies by torch version
                if not is_cuda_oom(error, self.torch):
                    raise
                self.torch.cuda.empty_cache()
                gc.collect()
                generated = self._generate_frames(prompt, seed, retry, guidance)
                used_profile = retry

            self.last_render_profile = dict(used_profile)
            self.export_to_video(generated, str(raw_path), fps=used_profile["fps"])
            if not raw_path.is_file() or raw_path.stat().st_size <= 0:
                raise RuntimeError("Diffusers export did not produce a video")
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("ffmpeg is required to normalize provider output")
            run_checked(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(raw_path),
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(final_path),
                ]
            )
            if not final_path.is_file() or final_path.stat().st_size <= 0:
                raise RuntimeError("FFmpeg normalization did not produce a video")
            return final_path.read_bytes()


class ProviderHandler(BaseHTTPRequestHandler):
    engine: LowVramModelScopeEngine
    settings: Settings

    def _authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {self.settings.token}"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"status": "FAILED", "error": "unauthorized"})
            return
        if self.path != "/health":
            self._send_json(404, {"status": "FAILED", "error": "not found"})
            return
        self._send_json(200, self.engine.health())

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"status": "FAILED", "error": "unauthorized"})
            return
        if self.path != "/v1/render":
            self._send_json(404, {"status": "FAILED", "error": "not found"})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if payload.get("schema") != "echoes.render-request.v1":
                raise ValueError("unsupported render request schema")
            task = payload.get("task")
            if not isinstance(task, dict):
                raise ValueError("render request task must be an object")
            video = self.engine.render(task)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(video)))
            self.end_headers()
            self.wfile.write(video)
        except Exception as error:  # noqa: BLE001 - return the exact provider blocker
            self._send_json(500, {"status": "FAILED", "error": str(error)})

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        print(f"provider {self.address_string()} {format % args}", flush=True)


def self_test() -> int:
    assert choose_offload_strategy(6.0) == "sequential-cpu-offload"
    assert choose_offload_strategy(8.0) == "model-cpu-offload"
    assert clamp_int("12", 1, 20, 5) == 12
    assert clamp_int("bad", 1, 20, 5) == 5
    attempts = safe_load_attempts("float16", DEFAULT_MODEL_REVISION)
    assert [name for name, _ in attempts] == [
        "fp16-safetensors-low-cpu-memory",
        "fp16-safetensors-standard-loader",
        "base-safetensors-low-cpu-memory",
    ]
    assert all(options.get("use_safetensors") is True for _, options in attempts)
    assert all(options.get("revision") == DEFAULT_MODEL_REVISION for _, options in attempts)
    assert all("trust_remote_code" not in options for _, options in attempts)
    assert attempts[0][1].get("variant") == "fp16"
    assert "variant" not in attempts[-1][1]
    assert is_cuda_oom(RuntimeError("CUDA out of memory"), type("T", (), {"cuda": type("C", (), {})})())
    print("ModelScopeLowVramProviderV2 self-test PASS pinned-revision=safe-tensors-only diagnostics=enabled")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--token", default="")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--height", type=int, default=216)
    parser.add_argument("--fps", type=int, default=4)
    parser.add_argument("--inference-steps", type=int, default=15)
    parser.add_argument("--guidance-scale", type=float, default=9.0)
    parser.add_argument("--max-frames", type=int, default=16)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()

    settings = Settings.from_args(args)
    engine = LowVramModelScopeEngine(settings)
    engine.load()
    handler = type("BoundProviderHandler", (ProviderHandler,), {"engine": engine, "settings": settings})
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    print(
        json.dumps(
            {
                "event": "provider-started",
                "host": settings.host,
                "port": settings.port,
                "realModelLoaded": engine.real_model_loaded,
                "modelId": settings.model_id,
                "modelRevision": settings.model_revision,
                "loadStrategy": engine.load_strategy,
                "loadAttempts": engine.load_attempts,
                "loadError": engine.load_error or None,
                "offloadStrategy": engine.offload_strategy,
                "vramGiB": engine.vram_gib,
                "safeTensorOnly": True,
            }
        ),
        flush=True,
    )
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
