#!/usr/bin/env python3
"""Authenticated local Diffusers video provider for Echoes Cinema.

The provider implements the canonical Echoes HTTP render contract:
- GET /health
- POST /v1/render

It reports ``realModelLoaded=true`` only after a Diffusers pipeline has been
successfully loaded into this process. Render requests are rejected while no
real model is loaded. Generated clips are normalized with FFmpeg to H.264,
yuv420p, and fast-start MP4 before they are returned.
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


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
class ProviderSettings:
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
    cpu_offload: bool
    use_safetensors: bool

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ProviderSettings":
        token = args.token or os.getenv("ECHOES_RENDER_TOKEN", "")
        model_id = args.model_id or os.getenv("ECHOES_DIFFUSERS_MODEL_ID", "")
        if not token:
            raise ValueError("ECHOES_RENDER_TOKEN or --token is required")
        if args.port <= 0 or args.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if args.width <= 0 or args.height <= 0 or args.fps <= 0:
            raise ValueError("width, height, and fps must be positive")
        if args.width % 8 != 0 or args.height % 8 != 0:
            raise ValueError("width and height must be divisible by 8")
        if args.inference_steps <= 0 or args.max_frames <= 0:
            raise ValueError("inference steps and max frames must be positive")
        return cls(
            token=token,
            model_id=model_id,
            host=args.host,
            port=args.port,
            device=args.device,
            width=args.width,
            height=args.height,
            fps=args.fps,
            inference_steps=args.inference_steps,
            guidance_scale=args.guidance_scale,
            max_frames=args.max_frames,
            cpu_offload=args.cpu_offload,
            use_safetensors=args.use_safetensors,
        )


class DiffusersEngine:
    def __init__(self, settings: ProviderSettings) -> None:
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
        if not self.settings.model_id:
            self.load_error = "ECHOES_DIFFUSERS_MODEL_ID is not configured"
            return
        try:
            import torch  # type: ignore
            from diffusers import DiffusionPipeline  # type: ignore
            from diffusers.utils import export_to_video  # type: ignore

            requested = self.settings.device.strip().lower()
            if requested == "auto":
                device = "cuda" if torch.cuda.is_available() else "cpu"
            elif requested in {"cuda", "cpu"}:
                device = requested
            else:
                raise ValueError("device must be auto, cuda, or cpu")
            if device == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")

            dtype = torch.float16 if device == "cuda" else torch.float32
            load_kwargs: dict[str, Any] = {"torch_dtype": dtype}
            if self.settings.use_safetensors:
                load_kwargs["use_safetensors"] = True

            pipeline = DiffusionPipeline.from_pretrained(self.settings.model_id, **load_kwargs)
            if self.settings.cpu_offload and device == "cuda" and hasattr(pipeline, "enable_model_cpu_offload"):
                pipeline.enable_model_cpu_offload()
            else:
                pipeline.to(device)
            if hasattr(pipeline, "enable_vae_slicing"):
                pipeline.enable_vae_slicing()
            if hasattr(pipeline, "enable_vae_tiling"):
                pipeline.enable_vae_tiling()
            if hasattr(pipeline, "set_progress_bar_config"):
                pipeline.set_progress_bar_config(disable=True)

            self.pipeline = pipeline
            self.torch = torch
            self.export_to_video = export_to_video
            self.device = device
            self.dtype = str(dtype).replace("torch.", "")
            self.load_error = ""
        except Exception as error:  # noqa: BLE001 - health must retain the exact blocker
            self.pipeline = None
            self.torch = None
            self.export_to_video = None
            self.load_error = str(error)

    def health(self) -> dict[str, Any]:
        return {
            "schema": "echoes.render-provider-health.v1",
            "status": "PASS",
            "backend": "diffusers-local",
            "realModelLoaded": self.real_model_loaded,
            "modelId": self.settings.model_id or None,
            "device": self.device,
            "dtype": self.dtype,
            "loadError": self.load_error or None,
            "capabilities": {
                "textToVideo": True,
                "referenceImage": False,
                "subjectIdentity": False,
                "seed": True,
                "h264": True,
                "pixelFormat": "yuv420p",
            },
        }

    def _extract_frames(self, result: Any) -> Any:
        frames = getattr(result, "frames", None)
        if frames is None and isinstance(result, tuple) and result:
            frames = result[0]
        if frames is None:
            raise RuntimeError("Diffusers pipeline returned no frames")
        if isinstance(frames, (list, tuple)) and len(frames) == 1:
            frames = frames[0]
        if not isinstance(frames, (list, tuple)) or not frames:
            raise RuntimeError("Diffusers pipeline returned an empty frame sequence")
        return frames

    def render(self, task: dict[str, Any]) -> bytes:
        if not self.real_model_loaded:
            raise RuntimeError("provider has no verified real model loaded")

        prompt = str(task.get("prompt", "")).strip()
        if not prompt:
            raise ValueError("render task prompt is required")
        if len(prompt) > 4000:
            raise ValueError("render task prompt exceeds 4000 characters")

        seed = clamp_int(task.get("seed"), 0, 0xFFFFFFFF, 0)
        duration = float(task.get("durationSeconds", 0.0))
        if duration <= 0.0:
            raise ValueError("render task duration must be positive")

        render_options = task.get("renderOptions") if isinstance(task.get("renderOptions"), dict) else {}
        width = clamp_int(render_options.get("width"), 128, 1920, self.settings.width)
        height = clamp_int(render_options.get("height"), 128, 1080, self.settings.height)
        width -= width % 8
        height -= height % 8
        fps = clamp_int(render_options.get("fps"), 1, 60, self.settings.fps)
        requested_frames = max(1, int(round(duration * fps)))
        num_frames = min(requested_frames, self.settings.max_frames)
        steps = clamp_int(render_options.get("inferenceSteps"), 1, 200, self.settings.inference_steps)
        guidance = float(render_options.get("guidanceScale", self.settings.guidance_scale))

        assert self.torch is not None
        assert self.pipeline is not None
        assert self.export_to_video is not None
        generator_device = "cuda" if self.device == "cuda" else "cpu"
        generator = self.torch.Generator(device=generator_device).manual_seed(seed)

        with self.lock, tempfile.TemporaryDirectory(prefix="echoes-diffusers-") as temp_dir:
            raw_path = Path(temp_dir) / "raw.mp4"
            final_path = Path(temp_dir) / "final.mp4"
            result = self.pipeline(
                prompt=prompt,
                num_inference_steps=steps,
                guidance_scale=guidance,
                height=height,
                width=width,
                num_frames=num_frames,
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
    engine: DiffusersEngine
    token: str


class Handler(BaseHTTPRequestHandler):
    server_version = "EchoesDiffusersProvider/1.0"

    @property
    def provider(self) -> ProviderServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"provider {self.address_string()} {format_string % args}", flush=True)

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.provider.token}"

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def require_authorized(self) -> bool:
        if self.authorized():
            return True
        self.send_json(401, {"status": "FAILED", "error": "unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/health":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        if not self.require_authorized():
            return
        self.send_json(200, self.provider.engine.health())

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/v1/render":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        if not self.require_authorized():
            return
        if not self.provider.engine.real_model_loaded:
            self.send_json(
                503,
                {
                    "status": "FAILED",
                    "error": "provider has no verified real model loaded",
                    "health": self.provider.engine.health(),
                },
            )
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
            self.send_header("X-Echoes-Backend", "diffusers-local")
            self.send_header("X-Echoes-Model-Id", self.provider.engine.settings.model_id)
            self.end_headers()
            self.wfile.write(clip)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json(400, {"status": "FAILED", "error": str(error)})
        except Exception as error:  # noqa: BLE001 - provider must return the exact render blocker
            self.send_json(500, {"status": "FAILED", "error": str(error)})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("ECHOES_DIFFUSERS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ECHOES_DIFFUSERS_PORT", "8081")))
    parser.add_argument("--token", default="")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--device", default=os.getenv("ECHOES_DIFFUSERS_DEVICE", "auto"))
    parser.add_argument("--width", type=int, default=int(os.getenv("ECHOES_DIFFUSERS_WIDTH", "576")))
    parser.add_argument("--height", type=int, default=int(os.getenv("ECHOES_DIFFUSERS_HEIGHT", "320")))
    parser.add_argument("--fps", type=int, default=int(os.getenv("ECHOES_DIFFUSERS_FPS", "8")))
    parser.add_argument("--inference-steps", type=int, default=int(os.getenv("ECHOES_DIFFUSERS_STEPS", "30")))
    parser.add_argument("--guidance-scale", type=float, default=float(os.getenv("ECHOES_DIFFUSERS_GUIDANCE", "7.5")))
    parser.add_argument("--max-frames", type=int, default=int(os.getenv("ECHOES_DIFFUSERS_MAX_FRAMES", "24")))
    parser.add_argument(
        "--cpu-offload",
        action=argparse.BooleanOptionalAction,
        default=env_bool("ECHOES_DIFFUSERS_CPU_OFFLOAD", True),
    )
    parser.add_argument(
        "--use-safetensors",
        action=argparse.BooleanOptionalAction,
        default=env_bool("ECHOES_DIFFUSERS_USE_SAFETENSORS", True),
    )
    return parser.parse_args()


def main() -> int:
    settings = ProviderSettings.from_args(parse_args())
    engine = DiffusersEngine(settings)
    print(f"Loading Diffusers model: {settings.model_id or '[not configured]'}", flush=True)
    engine.load()

    server = ProviderServer((settings.host, settings.port), Handler)
    server.engine = engine
    server.token = settings.token
    health = engine.health()
    print(
        f"EchoesDiffusersProvider READY http://{settings.host}:{settings.port} "
        f"realModelLoaded={health['realModelLoaded']} model={settings.model_id or '[none]'}",
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
