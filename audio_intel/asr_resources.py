"""Host-resource limits for ASR. No samples or telemetry enter job summaries."""
from __future__ import annotations

from contextvars import ContextVar
import json
from pathlib import Path
import shutil
import time
import threading
import os
import stat
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
        self.audio_stores = []
        self.lock = threading.RLock()

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
                            audio_cache_bytes=min(config.asr_audio_cache_max_bytes, memory // 4) // 262144 * 262144,
                            disk_reserve=config.min_free_disk_bytes, work=str(work), output=str(output)))
        except AsrResourceLimitError:
            raise
        except Exception as exc:
            raise AsrResourceLimitError('Cannot inspect ASR resource capacity') from exc

    def fail(self, resource: str, required: int, limit: int) -> None:
        raise AsrResourceLimitError(f'ASR {self.stage}: {resource} requires {required} bytes; '
                                    f'budget/available {max(0, limit)} bytes / 资源预算不足，输入已保留')

    def check(self, *, memory: int = 0, work: int = 0, force: bool = False,
              reclaim: bool = True, disk_only: bool = False) -> None:
        with self.lock:
            self._check(memory=memory, work=work, force=force, reclaim=reclaim, disk_only=disk_only)

    def _check(self, *, memory: int, work: int, force: bool, reclaim: bool, disk_only: bool) -> None:
        if not force and not memory and not work and time.monotonic() - self.last_check < .5:
            return
        try:
            refresh = force or not hasattr(self, '_snapshot') or time.monotonic() - self.last_check >= .5
            if refresh:
                rss = tree_rss(self.policy['pid'], self.policy['created'])
                self.peak_rss = max(self.peak_rss, rss)
                used = 0
                for key in ('work', 'output'):
                    pending = [Path(self.policy[key])]
                    while pending:
                        directory = pending.pop()
                        try:
                            with os.scandir(directory) as entries:
                                for entry in entries:
                                    try:
                                        info = entry.stat(follow_symlinks=False)
                                    except FileNotFoundError:
                                        continue
                                    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                                        continue
                                    if stat.S_ISDIR(info.st_mode):
                                        pending.append(Path(entry.path))
                                    elif stat.S_ISREG(info.st_mode):
                                        used += info.st_size
                        except FileNotFoundError:
                            continue
                self.peak_work = max(self.peak_work, used)
                _, available = memory_capacity()
                from .video_uploads import remaining_bytes
                free = min(shutil.disk_usage(Path(self.policy[k]) if Path(self.policy[k]).exists() else Path(self.policy[k]).parent).free
                           for k in ("work", "output")) - remaining_bytes()
                self._snapshot = rss, used, available, free
                self._cache_snapshot = sum(x.memory_bytes for x in self.audio_stores)
                self._write_snapshot = sum(x.stats['spilled_bytes'] + x.stats['normalized_written_bytes'] for x in self.audio_stores)
                self.last_check = time.monotonic()
            else:
                rss, used, available, free = self._snapshot
                growth = max(0, sum(x.memory_bytes for x in self.audio_stores) - self._cache_snapshot)
                writes = max(0, sum(x.stats['spilled_bytes'] + x.stats['normalized_written_bytes'] for x in self.audio_stores) - self._write_snapshot)
                if memory >= 64 * 1024**2:
                    rss = tree_rss(self.policy['pid'], self.policy['created'])
                    _, available = memory_capacity()
                    self.peak_rss = max(self.peak_rss, rss)
                    self._cache_snapshot = sum(x.memory_bytes for x in self.audio_stores)
                    self._snapshot = rss, self._snapshot[1], available, self._snapshot[3]
                else:
                    rss += growth
                    available -= growth
                used += writes
                free -= writes
            if reclaim and not disk_only and any(x.memory_bytes for x in self.audio_stores):
                margin = min(512 * 1024**2, self.policy['memory_bytes'] // 10)
                needed = max(0, rss + memory - self.policy['baseline_rss'] - self.policy['memory_bytes'] + margin,
                             memory - available + self.policy['memory_reserve'] + margin)
                if needed:
                    freed = 0
                    for store in self.audio_stores:
                        try:
                            freed += store.reclaim(needed - freed)
                        except AsrResourceLimitError:
                            break  # Soft reclamation is optional; enforce the hard limits below.
                        if freed >= needed:
                            break
                    if freed:
                        self._check(memory=memory, work=work, force=True, reclaim=False, disk_only=False)
                        return
            checks = [('memory growth', rss + memory - self.policy['baseline_rss'], self.policy['memory_bytes']),
                      ('system memory', memory, available - self.policy['memory_reserve']),
                      ('workspace', used + work, self.policy['work_bytes']),
                      ('disk headroom', work, free - self.policy['disk_reserve'])]
            for name, required, limit in checks:
                if disk_only and name in {'memory growth', 'system memory'}:
                    continue
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
            if self.audio_stores:
                value['audio_cache'] = [dict(x.stats) for x in self.audio_stores]
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
