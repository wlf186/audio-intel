from __future__ import annotations

import time
from typing import Any, Callable


MODEL_PROGRESS_INTERVAL_SECONDS = 0.5


class ThrottledProgress:
    """Limit durable progress writes while always allowing boundary flushes."""

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], None],
        interval_seconds: float = MODEL_PROGRESS_INTERVAL_SECONDS,
    ) -> None:
        self.callback = callback
        self.interval_seconds = max(0.0, float(interval_seconds))
        self.last_emitted_at = float("-inf")
        self.last_current: int | None = None

    def report(self, payload: dict[str, Any], *, force: bool = False) -> bool:
        current = int(payload.get("current", 0))
        now = time.monotonic()
        if not force and (
            current == self.last_current
            or now - self.last_emitted_at < self.interval_seconds
        ):
            return False
        self.callback(payload)
        self.last_current = current
        self.last_emitted_at = now
        return True
