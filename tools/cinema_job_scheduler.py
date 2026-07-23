#!/usr/bin/env python3
"""Priority, concurrency, and storage admission control for Echoes Cinema jobs.

Jobs are ordered by priority (100 highest) and FIFO sequence inside the same
priority. Every queued/running job owns a conservative storage reservation. A
new job is rejected before execution when the current free space minus all
reservations would fall below the configured emergency reserve.
"""

from __future__ import annotations

import argparse
import queue
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


STOP_JOB_ID = "__ECHOES_SCHEDULER_STOP__"


class StorageAdmissionError(RuntimeError):
    """Raised when a job cannot be admitted without violating free-space policy."""


@dataclass(order=True)
class _PriorityItem:
    sort_key: tuple[int, int]
    job_id: str = field(compare=False)
    function: Callable[..., Any] | None = field(compare=False)
    args: tuple[Any, ...] = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False)


class StorageBudget:
    def __init__(
        self,
        output_root: Path,
        *,
        minimum_free_bytes: int,
        free_space_provider: Callable[[Path], int] | None = None,
    ) -> None:
        if minimum_free_bytes < 0:
            raise ValueError("minimum_free_bytes must be non-negative")
        self.output_root = output_root.resolve()
        self.minimum_free_bytes = int(minimum_free_bytes)
        self._free_space_provider = free_space_provider or self._system_free_bytes
        self._reservations: dict[str, int] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _system_free_bytes(path: Path) -> int:
        path.mkdir(parents=True, exist_ok=True)
        return int(shutil.disk_usage(path).free)

    def reserve(self, job_id: str, requested_bytes: int) -> dict[str, Any]:
        if not job_id:
            raise ValueError("job_id is required")
        if requested_bytes <= 0:
            raise ValueError("requested_bytes must be positive")
        with self._lock:
            if job_id in self._reservations:
                raise ValueError(f"storage is already reserved for job: {job_id}")
            free_bytes = int(self._free_space_provider(self.output_root))
            committed_bytes = sum(self._reservations.values())
            projected_free_bytes = free_bytes - committed_bytes - int(requested_bytes)
            if projected_free_bytes < self.minimum_free_bytes:
                raise StorageAdmissionError(
                    "insufficient storage for Cinema job: "
                    f"free={free_bytes} committed={committed_bytes} requested={requested_bytes} "
                    f"projected={projected_free_bytes} requiredReserve={self.minimum_free_bytes}"
                )
            self._reservations[job_id] = int(requested_bytes)
            return self.snapshot()

    def release(self, job_id: str) -> int:
        with self._lock:
            return int(self._reservations.pop(job_id, 0))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            free_bytes = int(self._free_space_provider(self.output_root))
            committed_bytes = sum(self._reservations.values())
            return {
                "schema": "echoes.cinema-storage-budget.v1",
                "outputRoot": str(self.output_root),
                "freeBytes": free_bytes,
                "minimumFreeBytes": self.minimum_free_bytes,
                "reservedBytes": committed_bytes,
                "projectedFreeBytes": free_bytes - committed_bytes,
                "reservationCount": len(self._reservations),
                "reservations": dict(sorted(self._reservations.items())),
            }


