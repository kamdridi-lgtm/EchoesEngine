#!/usr/bin/env python3
"""Low-VRAM real-model provider used only to obtain the first Echoes Cinema AI proof.

This service implements the canonical Echoes render-provider contract:
- GET /health
- POST /v1/render

It targets ``ali-vilab/text-to-video-ms-1.7b`` because the official Diffusers
pipeline supports model CPU offload and VAE slicing on consumer NVIDIA GPUs.
The checkpoint is non-commercial; health therefore reports
``commercialUseAllowed=false``. It is a proof backend, not the production
backend for paid customer renders.
"""

from __future__ import annotations

import argparse
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


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def clamp_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(minimum, min(maximum, parsed))


def run_checked(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[-4000:] or f"command failed with exit code {completed.returncode}")


@dataclass(frozen=True)
class Settings:
    token: str
    model_id: str
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
        return cls(
            token=token,
            model_id=args.model_id or DEFAULT_MODEL_ID,
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


class ModelScopeEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pipeline: Any | None = None
        self.torch: Any | None = None
        self.export_to_video: Any | None = None
        self.device = "unresolved"
        self.dtype = "unresolved"
        self.load_error = ""
        self.lock = threading.Lock()

    @property
    def real_model_loaded(self) -> bool:
        return self.pipeline is not None and self.torch is not None and self.export_to_video is not None

    def load(self) -> None:
        try:
            import torch  # type: ignore
            from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler  # type: ignore
            from diffusers.utils import export_to_video  # type: ignore

            requested = self.settings.device.strip().lower()
            if requested == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            elif requested in {"cuda", "cpu"}:
                device = requested
            else:
                raise ValueError("device must be auto, cuda, or cpu")
            if device != "cuda":
                raise RuntimeError("the first real proof requires a CUDA-capable NVIDIA GPU")
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")

            dtype = torch.float16
            pipeline = DiffusionPipeline.from_pretrained(
                self.settings.model_id,
                torch_dtype=dtype,
                variant="fp16",
                use_safetensors=True,
            )
            pipeline.scheduler = DPMSolverMultistepScheduler.from_config(pipeline.scheduler.config)
            if not hasattr(pipeline, "enable_model_cpu_offload"):
                raise RuntimeError("loaded pipeline does not support model CPU offload")
            pipeline.enable_model_cpu_offload()
            if hasattr(pipeline, "enable_vae_slicing"):
                pipeline.enable_vae_slicing()
            if hasattr(pipeline, "enable_vae_tiling"):
                pipeline.enable_vae_tiling()
            unet = getattr(pipeline, "unet", None)
            if unet is not None and hasattr(unet, "enable_forward_chunking"):
                unet.enable_forward_chunking(chunk_size=1, dim=1)
            if hasattr(pipeline, "set_progress_bar_config"):
                pipeline.set_progress_bar_config(disable=False)

            self.pipeline = pipeline
            self.torch = torch
            self.export_to_video = export_to_video
            self.device = device
            self.dtype = "float16"
            self.load_error = ""
        except Exception as error:  # noqa: BLE001 - health retains the exact blocker
            self.pipeline = None
            self.torch = None
            self.export_to_video = None
            self.load_error = str(error)

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
            "backend": "diffusers-modelscope-proof",
            "realModelLoaded": self.real_model_loaded,
            "modelId": self.settings.model_id,
            "device": self.device,
            "dtype": self.dtype,
            "loadError": self.load_error or None,
            "commercialUseAllowed": False,
            "purpose": "first-real-ai-video-proof-only",
            "gpu": gpu,
            "capabilities": {
                "textToVideo": True,
                "referenceImage": False,
                "subjectIdentity": False,
                "seed": True,
                "h264": True,
                "pixelFormat": "yuv420p",
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
        height = clamp_int(render_options.get("height"), 256, 768, self.settings.height)
        width -= width % 8
        height -= height % 8
        fps = clamp_int(render_options.get("fps"), 4, 12, self.settings.fps)
        requested_frames = max(16, int(round(duration * fps)))
        num_frames = min(requested_frames, self.settings.max_frames)
        steps = clamp_int(render_options.get("inferenceSteps"), 10, 50, self.settings.inference_steps)
        guidance = float(render_options.get("guidanceScale", self.settings.guidance_scale))

        assert self.pipeline is not None
        assert self.torch is not None
        assert self.export_to_video is not None
        generator = self.torch.Generator(device="cpu").manual_seed(seed)

        with self.lock, tempfile.TemporaryDirectory(prefix="echoes-real-proof-") as temp_dir:
            raw_path = Path(temp_dir) / "raw.mp4"
            final_path = Path(temp_dir) / "final.mp4"
            result = self.pipeline(
                prompt=prompt,
                num_frames=num_frames,
                num_inference_steps=steps,
                guidance_scale=guidance,
                height=height,
                width=width,
                generator=generator,
            )
            frames = self._extract_frames(result)
            self.export_to_video(frames, str(raw_path), fps=fps)
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
                    "-t",
                    f"{duration:.3f}",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "medium",
                    "-crf",
                    "18",
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


class ProviderServer(ThreadingHTTPServer):
    engine: ModelScopeEngine
    token: str


class Handler(BaseHTTPRequestHandler):
    server_version = "EchoesModelScopeProofProvider/1.0"

    @property
    def provider(self) -> ProviderServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"provider {self.address_string()} {format_string % args}", flush=True)

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.provider.token}"

    def require_authorized(self) -> bool:
        if self.authorized():
            return True
        self.send_json(401, {"status": "FAILED", "error": "unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        if self.require_authorized():
            self.send_json(200, self.provider.engine.health())

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/render":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        if not self.require_authorized():
            return
        if not self.provider.engine.real_model_loaded:
            self.send_json(503, {"status": "FAILED", "error": "provider has no verified real model loaded", "health": self.provider.engine.health()})
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request body size")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if payload.get("schema") != "echoes.render-request.v1":
                raise ValueError("unsupported render request schema")
            task = payload.get("task")
            if not isinstance(task, dict):
                raise ValueError("render request task must be an object")
            clip = self.provider.engine.render(task)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(clip)))
            self.send_header("X-Echoes-Backend", "diffusers-modelscope-proof")
            self.send_header("X-Echoes-Model-Id", self.provider.engine.settings.model_id)
            self.end_headers()
            self.wfile.write(clip)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json(400, {"status": "FAILED", "error": str(error)})
        except Exception as error:  # noqa: BLE001
            self.send_json(500, {"status": "FAILED", "error": str(error)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--token", default="")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--width", type=int, default=576)
    parser.add_argument("--height", type=int, default=320)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--inference-steps", type=int, default=25)
    parser.add_argument("--guidance-scale", type=float, default=9.0)
    parser.add_argument("--max-frames", type=int, default=32)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Settings.from_args(args)
    engine = ModelScopeEngine(settings)
    if args.self_test:
        health = engine.health()
        assert health["realModelLoaded"] is False
        assert health["commercialUseAllowed"] is False
        assert health["backend"] == "diffusers-modelscope-proof"
        print("ModelScope proof provider fail-closed self-test PASS")
        return 0

    print(f"Loading real proof model: {settings.model_id}", flush=True)
    engine.load()
    server = ProviderServer((settings.host, settings.port), Handler)
    server.engine = engine
    server.token = settings.token
    health = engine.health()
    print(
        f"EchoesModelScopeProofProvider READY http://{settings.host}:{settings.port} "
        f"realModelLoaded={health['realModelLoaded']} model={settings.model_id}",
        flush=True,
    )
    if engine.load_error:
        print(f"Model load blocker: {engine.load_error}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
