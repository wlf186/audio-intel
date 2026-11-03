"""Best-effort crash evidence. Samples never enter job summaries or SSE."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import signal
from typing import Any

import psutil

from .utils import atomic_json

logger = logging.getLogger(__name__)


def cgroup_memory_directory() -> Path | None:
    """Resolve this process's cgroup v2 within its mount namespace."""
    if os.name == "nt":
        return None
    try:
        group = next(line[3:] for line in Path('/proc/self/cgroup').read_text(encoding='utf-8').splitlines()
                     if line.startswith('0::'))
        for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
            left, right = line.split(' - ', 1)
            if right.split()[0] != 'cgroup2':
                continue
            fields = left.split()
            # mountinfo escapes spaces/backslashes as octal sequences.
            def unescape(value: str) -> str:
                import re
                return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)
            relative = Path(group).relative_to(unescape(fields[3]))
            return Path(unescape(fields[4])) / relative
    except Exception:
        pass
    return None


def oom_kills() -> int | None:
    """Read this process's shared cgroup OOM counter when available."""
    try:
        folder = cgroup_memory_directory()
        if folder is not None:
            path = folder / 'memory.events'
            with path.open(encoding='utf-8') as stream:
                counters = dict(row.split() for row in stream.read(4096).splitlines())
            return int(counters['oom_kill'])
    except Exception:
        pass
    return None


class ExecutorDiagnostics:
    def __init__(self, pid: int):
        self.pid = pid
        self.created_at: float | None = None
        self.oom_before = oom_kills()
        self.last_rss: int | None = None
        self.peak_rss: int | None = None
        self.available_bytes: int | None = None
        self.sample()

    def sample(self) -> None:
        try:
            process = psutil.Process(self.pid)
            created = process.create_time()
            if self.created_at is not None and created != self.created_at:
                return
            self.created_at = created
            self.last_rss = process.memory_info().rss
            self.peak_rss = max(self.peak_rss or 0, self.last_rss)
            self.available_bytes = psutil.virtual_memory().available
        except Exception:
            pass

    def record(self, job: dict[str, Any], exit_code: int | None, directory: Path,
               timestamp: str, *, platform: str | None = None) -> str:
        platform = platform or os.name
        signal_name = None
        if platform != 'nt' and exit_code is not None and exit_code < 0:
            try:
                signal_name = signal.Signals(-exit_code).name
            except ValueError:
                pass
        after = oom_kills()
        delta = after - self.oom_before if after is not None and self.oom_before is not None and after >= self.oom_before else None
        suspected = signal_name == 'SIGKILL' and delta is not None and delta > 0
        message = f"{job['kind'].upper()} execution process exited unexpectedly (exit_code={exit_code}"
        if platform == 'nt' and exit_code is not None:
            message += f", status=0x{exit_code & 0xffffffff:08X}"
        if signal_name:
            message += f", signal={signal_name}"
        if suspected:
            message += "; possible system OOM / 疑似系统内存不足，需内核日志确认"
        message += ')'
        evidence = {
            'version': 1, 'job_id': job['id'], 'attempt': job['attempts'],
            'timestamp': timestamp, 'worker_id': job.get('worker_id'),
            'executor_pid': self.pid, 'executor_created_at': self.created_at,
            'exit_code': exit_code, 'platform': platform, 'signal': signal_name,
            'windows_status': f'0x{exit_code & 0xffffffff:08X}' if platform == 'nt' and exit_code is not None else None,
            'last_stage': str(job.get('stage', ''))[:200], 'progress': job.get('progress'),
            'last_rss_bytes': self.last_rss, 'observed_peak_rss_bytes': self.peak_rss,
            'last_system_available_bytes': self.available_bytes,
            'oom_kills_before': self.oom_before, 'oom_kills_after': after,
            'oom_kills_delta': delta, 'possible_oom': suspected,
            'oom_note': 'Cgroup evidence is shared and cannot establish which PID was killed.',
        }
        try:
            # One small record per attempt, outside the disposable work directory.
            assert len(json.dumps(evidence, ensure_ascii=False).encode('utf-8')) <= 16_384
            folder = directory / 'diagnostics'
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(folder / f"attempt-{job['attempts']}-executor-exit.json", evidence)
            (directory / 'error.log').write_text(message + '\n' + json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            logger.warning("Unable to persist executor diagnostics for job %s", job['id'], exc_info=True)
        return message
