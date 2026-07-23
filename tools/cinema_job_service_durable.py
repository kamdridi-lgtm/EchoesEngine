#!/usr/bin/env python3
"""Restart-safe, priority-aware Echoes Cinema job service.

The service persists every state transition, resumes interrupted jobs with
SHA-256/media validation, orders queued work by explicit priority, limits
concurrent execution, and rejects new jobs before the configured D-drive free
space reserve can be violated.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import cinema_job_service as base
from cinema_job_ledger import DurableJobLedger, LedgerConfig
from cinema_job_scheduler import PriorityJobScheduler


GIB = 1024**3
MIN_JOB_RESERVATION_BYTES = 64 * 1024**2


def env_gib(name: str, default: float, *, allow_zero: bool = False) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number of GiB") from error
    if value < 0 or (value == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier}")
    return int(value * GIB)


def request_priority(request: dict[str, Any], default: int = 50) -> int:
    value = request.get("priority", default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("priority must be an integer between 0 and 100")
    return PriorityJobScheduler.validate_priority(value)


def request_estimated_output_bytes(
    request: dict[str, Any],
    *,
    default_bytes: int,
    maximum_bytes: int,
) -> int:
    value = request.get("estimatedOutputBytes", default_bytes)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("estimatedOutputBytes must be an integer")
    if value < MIN_JOB_RESERVATION_BYTES:
        raise ValueError(f"estimatedOutputBytes must be at least {MIN_JOB_RESERVATION_BYTES}")
    if value > maximum_bytes:
        raise ValueError(f"estimatedOutputBytes exceeds configured maximum of {maximum_bytes}")
    return value


class DurableJobRegistry(base.JobRegistry):
    def __init__(self, config: base.ServiceConfig) -> None:
        super().__init__(config)
        self.ledger_lock = threading.RLock()
        self.resume_requested: set[str] = set()
        ledger_path = config.output_root / "_service" / "job-ledger.json"
        self.ledger = DurableJobLedger(LedgerConfig(path=ledger_path, max_events=5000))

        self.storage_reserve_bytes = env_gib("ECHOES_CINEMA_STORAGE_RESERVE_GIB", 20.0, allow_zero=True)
        self.default_job_reservation_bytes = env_gib("ECHOES_CINEMA_DEFAULT_JOB_GIB", 8.0)
        self.maximum_job_reservation_bytes = env_gib("ECHOES_CINEMA_MAX_JOB_GIB", 200.0)
        if self.default_job_reservation_bytes > self.maximum_job_reservation_bytes:
            raise ValueError("default Cinema job reservation exceeds configured maximum")
        self.scheduler = PriorityJobScheduler(
            max_workers=config.max_workers,
            output_root=config.output_root,
            minimum_free_bytes=self.storage_reserve_bytes,
        )

    def shutdown(self) -> None:
        self.scheduler.shutdown(wait=False)
        self.executor.shutdown(wait=False, cancel_futures=False)

    def status(self, job_id: str) -> dict[str, Any] | None:
        current = super().status(job_id)
        if current is not None:
            return current
        return self.ledger.get(job_id)

    def submit(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        job_id = base.validate_job_id(request.get("jobId"))
        resume_recovered = base.request_bool(request, "resumeRecovered")

        with self.ledger_lock:
            previous = self.ledger.get(job_id)
            previous_status = previous.get("status") if previous else None
            if previous_status == "RECOVERABLE" and not resume_recovered:
                return 409, {
                    **previous,
                    "status": "RECOVERABLE",
                    "actionRequired": "resubmit the same request with resumeRecovered=true",
                }
            if resume_recovered and previous_status != "RECOVERABLE":
                raise ValueError("resumeRecovered=true is allowed only for a RECOVERABLE job")
            if previous is not None and not resume_recovered:
                status_code = 200 if previous_status in {"PASS", "FAILED", "BROKEN"} else 202
                return status_code, previous

        sections_relative = base.safe_relative(request.get("sectionsCsv"), suffixes={".csv"}, field="sectionsCsv")
        sections_csv = base.resolve_under(self.config.sections_root, sections_relative, field="sectionsCsv")

        audio_file: Path | None = None
        if request.get("audioFile") not in {None, ""}:
            audio_relative = base.safe_relative(
                request.get("audioFile"),
                suffixes={".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg"},
                field="audioFile",
            )
            audio_file = base.resolve_under(self.config.audio_root, audio_relative, field="audioFile")

        seed = int(request.get("seed", previous.get("seed", 1337) if previous else 1337))
        if seed < 0 or seed > 0xFFFFFFFF:
            raise ValueError("seed must fit in an unsigned 32-bit integer")
        commercial_use_required = base.request_bool(request, "commercialUseRequired")
        priority = request_priority(request, int(previous.get("priority", 50)) if previous else 50)
        estimated_output_bytes = request_estimated_output_bytes(
            request,
            default_bytes=int(previous.get("estimatedOutputBytes", self.default_job_reservation_bytes))
            if previous
            else self.default_job_reservation_bytes,
            maximum_bytes=self.maximum_job_reservation_bytes,
        )

        health = base.fetch_provider_health(
            self.config.provider_endpoint,
            self.config.provider_token,
            self.config.provider_timeout,
        )
        required_capabilities = base.validate_provider_health(
            health,
            explicit_requirements={"textToVideo"},
            require_real_model=True,
            require_commercial_use=commercial_use_required,
        )

        self.scheduler.reserve_storage(job_id, estimated_output_bytes)
        queued_in_ledger = False
        try:
            accepted = {
                "schema": "echoes.cinema-service-job.v1",
                "jobId": job_id,
                "status": "QUEUED",
                "backend": "http-provider",
                "realModelLoaded": True,
                "modelId": health.get("modelId"),
                "modelRevision": health.get("modelRevision"),
                "commercialUseRequired": commercial_use_required,
                "commercialUseAllowed": health.get("commercialUseAllowed") is True,
                "requiredCapabilities": sorted(required_capabilities),
                "manifestGenerator": "native-render-manifest-cli"
                if self.config.manifest_cli
                else "python-render-manifest-v1",
                "priority": priority,
                "estimatedOutputBytes": estimated_output_bytes,
                "resumeRecovered": resume_recovered,
                "resumeRequested": resume_recovered,
            }

            with self.ledger_lock:
                latest = self.ledger.get(job_id)
                latest_status = latest.get("status") if latest else None
                if resume_recovered and latest_status != "RECOVERABLE":
                    raise RuntimeError("recoverable job state changed during admission")
                if not resume_recovered and latest is not None:
                    raise RuntimeError("job state appeared during admission")
                attempt = int(latest.get("attempt", 0)) + 1 if latest else 1
                durable = self.ledger.transition(
                    job_id,
                    "QUEUED",
                    attempt=attempt,
                    **{key: value for key, value in accepted.items() if key not in {"schema", "jobId", "status"}},
                    sectionsCsv=request.get("sectionsCsv"),
                    audioFile=request.get("audioFile"),
                    seed=seed,
                )
                queued_in_ledger = True
                if resume_recovered:
                    self.resume_requested.add(job_id)

            with self.lock:
                self.jobs[job_id] = accepted

            scheduled = self.scheduler.submit_reserved(
                job_id,
                priority,
                estimated_output_bytes,
                self._run_job,
                job_id,
                sections_csv,
                audio_file,
                seed,
                commercial_use_required,
            )
            return 202, {
                **accepted,
                **durable,
                "queueSequence": scheduled["sequence"],
                "scheduler": scheduled["scheduler"],
            }
        except BaseException as error:
            self.scheduler.release_storage(job_id)
            self.resume_requested.discard(job_id)
            with self.lock:
                self.jobs.pop(job_id, None)
            if queued_in_ledger:
                with self.ledger_lock:
                    current = self.ledger.get(job_id)
                    if current and current.get("status") == "QUEUED":
                        self.ledger.transition(job_id, "FAILED", error=f"scheduler admission failed: {error}")
            raise

    def _run_resume_job(
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
                "resumeRequested": True,
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
            "--resume",
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
                "resumeRequested": True,
                "error": f"resumed Cinema runner exited with {completed.returncode} without job-result.json",
            }
        with self.lock:
            self.jobs[job_id] = result

    def _run_job(
        self,
        job_id: str,
        sections_csv: Path,
        audio_file: Path | None,
        seed: int,
        commercial_use_required: bool,
    ) -> None:
        with self.ledger_lock:
            resume_existing = job_id in self.resume_requested
            self.resume_requested.discard(job_id)
            queued = self.ledger.get(job_id)
            attempt = int(queued.get("attempt", 1)) if queued else 1
            self.ledger.transition(
                job_id,
                "RUNNING",
                attempt=attempt,
                sectionsCsv=str(sections_csv),
                audioFile=str(audio_file) if audio_file else None,
                seed=seed,
                commercialUseRequired=commercial_use_required,
                resumeRequested=resume_existing,
            )

        try:
            if resume_existing:
                self._run_resume_job(job_id, sections_csv, audio_file, seed, commercial_use_required)
            else:
                super()._run_job(job_id, sections_csv, audio_file, seed, commercial_use_required)
            result = super().status(job_id)
            if result is None:
                result = {
                    "schema": "echoes.cinema-service-job.v1",
                    "jobId": job_id,
                    "status": "BROKEN",
                    "error": "durable service completed without a readable result",
                }
            terminal = str(result.get("status", "BROKEN"))
            if terminal not in {"PASS", "FAILED", "BROKEN"}:
                terminal = "BROKEN"
                result = {**result, "error": "job ended without a terminal truth status"}
            terminal_fields = dict(result)
            for reserved in ("jobId", "status", "attempt", "createdAt", "updatedAt", "finishedAt"):
                terminal_fields.pop(reserved, None)
            with self.ledger_lock:
                self.ledger.transition(job_id, terminal, attempt=attempt, **terminal_fields)
        except BaseException as error:
            with self.ledger_lock:
                current = self.ledger.get(job_id)
                if current and current.get("status") not in {"PASS", "FAILED", "BROKEN"}:
                    self.ledger.transition(
                        job_id,
                        "BROKEN",
                        attempt=attempt,
                        error=f"durable job worker crashed: {error}",
                    )
            raise


class DurableHandler(base.Handler):
    server_version = "EchoesCinemaDurableJobService/1.2"

    def do_GET(self) -> None:  # noqa: N802
        if not self.require_authorized():
            return
        registry = self.cinema.registry
        if not isinstance(registry, DurableJobRegistry):
            self.send_json(500, {"status": "BROKEN", "error": "durable registry is unavailable"})
            return

        if self.path == "/health":
            scheduler = registry.scheduler.snapshot()
            try:
                provider = base.fetch_provider_health(
                    self.cinema.config.provider_endpoint,
                    self.cinema.config.provider_token,
                    self.cinema.config.provider_timeout,
                )
                self.send_json(
                    200,
                    {
                        "schema": "echoes.cinema-service-health.v1",
                        "status": "PASS",
                        "backend": "cinema-durable-priority-service",
                        "realModelLoaded": provider.get("realModelLoaded") is True,
                        "acceptingRealJobs": base.accepting_real_jobs(provider),
                        "acceptingCommercialJobs": base.accepting_commercial_jobs(provider),
                        "provider": provider,
                        "maxWorkers": self.cinema.config.max_workers,
                        "manifestGenerator": "native-render-manifest-cli"
                        if self.cinema.config.manifest_cli
                        else "python-render-manifest-v1",
                        "scheduler": scheduler,
                    },
                )
            except Exception as error:  # noqa: BLE001
                self.send_json(
                    200,
                    {
                        "schema": "echoes.cinema-service-health.v1",
                        "status": "PARTIAL",
                        "backend": "cinema-durable-priority-service",
                        "realModelLoaded": False,
                        "acceptingRealJobs": False,
                        "acceptingCommercialJobs": False,
                        "scheduler": scheduler,
                        "error": str(error),
                    },
                )
            return

        if self.path == "/v1/cinema/jobs":
            self.send_json(
                200,
                {
                    "schema": "echoes.cinema-job-list.v1",
                    "status": "PASS",
                    "jobs": registry.ledger.list_jobs(),
                    "scheduler": registry.scheduler.snapshot(),
                },
            )
            return

        if self.path == "/v1/cinema/scheduler":
            self.send_json(200, registry.scheduler.snapshot())
            return

        super().do_GET()


def main() -> int:
    config = base.ServiceConfig.from_args(base.parse_args())
    server = base.CinemaServer((config.host, config.port), DurableHandler)
    server.config = config
    server.registry = DurableJobRegistry(config)
    print(
        f"EchoesCinemaDurableJobService READY http://{config.host}:{config.port} "
        f"ledger={server.registry.ledger.config.path} outputRoot={config.output_root} "
        f"maxWorkers={config.max_workers} storageReserveBytes={server.registry.storage_reserve_bytes}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.registry.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
