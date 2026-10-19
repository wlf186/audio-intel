"""On-demand, bounded job-owned disk accounting, separate from job summaries."""
from __future__ import annotations

import os
import stat
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from . import db

_lock = threading.Lock()
_cache: OrderedDict[tuple, tuple[float, dict[str, Any]]] = OrderedDict()
_slots = threading.BoundedSemaphore(2)


class StorageBusy(Exception):
    pass


def measure(paths: list[Path]) -> tuple[int, str]:
    total = 0
    allocated = True
    stack = list(paths)
    while stack:
        path = stack.pop()
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue  # Active jobs can remove temporary files while measured.
        reparse = bool(getattr(info, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(info.st_mode) or reparse:
            continue
        blocks = getattr(info, "st_blocks", None)
        total += blocks * 512 if blocks is not None else info.st_size
        allocated = allocated and blocks is not None
        if stat.S_ISDIR(info.st_mode):
            with os.scandir(path) as entries:
                stack.extend(Path(entry.path) for entry in entries)
    return total, "allocated" if allocated else "logical"


def retrieve(job_ids: list[str], jobs_root: Path, temp_root: Path, *, refresh: bool = False) -> dict:
    # Only metadata: never deserialize request_json/result_json.
    with db.connect() as connection:
        rows = {r["id"]: dict(r) for r in connection.execute(
            f"SELECT id,state,updated_at FROM jobs WHERE id IN ({','.join('?' for _ in job_ids)})", job_ids,
        )}
    items = []
    for job_id in job_ids:
        row = rows.get(job_id)
        if row is None:
            items.append({"id": job_id, "status": "not_found", "bytes": None, "checked_at": db.utcnow(), "basis": None})
            continue
        key = (str(jobs_root.resolve()), str(temp_root.resolve()), job_id, row['state'], row['updated_at'])
        now = time.monotonic()
        with _lock:
            cached = _cache.get(key)
            if not refresh and cached and cached[0] > now:
                _cache.move_to_end(key)
                items.append(dict(cached[1]))
                continue
        if not _slots.acquire(blocking=False):
            raise StorageBusy()
        try:
            paths = [root.resolve() / job_id for root in (jobs_root, temp_root)]
            # Job IDs from older/custom databases must still be one safe component.
            if any(path.parent != root.resolve() for path, root in zip(paths, (jobs_root, temp_root))):
                raise ValueError("Invalid owned path")
            count, basis = measure(paths)
            item = {"id": job_id, "status": "ready", "bytes": count, "checked_at": db.utcnow(), "basis": basis}
        except (OSError, ValueError):
            item = {"id": job_id, "status": "error", "bytes": None, "checked_at": db.utcnow(), "basis": None}
        finally:
            _slots.release()
        with _lock:
            _cache[key] = (time.monotonic() + (5 if row['state'] in {'queued', 'running'} else 30), item)
            _cache.move_to_end(key)
            while len(_cache) > 512:
                _cache.popitem(last=False)
        items.append(dict(item))
    return {"items": items}
