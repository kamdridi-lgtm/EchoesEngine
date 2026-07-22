#!/usr/bin/env python3
"""Commercially allowlisted CogVideoX provider for Echoes Cinema.

This provider is deliberately separate from the 6 GiB RTX 2060 proof backend.
It accepts only the exact CogVideoX-2B model/revision pair reviewed for Apache-2.0
licensing. It is a production-provider contract, not a claim that the user's
current GPU can load or render the model locally.
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
COMMERCIAL_MODEL_ID = "zai-org/CogVideoX-2b"
COMMERCIAL_MODEL_REVISION = "102080da924c0ab684abeeca4b061ec7dfb7d40c"
COMMERCIAL_LICENSE = "Apache-2.0"
DEFAULT_WIDTH = 720
DEFAULT_HEIGHT = 480
DEFAULT_FPS = 8
DEFAULT_FRAMES = 49
DEFAULT_STEPS = 50
MINIMUM_RECOMMENDED_VRAM_GIB = 12.0


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def run_checked(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(detail[-4000:] or f"command failed with exit code {completed.returncode}")


def is_allowlisted(model_id: str, revision: str) -> bool:
    return model_id == COMMERCIAL_MODEL_ID and revision == COMMERCIAL_MODEL_REVISION


@dataclass(frozen=True)
class Settings:
    token: str
    model_id: str
    revision: str
    host: str
    port: int
    device: str
    width: int
    height: int
    fps: int
    inference_steps: int
    guidance_scale: float

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Settings":
        token = args.token or os.getenv("ECHOES_RENDER_TOKEN", "")
        if not token:
            raise ValueError("ECHOES_RENDER_TOKEN or --token is required")
        model_id = args.model_id or COMMERCIAL_MODEL_ID
        revision = args.revision or COMMERCIAL_MODEL_REVISION
        if not is_allowlisted(model_id, revision):
            raise ValueError("commercial provider accepts only the reviewed CogVideoX-2B model/revision pair")
        if args.port <= 0 or args.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if args.width != DEFAULT_WIDTH or args.height != DEFAULT_HEIGHT:
            raise ValueError(f"commercial profile is fixed at {DEFAULT_WIDTH}x{DEFAULT_HEIGHT}")
        if args.fps != DEFAULT_FPS:
            raise ValueError(f"commercial profile is fixed at {DEFAULT_FPS} fps")
        if args.inference_steps <= 0:
            raise ValueError("inference steps must be positive")
        return cls(
            token=token,
            model_id=model_id,
            revision=revision,
            host=args.host,
            port=args.port,
            device=args.device,
            width=args.width,
            height=args.height,
            fps=args.fps,
            inference_steps=args.inference_steps,
            guidance_scale=args.guidance_scale,
        )


class CogVideoXCommercialEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.pipeline: Any | None = None
        self.torch: Any | None = None
        self.export_to_video: Any | None = None
        self.device = "unresolved"
        self.dtype = "unresolved"
        self.load_error = ""
        self.vram_gib = 0.0
        self.lock = threading.Lock()

    @property
    def real_model_loaded(self) -> bool:
        return self.pipeline is not None and self.torch is not None and self.export_to_video is not None

    def load(self) -> None:
        try:
            import torch  # type: ignore
            from diffusers import CogVideoXPipeline  # type: ignore
            from diffusers.utils import export_to_video  # type: ignore

            if self.settings.device not in {"cuda", "auto"}:
                raise RuntimeError("commercial CogVideoX provider requires CUDA")
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA was requested but torch.cuda.is_available() is false")

            properties = torch.cuda.get_device_properties(0)
            self.vram_gib = round(properties.total_memory / (1024**3), 2)
            if self.vram_gib < MINIMUM_RECOMMENDED_VRAM_GIB:
                raise RuntimeError(
                    f"commercial CogVideoX profile requires at least {MINIMUM_RECOMMENDED_VRAM_GIB:.0f} GiB "
                    f"recommended VRAM; detected {self.vram_gib:.2f} GiB"
                )

            torch.backends.cuda.matmul.allow_tf32 = True
            pipeline = CogVideoXPipeline.from_pretrained(
                self.settings.model_id,
                revision=self.settings.revision,
                torch_dtype=torch.float16,
                use_safetensors=True,
                low_cpu_mem_usage=True,
            )
            if not hasattr(pipeline, "enable_model_cpu_offload"):
                raise RuntimeError("loaded CogVideoX pipeline does not support model CPU offload")
            pipeline.enable_model_cpu_offload()

            vae = getattr(pipeline, "vae", None)
            if vae is not None and hasattr(vae, "enable_tiling"):
                vae.enable_tiling()
            if vae is not None and hasattr(vae, "enable_slicing"):
                vae.enable_slicing()
            transformer = getattr(pipeline, "transformer", None)
            if transformer is not None and hasattr(transformer, "enable_forward_chunking"):
                transformer.enable_forward_chunking(chunk_size=1, dim=1)
            if hasattr(pipeline, "set_progress_bar_config"):
                pipeline.set_progress_bar_config(disable=False)

            self.pipeline = pipeline
            self.torch = torch
            self.export_to_video = export_to_video
            self.device = "cuda"
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
            "backend": "diffusers-cogvideox-commercial",
            "realModelLoaded": self.real_model_loaded,
            "modelId": self.settings.model_id,
            "modelRevision": self.settings.revision,
            "device": self.device,
            "dtype": self.dtype,
            "loadError": self.load_error or None,
            "commercialUseAllowed": is_allowlisted(self.settings.model_id, self.settings.revision),
            "license": COMMERCIAL_LICENSE,
            "licenseEvidence": {
                "modelId": COMMERCIAL_MODEL_ID,
                "revision": COMMERCIAL_MODEL_REVISION,
                "classification": "allowlisted-exact-revision",
            },
            "purpose": "commercial-text-to-video-production",
            "minimumRecommendedVramGiB": MINIMUM_RECOMMENDED_VRAM_GIB,
            "currentLocalGpuSupported": bool(gpu and gpu.get("vramGiB", 0.0) >= MINIMUM_RECOMMENDED_VRAM_GIB),
            "gpu": gpu,
            "defaultProfile": {
                "width": self.settings.width,
                "height": self.settings.height,
                "fps": self.settings.fps,
                "frames": DEFAULT_FRAMES,
                "inferenceSteps": self.settings.inference_steps,
            },
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
    def extract_frames(result: Any) -> Any:
        frames = getattr(result, "frames", None)
        if frames is None and isinstance(result, tuple) and result:
            frames = result[0]
        if isinstance(frames, (list, tuple)) and len(frames) == 1:
            frames = frames[0]
        if not isinstance(frames, (list, tuple)) or not frames:
            raise RuntimeError("CogVideoX pipeline returned no video frames")
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
        if duration <= 0.0:
            raise ValueError("render task duration must be positive")
        seed = int(task.get("seed", 0))
        if seed < 0 or seed > 0xFFFFFFFF:
            raise ValueError("seed must fit in an unsigned 32-bit integer")

        assert self.pipeline is not None
        assert self.torch is not None
        assert self.export_to_video is not None
        generator = self.torch.Generator(device="cpu").manual_seed(seed)

        with self.lock, tempfile.TemporaryDirectory(prefix="echoes-cogvideox-commercial-") as temp_dir:
            raw_path = Path(temp_dir) / "raw.mp4"
            final_path = Path(temp_dir) / "final.mp4"
            result = self.pipeline(
                prompt=prompt,
                num_videos_per_prompt=1,
                num_inference_steps=self.settings.inference_steps,
                num_frames=DEFAULT_FRAMES,
                guidance_scale=self.settings.guidance_scale,
                height=self.settings.height,
                width=self.settings.width,
                generator=generator,
            )
            frames = self.extract_frames(result)
            self.export_to_video(frames, str(raw_path), fps=self.settings.fps)
            if not raw_path.is_file() or raw_path.stat().st_size <= 0:
                raise RuntimeError("CogVideoX export did not produce a video")

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
    engine: CogVideoXCommercialEngine
    settings: Settings

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"cogvideox-commercial {self.address_string()} {format_string % args}", flush=True)

    def authorized(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {self.settings.token}"

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self.authorized():
            self.send_json(401, {"status": "FAILED", "error": "unauthorized"})
            return
        if self.path != "/health":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        self.send_json(200, self.engine.health())

    def do_POST(self) -> None:  # noqa: N802
        if not self.authorized():
            self.send_json(401, {"status": "FAILED", "error": "unauthorized"})
            return
        if self.path != "/v1/render":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request body size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if payload.get("schema") != "echoes.render-request.v1":
                raise ValueError("unsupported request schema")
            task = payload.get("task")
            if not isinstance(task, dict):
                raise ValueError("request task must be an object")
            clip = self.engine.render(task)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(clip)))
            self.end_headers()
            self.wfile.write(clip)
        except Exception as error:  # noqa: BLE001
            self.send_json(500, {"status": "FAILED", "error": str(error)})


def self_test() -> int:
    assert is_allowlisted(COMMERCIAL_MODEL_ID, COMMERCIAL_MODEL_REVISION)
    assert not is_allowlisted("THUDM/CogVideoX-5b", COMMERCIAL_MODEL_REVISION)
    assert not is_allowlisted(COMMERCIAL_MODEL_ID, "main")
    args = argparse.Namespace(
        token="contract-token",
        model_id=COMMERCIAL_MODEL_ID,
        revision=COMMERCIAL_MODEL_REVISION,
        host="127.0.0.1",
        port=8082,
        device="cuda",
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        fps=DEFAULT_FPS,
        inference_steps=DEFAULT_STEPS,
        guidance_scale=6.0,
    )
    settings = Settings.from_args(args)
    engine = CogVideoXCommercialEngine(settings)
    health = engine.health()
    assert health["realModelLoaded"] is False
    assert health["commercialUseAllowed"] is True
    assert health["license"] == COMMERCIAL_LICENSE
    assert health["modelRevision"] == COMMERCIAL_MODEL_REVISION
    assert health["capabilities"]["textToVideo"] is True
    assert health["currentLocalGpuSupported"] is False
    print("CogVideoXCommercialProvider self-test PASS")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--token", default="")
    parser.add_argument("--model-id", default=COMMERCIAL_MODEL_ID)
    parser.add_argument("--revision", default=COMMERCIAL_MODEL_REVISION)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
    parser.add_argument("--inference-steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--guidance-scale", type=float, default=6.0)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return self_test()
    settings = Settings.from_args(args)
    engine = CogVideoXCommercialEngine(settings)
    engine.load()

    handler = type("BoundCogVideoXCommercialHandler", (ProviderHandler,), {})
    handler.engine = engine
    handler.settings = settings
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    print(
        f"CogVideoXCommercialProvider READY http://{settings.host}:{settings.port} "
        f"realModelLoaded={engine.real_model_loaded} license={COMMERCIAL_LICENSE}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
