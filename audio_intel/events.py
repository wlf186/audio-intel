from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any


class SnapshotHub:
    """Poll SQLite once and fan the latest durable state out to all SSE clients."""

    def __init__(self, loader: Callable[[], dict[str, Any]], poll_seconds: float = 0.5) -> None:
        self.loader = loader
        self.poll_seconds = poll_seconds
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.task: asyncio.Task[None] | None = None
        self.last: dict[str, Any] | None = None
        self.last_encoded = ""
        self.lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        async with self.lock:
            self.subscribers.add(queue)
            if self.task is None or self.task.done():
                self.task = asyncio.create_task(self._run())
            if self.last is not None:
                queue.put_nowait(self.last)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self.lock:
            self.subscribers.discard(queue)

    async def _run(self) -> None:
        try:
            while self.subscribers:
                snapshot = await asyncio.to_thread(self.loader)
                encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
                if encoded != self.last_encoded:
                    self.last = snapshot
                    self.last_encoded = encoded
                    for queue in tuple(self.subscribers):
                        if queue.full():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        queue.put_nowait(snapshot)
                await asyncio.sleep(self.poll_seconds)
        finally:
            self.task = None
