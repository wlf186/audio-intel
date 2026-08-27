from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable


MODEL_PROGRESS_INTERVAL_SECONDS = 0.5
PROGRESS_SNAPSHOT_SEQUENCE_WIDTH = 12


def progress_snapshot_path(base_path: Path, sequence: int) -> Path:
    """Return a unique immutable snapshot path for one progress update."""
    return base_path.with_name(
        f"{base_path.stem}-{int(sequence):0{PROGRESS_SNAPSHOT_SEQUENCE_WIDTH}d}{base_path.suffix}"
    )


def progress_snapshot_paths(base_path: Path) -> list[tuple[int, Path]]:
    """List fully published progress snapshots in sequence order."""
    prefix = f"{base_path.stem}-"
    snapshots: list[tuple[int, Path]] = []
    try:
        candidates = list(base_path.parent.iterdir())
    except OSError:
        return snapshots
    for candidate in candidates:
        name = candidate.name
        if not name.startswith(prefix) or not name.endswith(base_path.suffix):
            continue
        sequence_text = (
            name[len(prefix):-len(base_path.suffix)]
            if base_path.suffix else name[len(prefix):]
        )
        if (
            len(sequence_text) != PROGRESS_SNAPSHOT_SEQUENCE_WIDTH
            or not sequence_text.isdigit()
        ):
            continue
        snapshots.append((int(sequence_text), candidate))
    return sorted(snapshots)


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
