from __future__ import annotations

import fcntl
import subprocess
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from .config import settings


COMPUTE_DEVICES = {"cpu", "gpu"}


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
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                now = time.monotonic()
                if on_wait is not None and now >= next_notice:
                    on_wait()
                    next_notice = now + 2.0
                time.sleep(poll_seconds)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
