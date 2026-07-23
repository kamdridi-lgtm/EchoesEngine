#!/usr/bin/env python3
"""Durable, atomic job-state ledger for Echoes Cinema.

The ledger prevents QUEUED/RUNNING jobs from disappearing when the service or
machine restarts. Non-terminal jobs are marked RECOVERABLE on reload instead of
being reported as successful or silently discarded. Writes are atomic and a
corrupt ledger fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "echoes.cinema-job-ledger.v1"
EVENT_SCHEMA = "echoes.cinema-job-ledger-event.v1"
TERMINAL_STATUSES = {"PASS", "FAILED", "BROKEN"}
ACTIVE_STATUSES = {"QUEUED", "RUNNING", "RECOVERABLE"}
ALL_STATUSES = TERMINAL_STATUSES | ACTIVE_STATUSES
ALLOWED_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"QUEUED", "RECOVERABLE", "FAILED", "BROKEN"},
    "QUEUED": {"QUEUED", "RUNNING", "RECOVERABLE", "FAILED", "BROKEN"},
    "RUNNING": {"RUNNING", "RECOVERABLE", "PASS", "FAILED", "BROKEN"},
    "RECOVERABLE": {"RECOVERABLE", "QUEUED", "RUNNING", "FAILED", "BROKEN"},
    "PASS": {"PASS"},
    "FAILED": {"FAILED", "QUEUED"},
    "BROKEN": {"BROKEN", "QUEUED"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class LedgerConfig:
    path: Path
    max_events: int = 1000

    def __post_init__(self) -> None:
        if self.max_events <= 0:
            raise ValueError("max_events must be positive")


class DurableJobLedger:
    def __init__(self, config: LedgerConfig) -> None:
        self.config = config
        self.payload = self._load()
        if self._recover_interrupted_jobs():
            self._persist()

    def _empty(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "updatedAt": utc_now(),
            "sequence": 0,
            "jobs": {},
            "events": [],
        }

    def _load(self) -> dict[str, Any]:
        path = self.config.path
        if not path.exists():
            return self._empty()
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Cinema job ledger is unreadable or corrupt: {path}: {error}") from error
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise RuntimeError(f"Cinema job ledger schema is unsupported: {path}")
        if not isinstance(payload.get("jobs"), dict) or not isinstance(payload.get("events"), list):
            raise RuntimeError(f"Cinema job ledger structure is invalid: {path}")
        if not isinstance(payload.get("sequence"), int) or payload["sequence"] < 0:
            raise RuntimeError(f"Cinema job ledger sequence is invalid: {path}")
        return payload

    def _recover_interrupted_jobs(self) -> bool:
        changed = False
        for job_id, record in self.payload["jobs"].items():
            if not isinstance(record, dict):
                raise RuntimeError(f"Cinema job ledger record is invalid: {job_id}")
            status = record.get("status")
            if status in {"QUEUED", "RUNNING"}:
                recovered = dict(record)
                recovered["status"] = "RECOVERABLE"
                recovered["recoveryReason"] = "service restarted before a terminal result was recorded"
                recovered["updatedAt"] = utc_now()
                self.payload["jobs"][job_id] = recovered
                self._append_event(job_id, status, "RECOVERABLE", recovered)
                changed = True
        return changed

    def _append_event(
        self,
        job_id: str,
        previous_status: str | None,
        status: str,
        record: dict[str, Any],
    ) -> None:
        self.payload["sequence"] += 1
        event = {
            "schema": EVENT_SCHEMA,
            "sequence": self.payload["sequence"],
            "jobId": job_id,
            "previousStatus": previous_status,
            "status": status,
            "at": utc_now(),
            "attempt": int(record.get("attempt", 1)),
        }
        self.payload["events"].append(event)
        if len(self.payload["events"]) > self.config.max_events:
            self.payload["events"] = self.payload["events"][-self.config.max_events :]

    def _persist(self) -> None:
        self.payload["updatedAt"] = utc_now()
        atomic_write_json(self.config.path, self.payload)

    def get(self, job_id: str) -> dict[str, Any] | None:
        record = self.payload["jobs"].get(job_id)
        return dict(record) if isinstance(record, dict) else None

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = [dict(record) for record in self.payload["jobs"].values() if isinstance(record, dict)]
        return sorted(jobs, key=lambda item: (str(item.get("updatedAt", "")), str(item.get("jobId", ""))), reverse=True)

    def transition(self, job_id: str, status: str, **fields: Any) -> dict[str, Any]:
        if status not in ALL_STATUSES:
            raise ValueError(f"unsupported Cinema job status: {status}")
        previous = self.get(job_id)
        previous_status = str(previous.get("status")) if previous else None
        allowed = ALLOWED_TRANSITIONS.get(previous_status, set())
        if status not in allowed:
            raise RuntimeError(f"invalid Cinema job transition for {job_id}: {previous_status} -> {status}")

        attempt = int(fields.pop("attempt", previous.get("attempt", 1) if previous else 1))
        if attempt <= 0:
            raise ValueError("attempt must be positive")
        record = dict(previous or {})
        record.update(fields)
        record.update(
            {
                "schema": "echoes.cinema-service-job.v1",
                "jobId": job_id,
                "status": status,
                "attempt": attempt,
                "updatedAt": utc_now(),
            }
        )
        if previous is None:
            record["createdAt"] = record["updatedAt"]
        else:
            record.setdefault("createdAt", previous.get("createdAt", record["updatedAt"]))
        if status in TERMINAL_STATUSES:
            record["finishedAt"] = record["updatedAt"]
        else:
            record.pop("finishedAt", None)

        self.payload["jobs"][job_id] = record
        self._append_event(job_id, previous_status, status, record)
        self._persist()
        return dict(record)


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-ledger-test-") as temp_dir:
        path = Path(temp_dir) / "job-ledger.json"
        ledger = DurableJobLedger(LedgerConfig(path=path, max_events=5))
        queued = ledger.transition("job-a", "QUEUED", modelId="proof-model")
        assert queued["status"] == "QUEUED"
        running = ledger.transition("job-a", "RUNNING")
        assert running["status"] == "RUNNING"

        restarted = DurableJobLedger(LedgerConfig(path=path, max_events=5))
        recovered = restarted.get("job-a")
        assert recovered is not None and recovered["status"] == "RECOVERABLE"
        assert "service restarted" in recovered["recoveryReason"]

        restarted.transition("job-a", "RUNNING", attempt=2)
        passed = restarted.transition("job-a", "PASS", artifactSha256="abc123")
        assert passed["attempt"] == 2 and passed["artifactSha256"] == "abc123"
        stable = DurableJobLedger(LedgerConfig(path=path, max_events=5)).get("job-a")
        assert stable is not None and stable["status"] == "PASS"

        try:
            restarted.transition("job-a", "RUNNING")
        except RuntimeError as error:
            assert "PASS -> RUNNING" in str(error)
        else:
            raise AssertionError("terminal PASS transition was not protected")

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema"] == SCHEMA
        assert len(payload["events"]) <= 5

        corrupt = Path(temp_dir) / "corrupt.json"
        corrupt.write_text("{not-json", encoding="utf-8")
        try:
            DurableJobLedger(LedgerConfig(path=corrupt))
        except RuntimeError as error:
            assert "corrupt" in str(error)
        else:
            raise AssertionError("corrupt ledger did not fail closed")

    print("CinemaJobLedger PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if args.ledger is None:
        raise SystemExit("--ledger is required unless --self-test is used")
    ledger = DurableJobLedger(LedgerConfig(path=args.ledger))
    if args.list:
        print(json.dumps(ledger.list_jobs(), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(ledger.payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
