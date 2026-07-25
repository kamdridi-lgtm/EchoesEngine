#!/usr/bin/env python3
"""Autonomous first-real-clip proof runner for the local Echoes Cinema control center.

The control center starts immediately and stays responsive. This worker waits for the
real provider, resumes an incomplete proof, validates the final media, and packages
truthful evidence without exposing provider tokens in files or command lines.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cinema_real_input_audio as real_audio

SCHEMA = "echoes.cinema-p0-autopilot.v1"
JOB_ID = "echoes-first-real-ai-clip"
PROOF_DURATION_SECONDS = 4.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def tail_text(path: Path, limit: int = 30) -> str:
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(line for line in lines[-limit:] if line.strip())


def truthy_environment(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


@dataclass(frozen=True)
class AutopilotConfig:
    workspace: Path
    runtime_root: Path
    repo_root: Path
    provider_endpoint: str
    provider_health_url: str
    provider_token: str
    enabled: bool
    provider_mode: str
    wait_timeout_seconds: float = 7200.0
    provider_timeout_seconds: float = 3600.0

    @classmethod
    def from_environment(cls) -> "AutopilotConfig":
        runtime_root = Path(os.getenv("ECHOES_CINEMA_RUNTIME_ROOT", r"D:\A.I\EchoesCinema\runtime"))
        workspace = runtime_root.parent
        repo_root = Path(__file__).resolve().parents[1]
        provider_endpoint = os.getenv("ECHOES_RENDER_ENDPOINT", "http://127.0.0.1:8081/v1/render")
        provider_health_url = os.getenv("ECHOES_RENDER_HEALTH_URL", "http://127.0.0.1:8081/health")
        provider_mode = os.getenv("ECHOES_CINEMA_PROVIDER_MODE", "real").strip().lower() or "real"
        enabled = truthy_environment("ECHOES_CINEMA_P0_AUTORUN", True) and provider_mode == "real"
        return cls(
            workspace=workspace,
            runtime_root=runtime_root,
            repo_root=repo_root,
            provider_endpoint=provider_endpoint,
            provider_health_url=provider_health_url,
            provider_token=os.getenv("ECHOES_RENDER_TOKEN", ""),
            enabled=enabled,
            provider_mode=provider_mode,
            wait_timeout_seconds=float(os.getenv("ECHOES_CINEMA_P0_WAIT_TIMEOUT", "7200")),
            provider_timeout_seconds=float(os.getenv("ECHOES_CINEMA_P0_PROVIDER_TIMEOUT", "3600")),
        )


class P0Autopilot:
    def __init__(self, config: AutopilotConfig):
        self.config = config
        self.output_root = config.workspace / "proofs" / "first-real-ai-clip"
        self.status_path = config.runtime_root / "p0-autopilot-status.json"
        self.failure_path = self.output_root / "run-failure.txt"
        self.audio_path = self.output_root / "proof-audio.wav"
        self.audio_evidence_path = self.output_root / "proof-audio-source.json"
        self.final_mp4 = self.output_root / f"{JOB_ID}.mp4"
        self.job_result_path = self.output_root / "job-result.json"
        self.qc_path = self.output_root / "video-qc.json"
        self.provider_health_path = self.output_root / "provider-health.json"
        self.autopilot_log = self.output_root / "autopilot.log"
        self.autopilot_error_log = self.output_root / "autopilot-error.log"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._process_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.config.runtime_root.mkdir(parents=True, exist_ok=True)
        if not config.enabled:
            self._write_status(
                "DORMANT",
                "DISABLED",
                "P0 autopilot is disabled for this provider mode or environment.",
            )

    def _write_status(
        self,
        status: str,
        phase: str,
        message: str,
        *,
        blocker: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": SCHEMA,
            "timestampUtc": utc_now(),
            "status": status,
            "phase": phase,
            "message": message,
            "blocker": blocker,
            "enabled": self.config.enabled,
            "providerMode": self.config.provider_mode,
            "providerHealthUrl": self.config.provider_health_url,
            "workspace": str(self.config.workspace),
            "proofDirectory": str(self.output_root),
            "jobId": JOB_ID,
            "systemDriveWritesAllowed": False,
            "secretsPersisted": False,
        }
        if extra:
            payload.update(extra)
        with self._status_lock:
            atomic_json(self.status_path, payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        payload = load_json(self.status_path)
        if payload:
            return payload
        return {
            "schema": SCHEMA,
            "status": "MISSING",
            "phase": "NOT_STARTED",
            "message": "No P0 autopilot status has been written yet.",
            "enabled": self.config.enabled,
            "providerMode": self.config.provider_mode,
            "systemDriveWritesAllowed": False,
            "secretsPersisted": False,
        }

    def start(self) -> None:
        if not self.config.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_guarded, name="echoes-p0-autopilot", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop_event.set()
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def verified_proof(self) -> tuple[bool, str]:
        result = load_json(self.job_result_path)
        qc = load_json(self.qc_path)
        if not result:
            return False, "job-result.json is missing or unreadable"
        if result.get("status") != "PASS":
            return False, f"job-result status is {result.get('status')!r}"
        if result.get("backendStatus") != "REAL":
            return False, f"backendStatus is {result.get('backendStatus')!r}"
        if not qc or qc.get("status") != "PASS":
            return False, "video-qc.json is missing or not PASS"
        if not self.final_mp4.is_file() or self.final_mp4.stat().st_size <= 0:
            return False, "final MP4 is missing or empty"
        evidence = result.get("artifactEvidence")
        if not isinstance(evidence, dict):
            return False, "artifactEvidence is missing"
        final_evidence = evidence.get("finalMp4")
        if not isinstance(final_evidence, dict) or not final_evidence.get("sha256"):
            return False, "final MP4 SHA-256 evidence is missing"
        audio_evidence = load_json(self.audio_evidence_path)
        if not audio_evidence or audio_evidence.get("status") != "PASS":
            return False, "real input audio evidence is missing or not PASS"
        if audio_evidence.get("generatedByAutopilot") is not False:
            return False, "P0 audio was generated by the autopilot instead of using real input"
        if not audio_evidence.get("sourceSha256") or not audio_evidence.get("outputSha256"):
            return False, "real input audio SHA-256 evidence is missing"
        resume = result.get("resume")
        if not isinstance(resume, dict) or resume.get("requested") is not True:
            return False, "resume evidence is missing"
        return True, "Real input audio, model load, REAL backend, QC, MP4, SHA-256, and resume evidence passed."

    def _archive_old_failure(self) -> None:
        if not self.failure_path.is_file():
            return
        attempts = self.output_root / "attempts"
        attempts.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        destination = attempts / f"autopilot-{stamp}-run-failure.txt"
        os.replace(self.failure_path, destination)

    def _write_failure(self, blocker: str) -> None:
        atomic_json(
            self.failure_path,
            {
                "timestampUtc": utc_now(),
                "status": "FAILED",
                "firstBlocker": blocker,
                "source": "cinema-p0-autopilot",
                "systemDriveWritesAllowed": False,
            },
        )

    def _provider_health(self) -> dict[str, Any]:
        request = urllib.request.Request(self.config.provider_health_url)
        request.add_header("Authorization", f"Bearer {self.config.provider_token}")
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("provider health response is not a JSON object")
        return payload

    def _wait_for_provider(self) -> dict[str, Any]:
        if not self.config.provider_token:
            raise RuntimeError("ECHOES_RENDER_TOKEN is missing from the control-center environment")
        deadline = time.monotonic() + max(30.0, self.config.wait_timeout_seconds)
        last_message = "provider has not answered yet"
        last_status_write = 0.0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            try:
                health = self._provider_health()
                if health.get("realModelLoaded") is True:
                    atomic_json(self.provider_health_path, health)
                    return health
                load_error = str(health.get("loadError") or "").strip()
                if load_error:
                    raise RuntimeError(f"model load failed: {load_error}")
                last_message = "provider is reachable but realModelLoaded is not true yet"
            except urllib.error.HTTPError as error:
                if error.code in {401, 403}:
                    raise RuntimeError(f"provider health authorization failed with HTTP {error.code}") from error
                last_message = f"provider health HTTP {error.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as error:
                last_message = str(error)

            now = time.monotonic()
            if now - last_status_write >= 15.0:
                self._write_status(
                    "PARTIAL",
                    "WAITING_PROVIDER",
                    "The dashboard is online. P0 is waiting for the real model to finish loading.",
                    blocker=last_message,
                )
                last_status_write = now
            self._stop_event.wait(5.0)

        if self._stop_event.is_set():
            raise RuntimeError("P0 autopilot was stopped before the provider became ready")
        raise RuntimeError(
            f"timed out after {int(self.config.wait_timeout_seconds)} seconds waiting for realModelLoaded=true; last provider message: {last_message}"
        )

    def _wait_for_real_audio(self) -> dict[str, Any]:
        deadline = time.monotonic() + max(30.0, self.config.wait_timeout_seconds)
        explicit_path = os.getenv("ECHOES_CINEMA_P0_AUDIO")
        last_status_write = 0.0
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            source, selection_method = real_audio.discover_real_input(
                self.config.workspace, explicit_path
            )
            if source is not None:
                return real_audio.prepare_real_input(
                    source,
                    self.audio_path,
                    self.audio_evidence_path,
                    selection_method=selection_method,
                    proof_duration_seconds=PROOF_DURATION_SECONDS,
                )
            now = time.monotonic()
            if now - last_status_write >= 15.0:
                self._write_status(
                    "PARTIAL",
                    "WAITING_INPUT_AUDIO",
                    "P0 is waiting for a real song file. Synthetic fallback audio is disabled.",
                    blocker=f"No supported audio file was found in {self.config.workspace / 'input'}",
                    extra={
                        "inputDirectory": str(self.config.workspace / "input"),
                        "supportedExtensions": sorted(real_audio.SUPPORTED_EXTENSIONS),
                        "syntheticFallbackAllowed": False,
                    },
                )
                last_status_write = now
            self._stop_event.wait(5.0)
        if self._stop_event.is_set():
            raise RuntimeError("P0 autopilot stopped before real input audio became available")
        raise RuntimeError(
            f"timed out after {int(self.config.wait_timeout_seconds)} seconds waiting for real input audio in "
            f"{self.config.workspace / 'input'}"
        )

    def _copy_provider_logs(self) -> None:
        status = load_json(self.config.runtime_root / "provider-worker-status.json") or {}
        pairs = (
            (status.get("stdoutLog"), self.output_root / "provider.log"),
            (status.get("stderrLog"), self.output_root / "provider-error.log"),
        )
        for source_value, destination in pairs:
            if not source_value:
                continue
            source = Path(str(source_value))
            try:
                if source.is_file():
                    shutil.copy2(source, destination)
            except OSError:
                continue

    def _run_runner(self) -> None:
        runner = self.config.repo_root / "tools" / "cinema_job_runner.py"
        fixture = self.config.repo_root / "tests" / "fixtures" / "first_real_clip_sections.csv"
        if not runner.is_file():
            raise RuntimeError(f"Cinema job runner is missing: {runner}")
        if not fixture.is_file():
            raise RuntimeError(f"P0 section fixture is missing: {fixture}")
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise RuntimeError("FFmpeg and FFprobe must be available in PATH for P0 assembly and QC")

        environment = os.environ.copy()
        environment["ECHOES_RENDER_ENDPOINT"] = self.config.provider_endpoint
        environment["ECHOES_RENDER_HEALTH_URL"] = self.config.provider_health_url
        environment["ECHOES_RENDER_HOST_ALLOWLIST"] = "127.0.0.1,localhost"
        environment["ECHOES_RENDER_TOKEN"] = self.config.provider_token
        command = [
            sys.executable,
            str(runner),
            str(fixture),
            str(self.output_root),
            "--job-id",
            JOB_ID,
            "--seed",
            "7331",
            "--backend",
            "http",
            "--audio",
            str(self.audio_path),
            "--provider-timeout",
            str(int(self.config.provider_timeout_seconds)),
            "--resume",
        ]

        with self.autopilot_log.open("a", encoding="utf-8") as stdout, self.autopilot_error_log.open(
            "a", encoding="utf-8"
        ) as stderr:
            stdout.write(f"\n[{utc_now()}] starting resumable P0 runner\n")
            stdout.flush()
            process = subprocess.Popen(
                command,
                cwd=self.config.workspace,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            with self._process_lock:
                self._process = process
            try:
                while process.poll() is None:
                    if self._stop_event.wait(2.0):
                        process.terminate()
                        raise RuntimeError("P0 runner was stopped by control-center shutdown")
                if process.returncode != 0:
                    detail = tail_text(self.autopilot_error_log) or tail_text(self.output_root / "render.log")
                    raise RuntimeError(f"P0 Cinema runner failed with exit code {process.returncode}: {detail}")
            finally:
                with self._process_lock:
                    self._process = None

    def _build_evidence(self, run_error: str | None = None) -> str | None:
        bundler = self.config.repo_root / "tools" / "cinema_p0_evidence_bundle.py"
        if not bundler.is_file():
            return f"evidence bundler is missing: {bundler}"
        output = self.config.workspace / "proofs" / "evidence" / "latest-p0-evidence.zip"
        command = [
            sys.executable,
            str(bundler),
            "--workspace",
            str(self.config.workspace),
            "--proof-dir",
            str(self.output_root),
            "--output",
            str(output),
        ]
        if run_error:
            command.extend(["--run-error", run_error])
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            return completed.stderr.strip() or completed.stdout.strip() or "evidence bundle creation failed"
        return None

    def _run_guarded(self) -> None:
        try:
            verified, detail = self.verified_proof()
            if verified:
                self._write_status(
                    "REAL",
                    "ALREADY_COMPLETE",
                    "A complete first REAL AI clip already exists; no duplicate render was started.",
                    extra={"finalMp4": str(self.final_mp4), "jobResult": str(self.job_result_path)},
                )
                return

            self._write_status(
                "PARTIAL",
                "WAITING_INPUT_AUDIO",
                "P0 is selecting a real song input before any render begins.",
                blocker=detail,
                extra={"syntheticFallbackAllowed": False},
            )
            audio_evidence = self._wait_for_real_audio()
            self._write_status(
                "PARTIAL",
                "WAITING_PROVIDER",
                "Real input audio is verified. P0 is waiting for the real model to finish loading.",
                extra={
                    "audioSource": audio_evidence.get("sourcePath"),
                    "audioSourceSha256": audio_evidence.get("sourceSha256"),
                    "audioEvidence": str(self.audio_evidence_path),
                    "syntheticFallbackAllowed": False,
                },
            )
            health = self._wait_for_provider()
            self._archive_old_failure()
            self._copy_provider_logs()
            self._write_status(
                "PARTIAL",
                "RENDERING",
                "The real model is loaded. Echoes Cinema is rendering and will resume validated clips after interruption.",
                extra={
                    "modelId": health.get("modelId"),
                    "modelRevision": health.get("modelRevision"),
                    "gpuName": (health.get("gpu") or {}).get("name") if isinstance(health.get("gpu"), dict) else None,
                    "audioSource": audio_evidence.get("sourcePath"),
                    "audioSourceSha256": audio_evidence.get("sourceSha256"),
                    "audioEvidence": str(self.audio_evidence_path),
                    "syntheticFallbackAllowed": False,
                },
            )
            self._run_runner()
            self._copy_provider_logs()
            verified, detail = self.verified_proof()
            if not verified:
                raise RuntimeError(f"P0 runner exited successfully but proof verification failed: {detail}")
            bundle_warning = self._build_evidence()
            self._write_status(
                "REAL",
                "COMPLETE",
                detail,
                extra={
                    "finalMp4": str(self.final_mp4),
                    "jobResult": str(self.job_result_path),
                    "videoQc": str(self.qc_path),
                    "audioEvidence": str(self.audio_evidence_path),
                    "audioSourceSha256": audio_evidence.get("sourceSha256"),
                    "evidenceZip": str(self.config.workspace / "proofs" / "evidence" / "latest-p0-evidence.zip"),
                    "evidenceWarning": bundle_warning,
                },
            )
        except Exception as error:  # noqa: BLE001 - exact blocker is the product requirement
            blocker = str(error)
            self._copy_provider_logs()
            self._write_failure(blocker)
            bundle_warning = self._build_evidence(blocker)
            self._write_status(
                "BROKEN",
                "FAILED",
                "P0 stopped safely. The exact blocker and evidence were preserved for the next automatic resume.",
                blocker=blocker,
                extra={"evidenceWarning": bundle_warning},
            )


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-p0-autopilot-test-") as temporary:
        root = Path(temporary)
        workspace = root / "D-drive-simulation" / "EchoesCinema"
        runtime = workspace / "runtime"
        config = AutopilotConfig(
            workspace=workspace,
            runtime_root=runtime,
            repo_root=Path(__file__).resolve().parents[1],
            provider_endpoint="http://127.0.0.1:18081/v1/render",
            provider_health_url="http://127.0.0.1:18081/health",
            provider_token="self-test-token",
            enabled=False,
            provider_mode="real",
        )
        autopilot = P0Autopilot(config)
        autopilot.output_root.mkdir(parents=True, exist_ok=True)
        atomic_json(
            autopilot.job_result_path,
            {
                "status": "PASS",
                "backendStatus": "REAL",
                "resume": {"requested": True},
                "artifactEvidence": {"finalMp4": {"sha256": "a" * 64}},
            },
        )
        atomic_json(autopilot.qc_path, {"status": "PASS"})
        autopilot.final_mp4.write_bytes(b"fake-mp4-proof")
        atomic_json(
            autopilot.audio_evidence_path,
            {
                "status": "PASS",
                "generatedByAutopilot": False,
                "sourceSha256": "b" * 64,
                "outputSha256": "c" * 64,
            },
        )
        verified, reason = autopilot.verified_proof()
        assert verified, reason
        atomic_json(autopilot.qc_path, {"status": "FAILED"})
        verified, _ = autopilot.verified_proof()
        assert not verified
        status = autopilot.snapshot()
        assert status["status"] == "DORMANT"
        raw_status = autopilot.status_path.read_text(encoding="utf-8")
        assert "self-test-token" not in raw_status
    print("CinemaP0Autopilot PASS idempotent-proof=validated real-input-audio=required secrets=absent resume=required")
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
