#!/usr/bin/env python3
"""Restart-safe Echoes Cinema job service.

This entrypoint wraps ``cinema_job_service.py`` with an atomic durable ledger.
QUEUED/RUNNING jobs become RECOVERABLE after a restart and can be explicitly
resubmitted with ``resumeRecovered=true``. No interrupted job is silently lost
or reported as successful.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import cinema_job_service as base
from cinema_job_ledger import DurableJobLedger, LedgerConfig


class DurableJobRegistry(base.JobRegistry):
    def __init__(self, config: base.ServiceConfig) -> None:
        super().__init__(config)
        self.ledger_lock = threading.RLock()
        self.retrying_jobs: set[str] = set()
        ledger_path = config.output_root / "_service" / "job-ledger.json"
        self.ledger = DurableJobLedger(LedgerConfig(path=ledger_path, max_events=5000))

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self.ledger_lock:
            if job_id in self.retrying_jobs:
                return None
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

            if resume_recovered:
                self.retrying_jobs.add(job_id)
            try:
                status_code, accepted = super().submit(request)
                if accepted.get("status") == "QUEUED":
                    attempt = int(previous.get("attempt", 0)) + 1 if previous else 1
                    durable = self.ledger.transition(
                        job_id,
                        "QUEUED",
                        attempt=attempt,
                        backend=accepted.get("backend"),
                        modelId=accepted.get("modelId"),
                        commercialUseRequired=accepted.get("commercialUseRequired", False),
                        commercialUseAllowed=accepted.get("commercialUseAllowed", False),
                        requiredCapabilities=accepted.get("requiredCapabilities", []),
                        manifestGenerator=accepted.get("manifestGenerator"),
                        sectionsCsv=request.get("sectionsCsv"),
                        audioFile=request.get("audioFile"),
                        seed=int(request.get("seed", 1337)),
                        resumeRecovered=resume_recovered,
                    )
                    accepted = {**accepted, **durable}
                return status_code, accepted
            finally:
                self.retrying_jobs.discard(job_id)

    def _run_job(
        self,
        job_id: str,
        sections_csv: Path,
        audio_file: Path | None,
        seed: int,
        commercial_use_required: bool,
    ) -> None:
        with self.ledger_lock:
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
            )

        try:
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
    server_version = "EchoesCinemaDurableJobService/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/cinema/jobs":
            if not self.require_authorized():
                return
            registry = self.cinema.registry
            if not isinstance(registry, DurableJobRegistry):
                self.send_json(500, {"status": "BROKEN", "error": "durable registry is unavailable"})
                return
            self.send_json(
                200,
                {
                    "schema": "echoes.cinema-job-list.v1",
                    "status": "PASS",
                    "jobs": registry.ledger.list_jobs(),
                },
            )
            return
        super().do_GET()


def main() -> int:
    config = base.ServiceConfig.from_args(base.parse_args())
    server = base.CinemaServer((config.host, config.port), DurableHandler)
    server.config = config
    server.registry = DurableJobRegistry(config)
    print(
        f"EchoesCinemaDurableJobService READY http://{config.host}:{config.port} "
        f"ledger={server.registry.ledger.config.path} outputRoot={config.output_root}",
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
