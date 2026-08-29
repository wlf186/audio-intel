from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any


class SnapshotHub:
    """Poll SQLite once and fan the latest durable state out to all SSE clients."""

    def __init__(
        self,
        loader: Callable[[], dict[str, Any]],
        poll_seconds: float = 0.5,
        semantic_key: Callable[[dict[str, Any]], Any] | None = None,
        revision_loader: Callable[[], Any] | None = None,
    ) -> None:
        self.loader = loader
        self.poll_seconds = poll_seconds
        self.semantic_key = semantic_key or (lambda snapshot: snapshot)
        self.revision_loader = revision_loader
        self.subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.task: asyncio.Task[None] | None = None
        self.last: dict[str, Any] | None = None
        self.last_semantic_encoded = ""
        self.last_revision_encoded = ""
        self.lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)
        revision = await asyncio.to_thread(self.revision_loader) if self.revision_loader else None
        snapshot = await asyncio.to_thread(self.loader)
        semantic_encoded = json.dumps(
            self.semantic_key(snapshot), ensure_ascii=False, sort_keys=True, default=str,
        )
        async with self.lock:
            changed = semantic_encoded != self.last_semantic_encoded
            if changed:
                for subscriber in tuple(self.subscribers):
                    if subscriber.full():
                        try:
                            subscriber.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    subscriber.put_nowait(snapshot)
            self.subscribers.add(queue)
            self.last = snapshot
            self.last_semantic_encoded = semantic_encoded
            if revision is not None:
                self.last_revision_encoded = json.dumps(revision, sort_keys=True, default=str)
            if self.task is None or self.task.done():
                self.task = asyncio.create_task(self._run())
            queue.put_nowait(snapshot)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self.lock:
            self.subscribers.discard(queue)

    async def _run(self) -> None:
        try:
            while self.subscribers:
                if self.revision_loader is not None:
                    revision = await asyncio.to_thread(self.revision_loader)
                    revision_encoded = json.dumps(revision, sort_keys=True, default=str)
                    if revision_encoded == self.last_revision_encoded:
                        await asyncio.sleep(self.poll_seconds)
                        continue
                    self.last_revision_encoded = revision_encoded
                snapshot = await asyncio.to_thread(self.loader)
                semantic_encoded = json.dumps(
                    self.semantic_key(snapshot), ensure_ascii=False, sort_keys=True, default=str,
                )
                if semantic_encoded != self.last_semantic_encoded:
                    self.last = snapshot
                    self.last_semantic_encoded = semantic_encoded
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
