"""Host-resource limits for ASR. No samples or telemetry enter job summaries."""
from __future__ import annotations

from contextvars import ContextVar
import json
from pathlib import Path
import shutil
import time
from typing import Any

import psutil

GIB = 1024**3
current_budget: ContextVar[Budget | None] = ContextVar('asr_budget', default=None)


class AsrResourceLimitError(RuntimeError):
    """A deliberate resource failure; never an optional enhancement fallback."""


def memory_capacity() -> tuple[int, int]:
    memory = psutil.virtual_memory()
    total, available = memory.total, memory.available
    # cgroup v2 paths use the same namespace-aware resolution as exit evidence.
    from .executor_diagnostics import cgroup_memory_directory
    folder = cgroup_memory_directory()
    if folder is not None:
        try:
            limit = (folder / 'memory.max').read_text(encoding='utf-8').strip()
            if limit != 'max':
                total = min(total, int(limit))
                available = min(available, max(0, int(limit) - int((folder / 'memory.current').read_text(encoding='utf-8'))))
        except (OSError, ValueError):
            pass
    return total, available


def tree_rss(pid: int, created: float) -> int:
    process = psutil.Process(pid)
    if abs(process.create_time() - created) > .01:
        raise AsrResourceLimitError('ASR executor identity changed')
    result = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            result += child.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return result


class Budget:
    def __init__(self, policy: dict[str, Any]):
        self.policy = policy
        self.last_check = 0.
        self.peak_rss = policy['baseline_rss']
        self.peak_work = 0
        self.stage = 'starting'

    @classmethod
    def start(cls, config: Any, pid: int, work: Path, output: Path) -> Budget:
        try:
            from .video_uploads import remaining_bytes
            total, available = memory_capacity()
            reserve = max(GIB, total // 10)
            process = psutil.Process(pid)
            baseline = tree_rss(pid, process.create_time())
            free = min(shutil.disk_usage(config.temp_dir).free, shutil.disk_usage(output).free) - remaining_bytes()
            memory = config.asr_memory_budget_bytes or max(0, (available - reserve) // 2)
            disk = config.asr_work_budget_bytes or max(0, (free - config.min_free_disk_bytes) // 2)
            if memory <= 0 or disk <= 0:
                raise AsrResourceLimitError('Insufficient ASR resource headroom / ASR 资源余量不足')
            return cls(dict(version=1, pid=pid, created=process.create_time(), baseline_rss=baseline,
                            memory_bytes=memory, work_bytes=disk, memory_reserve=reserve,
                            disk_reserve=config.min_free_disk_bytes, work=str(work), output=str(output)))
        except AsrResourceLimitError:
            raise
        except Exception as exc:
            raise AsrResourceLimitError('Cannot inspect ASR resource capacity') from exc

    def fail(self, resource: str, required: int, limit: int) -> None:
        raise AsrResourceLimitError(f'ASR {self.stage}: {resource} requires {required} bytes; '
                                    f'budget/available {max(0, limit)} bytes / 资源预算不足，输入已保留')

    def check(self, *, memory: int = 0, work: int = 0, force: bool = False) -> None:
        if not force and not memory and not work and time.monotonic() - self.last_check < .5:
            return
        try:
            refresh = force or not hasattr(self, '_snapshot') or time.monotonic() - self.last_check >= .5 or memory >= 64 * 1024**2
            if refresh:
                rss = tree_rss(self.policy['pid'], self.policy['created'])
                self.peak_rss = max(self.peak_rss, rss)
                used = 0
                for key in ('work', 'output'):
                    for path in Path(self.policy[key]).rglob('*'):
                        try:
                            if path.is_file() and not path.is_symlink():
                                used += path.stat().st_size
                        except FileNotFoundError:
                            pass
                self.peak_work = max(self.peak_work, used)
                _, available = memory_capacity()
                from .video_uploads import remaining_bytes
                free = min(shutil.disk_usage(Path(self.policy[k]) if Path(self.policy[k]).exists() else Path(self.policy[k]).parent).free
                           for k in ("work", "output")) - remaining_bytes()
                self._snapshot = rss, used, available, free
                self.last_check = time.monotonic()
            else:
                rss, used, available, free = self._snapshot
            checks = [('memory growth', rss + memory - self.policy['baseline_rss'], self.policy['memory_bytes']),
                      ('system memory', memory, available - self.policy['memory_reserve']),
                      ('workspace', used + work, self.policy['work_bytes']),
                      ('disk headroom', work, free - self.policy['disk_reserve'])]
            for name, required, limit in checks:
                if required > limit:
                    self.fail(name, required, limit)
        except AsrResourceLimitError:
            raise
        except psutil.NoSuchProcess:
            return  # Supervisor reconciles an exited executor via the normal exit path.
        except Exception as exc:
            raise AsrResourceLimitError('Cannot inspect ASR resource usage') from exc

    def record(self, directory: Path, attempt: int, error: str | None = None) -> None:
        from .utils import atomic_json
        try:
            value = dict(policy=self.policy, observed_peak_tree_rss_bytes=self.peak_rss,
                         observed_peak_work_bytes=self.peak_work, stage=self.stage, error=error,
                         note='RSS sums shared pages conservatively; sampling is not an OS hard limit.')
            assert len(json.dumps(value).encode()) <= 16384
            folder = directory / 'diagnostics'
            folder.mkdir(parents=True, exist_ok=True)
            atomic_json(folder / f'attempt-{attempt}-resource-budget.json', value)
        except Exception:
            pass


def check_resources(*, memory: int = 0, work: int = 0, stage: str | None = None) -> None:
    budget = current_budget.get()
    if budget:
        if stage:
            budget.stage = stage
        budget.check(memory=memory, work=work)


def clustering_memory(windows: int) -> int:
    # Pinned FunASR: spectral below 2048, UMAP/HDBSCAN (min cluster size 10) above.
    return windows ** 2 * 64 if windows < 2048 else windows * 16384 + (windows // 10 + 1) ** 2 * 64
