from __future__ import annotations

import errno
import os
import subprocess
import time
from contextlib import contextmanager
from typing import BinaryIO, Callable, Iterator

try:  # POSIX advisory file locks
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows CI
    _fcntl = None

try:  # Windows byte-range file locks
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on Linux CI
    _msvcrt = None

from .config import settings


COMPUTE_DEVICES = {"cpu", "gpu"}


def _try_lock(handle: BinaryIO) -> bool:
    if os.name == "nt":
        if _msvcrt is None:  # pragma: no cover - defensive platform guard
            raise RuntimeError("Windows locking support is unavailable")
        # Reading a byte locked by another Windows process raises EACCES, so
        # initialize new lock files using metadata before attempting the lock.
        if os.fstat(handle.fileno()).st_size == 0:
            handle.seek(0)
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
    if _fcntl is None:  # pragma: no cover - defensive platform guard
        raise RuntimeError("POSIX locking support is unavailable")
    try:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock(handle: BinaryIO) -> None:
    if os.name == "nt":
        if _msvcrt is None:  # pragma: no cover - defensive platform guard
            raise RuntimeError("Windows locking support is unavailable")
        handle.seek(0)
        _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
        return
    if _fcntl is None:  # pragma: no cover - defensive platform guard
        raise RuntimeError("POSIX locking support is unavailable")
    _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)


def gpu_snapshot(index: int = 0) -> dict[str, int | str] | None:
    try:
        output = subprocess.run(
            [
                "nvidia-smi",
                f"--id={index}",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        ).stdout.strip()
        fields = [field.strip() for field in output.splitlines()[0].split(",")]
        return {
            "name": fields[0],
            "memory_used_mib": int(fields[1]),
            "memory_total_mib": int(fields[2]),
            "utilization": int(fields[3]),
        }
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def compute_device_name(compute_device: str, fallback: str | None = None) -> str:
    if compute_device == "cpu":
        return "CPU"
    snapshot = gpu_snapshot(0)
    return str(snapshot["name"]) if snapshot else (fallback or "GPU")


@contextmanager
def gpu_lease(on_wait: Callable[[], None] | None = None, poll_seconds: float = 0.5) -> Iterator[None]:
    """Serialize large GPU models across independent ASR and TTS workers."""
    settings.run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.run_dir / "gpu.lock"
    with lock_path.open("a+b") as handle:
        next_notice = 0.0
        while True:
            if _try_lock(handle):
                break
            now = time.monotonic()
            if on_wait is not None and now >= next_notice:
                on_wait()
                next_notice = now + 2.0
            time.sleep(poll_seconds)
        try:
            yield
        finally:
            _unlock(handle)
