#!/usr/bin/env python3
"""Authenticated HTTP service for submitting verified Echoes Cinema jobs.

The service accepts only safe relative inputs, requires a real text-to-video
provider, exposes separate real/commercial readiness, and uses the compiler-free
manifest path unless an optional native manifest CLI is explicitly configured.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any

from render_capabilities import (
    accepting_commercial_jobs,
    accepting_real_jobs,
    validate_provider_health,
)


MAX_REQUEST_BYTES = 256 * 1024
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def safe_relative(raw: Any, *, suffixes: set[str], field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} is required")
    candidate = PurePosixPath(raw.replace("\\", "/"))
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{field} must be a safe relative path")
    if candidate.suffix.lower() not in suffixes:
        raise ValueError(f"{field} has an unsupported extension")
    return Path(*candidate.parts)


def resolve_under(root: Path, relative: Path, *, field: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{field} escapes its configured root") from error
    if not candidate.is_file():
        raise FileNotFoundError(f"{field} not found: {relative.as_posix()}")
    return candidate


def validate_job_id(raw: Any) -> str:
    if not isinstance(raw, str) or not JOB_ID_PATTERN.fullmatch(raw):
        raise ValueError("jobId must contain only letters, digits, dot, underscore, or hyphen")
    return raw


def request_bool(request: dict[str, Any], field: str, default: bool = False) -> bool:
    value = request.get(field, default)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def provider_health_url(endpoint: str) -> str:
    explicit = os.getenv("ECHOES_RENDER_HEALTH_URL", "")
    if explicit:
        return explicit
    parsed = urllib.parse.urlparse(endpoint)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))


def fetch_provider_health(endpoint: str, token: str, timeout: float) -> dict[str, Any]:
    health_url = provider_health_url(endpoint)
    request = urllib.request.Request(
        health_url,
        method="GET",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "EchoesCinemaJobService/1.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            body = response.read(1024 * 1024)
            status_code = response.status
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace")
        raise RuntimeError(f"provider health HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"provider health connection failed: {error.reason}") from error

    if status_code != 200 or content_type != "application/json":
        raise RuntimeError(f"provider health returned HTTP {status_code} content-type {content_type}")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("provider health returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise RuntimeError("provider health must be a JSON object")
    return payload


@dataclass(frozen=True)
class ServiceConfig:
    token: str
    host: str
    port: int
    manifest_cli: Path | None
    runner: Path
    sections_root: Path
    audio_root: Path
    output_root: Path
    provider_endpoint: str
    provider_token: str
    provider_timeout: float
    max_workers: int

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ServiceConfig":
        token = args.token or os.getenv("ECHOES_CINEMA_SERVICE_TOKEN", "")
        provider_token = os.getenv("ECHOES_RENDER_TOKEN", "")
        provider_endpoint = os.getenv("ECHOES_RENDER_ENDPOINT", "http://127.0.0.1:8081/v1/render")
        if not token:
            raise ValueError("ECHOES_CINEMA_SERVICE_TOKEN or --token is required")
        if not provider_token:
            raise ValueError("ECHOES_RENDER_TOKEN is required")
        if args.port <= 0 or args.port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if args.provider_timeout <= 0 or args.max_workers <= 0:
            raise ValueError("provider timeout and max workers must be positive")

        manifest_cli = Path(args.manifest_cli).resolve() if args.manifest_cli else None
        runner = Path(args.runner).resolve()
        sections_root = Path(args.sections_root).resolve()
        audio_root = Path(args.audio_root or args.sections_root).resolve()
        output_root = Path(args.output_root).resolve()

        if manifest_cli is not None and not manifest_cli.is_file():
            raise FileNotFoundError(f"RenderManifestCli not found: {manifest_cli}")
        if not runner.is_file():
            raise FileNotFoundError(f"cinema job runner not found: {runner}")
        if not sections_root.is_dir():
            raise NotADirectoryError(f"sections root not found: {sections_root}")
        if not audio_root.is_dir():
            raise NotADirectoryError(f"audio root not found: {audio_root}")
        output_root.mkdir(parents=True, exist_ok=True)

        return cls(
            token=token,
            host=args.host,
            port=args.port,
            manifest_cli=manifest_cli,
            runner=runner,
            sections_root=sections_root,
            audio_root=audio_root,
            output_root=output_root,
            provider_endpoint=provider_endpoint,
            provider_token=provider_token,
            provider_timeout=args.provider_timeout,
            max_workers=args.max_workers,
        )


class JobRegistry:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.executor = ThreadPoolExecutor(max_workers=config.max_workers, thread_name_prefix="echoes-cinema")
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def _job_dir(self, job_id: str) -> Path:
        return self.config.output_root / job_id

    def read_result(self, job_id: str) -> dict[str, Any] | None:
        result_path = self._job_dir(job_id) / "job-result.json"
        if not result_path.is_file():
            return None
        try:
            return json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                "schema": "echoes.cinema-service-job.v1",
                "jobId": job_id,
                "status": "BROKEN",
                "error": "job-result.json could not be read",
            }

    def status(self, job_id: str) -> dict[str, Any] | None:
        persisted = self.read_result(job_id)
        if persisted is not None:
            return persisted
        with self.lock:
            current = self.jobs.get(job_id)
            return dict(current) if current else None

    def submit(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        job_id = validate_job_id(request.get("jobId"))
        existing = self.status(job_id)
        if existing is not None:
            status_code = 200 if existing.get("status") in {"PASS", "FAILED"} else 202
            return status_code, existing

        sections_relative = safe_relative(request.get("sectionsCsv"), suffixes={".csv"}, field="sectionsCsv")
        sections_csv = resolve_under(self.config.sections_root, sections_relative, field="sectionsCsv")

        audio_file: Path | None = None
        if request.get("audioFile") not in {None, ""}:
            audio_relative = safe_relative(
                request.get("audioFile"),
                suffixes={".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"},
                field="audioFile",
            )
            audio_file = resolve_under(self.config.audio_root, audio_relative, field="audioFile")

        seed = int(request.get("seed", 1337))
        if seed < 0 or seed > 0xFFFFFFFF:
            raise ValueError("seed must fit in an unsigned 32-bit integer")
        commercial_use_required = request_bool(request, "commercialUseRequired")

        health = fetch_provider_health(
            self.config.provider_endpoint,
            self.config.provider_token,
            self.config.provider_timeout,
        )
        required_capabilities = validate_provider_health(
            health,
            explicit_requirements={"textToVideo"},
            require_real_model=True,
            require_commercial_use=commercial_use_required,
        )

        accepted = {
            "schema": "echoes.cinema-service-job.v1",
            "jobId": job_id,
            "status": "QUEUED",
            "backend": "http-provider",
            "realModelLoaded": True,
            "modelId": health.get("modelId"),
            "commercialUseRequired": commercial_use_required,
            "commercialUseAllowed": health.get("commercialUseAllowed") is True,
            "requiredCapabilities": sorted(required_capabilities),
            "manifestGenerator": "native-render-manifest-cli" if self.config.manifest_cli else "python-render-manifest-v1",
        }
        with self.lock:
            if job_id in self.jobs:
                return 202, dict(self.jobs[job_id])
            self.jobs[job_id] = accepted

        self.executor.submit(
            self._run_job,
            job_id,
            sections_csv,
            audio_file,
            seed,
            commercial_use_required,
        )
        return 202, accepted

    def _run_job(
        self,
        job_id: str,
        sections_csv: Path,
        audio_file: Path | None,
        seed: int,
        commercial_use_required: bool,
    ) -> None:
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.jobs[job_id] = {
                "schema": "echoes.cinema-service-job.v1",
                "jobId": job_id,
                "status": "RUNNING",
                "commercialUseRequired": commercial_use_required,
            }

        command = [
            sys.executable,
            str(self.config.runner),
            str(sections_csv),
            str(job_dir),
            "--job-id",
            job_id,
            "--seed",
            str(seed),
            "--backend",
            "http",
            "--provider-timeout",
            str(self.config.provider_timeout),
        ]
        if self.config.manifest_cli is not None:
            command.extend(["--manifest-cli", str(self.config.manifest_cli)])
        if audio_file is not None:
            command.extend(["--audio", str(audio_file)])
        if commercial_use_required:
            command.append("--require-commercial-use")

        completed = subprocess.run(command, text=True, capture_output=True, check=False, shell=False)
        (job_dir / "service-run.log").write_text(
            "$ " + " ".join(command) + "\n\nSTDOUT\n" + completed.stdout + "\nSTDERR\n" + completed.stderr,
            encoding="utf-8",
        )

        result = self.read_result(job_id)
        if result is None:
            result = {
                "schema": "echoes.cinema-service-job.v1",
                "jobId": job_id,
                "status": "FAILED",
                "error": f"cinema job runner exited with {completed.returncode} without job-result.json",
            }
        with self.lock:
            self.jobs[job_id] = result


class CinemaServer(ThreadingHTTPServer):
    config: ServiceConfig
    registry: JobRegistry


class Handler(BaseHTTPRequestHandler):
    server_version = "EchoesCinemaJobService/1.1"

    @property
    def cinema(self) -> CinemaServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"cinema-service {self.address_string()} {format_string % args}", flush=True)

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {self.cinema.config.token}"

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

    def do_GET(self) -> None:  # noqa: N802
        if not self.require_authorized():
            return
        if self.path == "/health":
            try:
                provider = fetch_provider_health(
                    self.cinema.config.provider_endpoint,
                    self.cinema.config.provider_token,
                    self.cinema.config.provider_timeout,
                )
                real_ready = accepting_real_jobs(provider)
                commercial_ready = accepting_commercial_jobs(provider)
                self.send_json(
                    200,
                    {
                        "schema": "echoes.cinema-service-health.v1",
                        "status": "PASS",
                        "backend": "cinema-job-service",
                        "realModelLoaded": provider.get("realModelLoaded") is True,
                        "acceptingRealJobs": real_ready,
                        "acceptingCommercialJobs": commercial_ready,
                        "provider": provider,
                        "maxWorkers": self.cinema.config.max_workers,
                        "manifestGenerator": "native-render-manifest-cli"
                        if self.cinema.config.manifest_cli
                        else "python-render-manifest-v1",
                    },
                )
            except Exception as error:  # noqa: BLE001
                self.send_json(
                    200,
                    {
                        "schema": "echoes.cinema-service-health.v1",
                        "status": "PARTIAL",
                        "backend": "cinema-job-service",
                        "realModelLoaded": False,
                        "acceptingRealJobs": False,
                        "acceptingCommercialJobs": False,
                        "error": str(error),
                    },
                )
            return

        prefix = "/v1/cinema/jobs/"
        if self.path.startswith(prefix):
            try:
                job_id = validate_job_id(urllib.parse.unquote(self.path[len(prefix) :]))
                status = self.cinema.registry.status(job_id)
                if status is None:
                    self.send_json(404, {"status": "MISSING", "jobId": job_id})
                else:
                    self.send_json(200, status)
            except ValueError as error:
                self.send_json(400, {"status": "FAILED", "error": str(error)})
            return

        self.send_json(404, {"status": "FAILED", "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/cinema/jobs":
            self.send_json(404, {"status": "FAILED", "error": "not found"})
            return
        if not self.require_authorized():
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request body size")
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if payload.get("schema") != "echoes.cinema-job-request.v1":
                raise ValueError("unsupported Cinema job request schema")
            status_code, result = self.cinema.registry.submit(payload)
            self.send_json(status_code, result)
        except (ValueError, FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.send_json(400, {"status": "FAILED", "error": str(error)})
        except Exception as error:  # noqa: BLE001
            self.send_json(503, {"status": "FAILED", "error": str(error)})


def parse_args() -> argparse.Namespace:
    script_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("ECHOES_CINEMA_SERVICE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("ECHOES_CINEMA_SERVICE_PORT", "8090")))
    parser.add_argument("--token", default="")
    parser.add_argument("--manifest-cli", default=os.getenv("ECHOES_CINEMA_MANIFEST_CLI", ""))
    parser.add_argument("--runner", default=str(script_root / "cinema_job_runner.py"))
    parser.add_argument("--sections-root", default=os.getenv("ECHOES_CINEMA_SECTIONS_ROOT", ""))
    parser.add_argument("--audio-root", default=os.getenv("ECHOES_CINEMA_AUDIO_ROOT", ""))
    parser.add_argument("--output-root", default=os.getenv("ECHOES_CINEMA_OUTPUT_ROOT", "cinema-jobs"))
    parser.add_argument("--provider-timeout", type=float, default=float(os.getenv("ECHOES_CINEMA_PROVIDER_TIMEOUT", "180")))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("ECHOES_CINEMA_MAX_WORKERS", "1")))
    return parser.parse_args()


def main() -> int:
    config = ServiceConfig.from_args(parse_args())
    server = CinemaServer((config.host, config.port), Handler)
    server.config = config
    server.registry = JobRegistry(config)
    print(
        f"EchoesCinemaJobService READY http://{config.host}:{config.port} "
        f"sectionsRoot={config.sections_root} outputRoot={config.output_root} "
        f"manifestGenerator={'native' if config.manifest_cli else 'python'}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.registry.executor.shutdown(wait=False, cancel_futures=False)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