class PriorityJobScheduler:
    def __init__(
        self,
        *,
        max_workers: int,
        output_root: Path,
        minimum_free_bytes: int,
        free_space_provider: Callable[[Path], int] | None = None,
    ) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self.max_workers = int(max_workers)
        self.storage = StorageBudget(
            output_root,
            minimum_free_bytes=minimum_free_bytes,
            free_space_provider=free_space_provider,
        )
        self._queue: queue.PriorityQueue[_PriorityItem] = queue.PriorityQueue()
        self._lock = threading.RLock()
        self._sequence = 0
        self._queued: dict[str, dict[str, Any]] = {}
        self._running: dict[str, dict[str, Any]] = {}
        self._failures: dict[str, str] = {}
        self._closed = False
        self._workers = [
            threading.Thread(target=self._worker, name=f"echoes-cinema-priority-{index + 1}", daemon=True)
            for index in range(self.max_workers)
        ]
        for worker in self._workers:
            worker.start()

    @staticmethod
    def validate_priority(priority: int) -> int:
        if isinstance(priority, bool):
            raise ValueError("priority must be an integer between 0 and 100")
        parsed = int(priority)
        if parsed < 0 or parsed > 100:
            raise ValueError("priority must be between 0 and 100")
        return parsed

    def reserve_storage(self, job_id: str, estimated_output_bytes: int) -> dict[str, Any]:
        return self.storage.reserve(job_id, estimated_output_bytes)

    def release_storage(self, job_id: str) -> int:
        return self.storage.release(job_id)

    def submit_reserved(
        self,
        job_id: str,
        priority: int,
        estimated_output_bytes: int,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        parsed_priority = self.validate_priority(priority)
        with self._lock:
            if self._closed:
                raise RuntimeError("Cinema scheduler is shut down")
            if job_id in self._queued or job_id in self._running:
                raise ValueError(f"job is already queued or running: {job_id}")
            reservations = self.storage.snapshot()["reservations"]
            if job_id not in reservations:
                raise RuntimeError(f"storage must be reserved before queueing job: {job_id}")
            self._sequence += 1
            sequence = self._sequence
            record = {
                "jobId": job_id,
                "priority": parsed_priority,
                "sequence": sequence,
                "estimatedOutputBytes": int(estimated_output_bytes),
                "status": "QUEUED",
            }
            self._queued[job_id] = record
            self._queue.put(
                _PriorityItem(
                    sort_key=(-parsed_priority, sequence),
                    job_id=job_id,
                    function=function,
                    args=args,
                    kwargs=kwargs,
                )
            )
            return {**record, "scheduler": self.snapshot()}

    def submit(
        self,
        job_id: str,
        priority: int,
        estimated_output_bytes: int,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.reserve_storage(job_id, estimated_output_bytes)
        try:
            return self.submit_reserved(
                job_id,
                priority,
                estimated_output_bytes,
                function,
                *args,
                **kwargs,
            )
        except BaseException:
            self.release_storage(job_id)
            raise

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item.job_id == STOP_JOB_ID:
                self._queue.task_done()
                return
            with self._lock:
                queued = self._queued.pop(item.job_id, None) or {
                    "jobId": item.job_id,
                    "priority": -item.sort_key[0],
                    "sequence": item.sort_key[1],
                }
                self._running[item.job_id] = {**queued, "status": "RUNNING"}
            try:
                if item.function is None:
                    raise RuntimeError("scheduler received a job without a function")
                item.function(*item.args, **item.kwargs)
            except BaseException as error:  # noqa: BLE001 - scheduler must survive a failed worker task
                with self._lock:
                    self._failures[item.job_id] = f"{type(error).__name__}: {error}"
                    if len(self._failures) > 20:
                        oldest = next(iter(self._failures))
                        self._failures.pop(oldest, None)
            finally:
                with self._lock:
                    self._running.pop(item.job_id, None)
                self.storage.release(item.job_id)
                self._queue.task_done()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            queued = sorted(
                (dict(record) for record in self._queued.values()),
                key=lambda item: (-int(item["priority"]), int(item["sequence"])),
            )
            running = sorted((dict(record) for record in self._running.values()), key=lambda item: item["jobId"])
            return {
                "schema": "echoes.cinema-priority-scheduler.v1",
                "status": "PASS" if not self._closed else "DORMANT",
                "maxWorkers": self.max_workers,
                "queuedCount": len(queued),
                "runningCount": len(running),
                "queuedJobs": queued,
                "runningJobs": running,
                "recentWorkerFailures": dict(self._failures),
                "storage": self.storage.snapshot(),
            }

    def join(self) -> None:
        self._queue.join()

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if not wait:
            return
        self._queue.join()
        for index in range(len(self._workers)):
            self._queue.put(
                _PriorityItem(
                    sort_key=(10**9, self._sequence + index + 1),
                    job_id=STOP_JOB_ID,
                    function=None,
                    args=(),
                    kwargs={},
                )
            )
        for worker in self._workers:
            worker.join(timeout=5)


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="echoes-scheduler-test-") as temp_dir:
        root = Path(temp_dir)
        free_bytes = 1_000
        scheduler = PriorityJobScheduler(
            max_workers=1,
            output_root=root,
            minimum_free_bytes=100,
            free_space_provider=lambda _path: free_bytes,
        )
        order: list[str] = []
        blocker_started = threading.Event()
        release_blocker = threading.Event()

        def blocker() -> None:
            blocker_started.set()
            if not release_blocker.wait(timeout=5):
                raise RuntimeError("scheduler self-test blocker timed out")
            order.append("blocker")

        def record(name: str) -> None:
            order.append(name)

        scheduler.submit("blocker", 50, 100, blocker)
        assert blocker_started.wait(timeout=2)
        scheduler.submit("low", 10, 200, record, "low")
        scheduler.submit("high", 90, 200, record, "high")
        try:
            scheduler.submit("too-large", 100, 500, record, "too-large")
        except StorageAdmissionError as error:
            assert "insufficient storage" in str(error)
        else:
            raise AssertionError("storage admission did not fail closed")

        queued = scheduler.snapshot()
        assert [job["jobId"] for job in queued["queuedJobs"]] == ["high", "low"]
        assert queued["storage"]["reservedBytes"] == 500
        release_blocker.set()
        scheduler.join()
        assert order == ["blocker", "high", "low"]
        assert scheduler.snapshot()["storage"]["reservedBytes"] == 0
        scheduler.shutdown(wait=True)

    print("CinemaJobScheduler PASS priority=validated storage=fail-closed concurrency=bounded")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    raise SystemExit("--self-test is required")


if __name__ == "__main__":
    raise SystemExit(main())
