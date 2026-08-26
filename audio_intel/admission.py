from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import queued_count


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    code: str | None = None
    detail: str | None = None
    retry_after_seconds: int = 30
    queue_depth: int = 0
    queue_capacity: int = 0
    free_bytes: int = 0
    minimum_free_bytes: int = 0


class AdmissionController:
    """Single-API-process admission reservations around the durable SQLite queue."""

    def __init__(
        self,
        data_dir: Path,
        capacities: dict[str, int],
        max_concurrent: int,
        minimum_free_bytes: int,
    ) -> None:
        self.data_dir = data_dir
        self.capacities = capacities
        self.max_concurrent = max(1, max_concurrent)
        self.minimum_free_bytes = max(0, minimum_free_bytes)
        self._active = 0
        self._reserved = {"asr": 0, "tts": 0}
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    def reservations(self) -> dict[str, int]:
        return dict(self._reserved)

    def disk_free(self) -> int:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return int(shutil.disk_usage(self.data_dir).free)

    async def reserve(self, kind: str, expected_bytes: int) -> AdmissionDecision:
        async with self._lock:
            depth = await asyncio.to_thread(queued_count, kind)
            capacity = self.capacities[kind]
            free = await asyncio.to_thread(self.disk_free)
            if self._active >= self.max_concurrent:
                return AdmissionDecision(
                    False, "submission_concurrency_limited",
                    "Too many submissions are being persisted; retry shortly",
                    1, depth, capacity, free, self.minimum_free_bytes,
                )
            if depth + self._reserved[kind] >= capacity:
                return AdmissionDecision(
                    False, "queue_capacity_reached",
                    f"The {kind.upper()} queue has reached its configured capacity",
                    30, depth, capacity, free, self.minimum_free_bytes,
                )
            if free - max(0, expected_bytes) < self.minimum_free_bytes:
                return AdmissionDecision(
                    False, "insufficient_queue_storage",
                    "The local data volume does not have enough reserved free space",
                    300, depth, capacity, free, self.minimum_free_bytes,
                )
            self._active += 1
            self._reserved[kind] += 1
            return AdmissionDecision(
                True, queue_depth=depth, queue_capacity=capacity,
                free_bytes=free, minimum_free_bytes=self.minimum_free_bytes,
            )

    async def release(self, kind: str) -> None:
        async with self._lock:
            self._active = max(0, self._active - 1)
            self._reserved[kind] = max(0, self._reserved[kind] - 1)

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            counts = {
                kind: await asyncio.to_thread(queued_count, kind)
                for kind in ("asr", "tts")
            }
            free = await asyncio.to_thread(self.disk_free)
            return {
                "active": self._active,
                "max_concurrent": self.max_concurrent,
                "reserved": dict(self._reserved),
                "counts": counts,
                "capacities": dict(self.capacities),
                "free_bytes": free,
                "minimum_free_bytes": self.minimum_free_bytes,
            }
