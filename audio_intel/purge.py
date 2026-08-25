from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from .config import settings
from .db import (
    compact_database,
    delete_job_record,
    delete_voiceprint_sample_record,
    get_voiceprint_sample,
    prepare_job_for_purge,
)


def _allocated_bytes_for_entry(path: Path) -> int:
    """Return allocated bytes where available, falling back to logical size."""
    stat = path.lstat()
    blocks = getattr(stat, "st_blocks", None)
    return blocks * 512 if blocks is not None else stat.st_size


def allocated_bytes(path: Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    total = _allocated_bytes_for_entry(path)
    if not path.is_dir() or path.is_symlink():
        return total
    for directory, directories, files in os.walk(path, followlinks=False):
        root = Path(directory)
        for name in directories:
            total += _allocated_bytes_for_entry(root / name)
        for name in files:
            total += _allocated_bytes_for_entry(root / name)
    return total


def database_allocated_bytes() -> int:
    database = settings.database_path
    return sum(
        allocated_bytes(Path(str(database) + suffix))
        for suffix in ("", "-wal", "-shm")
    )


def _owned_job_path(root: Path, job_id: str) -> Path:
    root = root.resolve()
    path = root / job_id
    if path.parent.resolve() != root:
        raise ValueError("Invalid job storage path")
    return path


def _remove_and_verify(path: Path) -> None:
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    if path.exists() or path.is_symlink():
        raise OSError(f"Task storage remains after deletion: {path}")


def purge_jobs(job_ids: list[str], compact: bool = True) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(job_ids))
    deleted: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    database_before = database_allocated_bytes()

    for job_id in unique_ids:
        try:
            job = prepare_job_for_purge(job_id)
            if job is None:
                failed.append({"id": job_id, "code": "not_found", "message": "任务不存在"})
                continue
            if job["state"] == "running":
                failed.append({"id": job_id, "code": "running", "message": "运行中的任务不能删除，请先取消并等待任务结束"})
                continue
            paths = [
                _owned_job_path(settings.jobs_dir, job_id),
                _owned_job_path(settings.temp_dir, job_id),
            ]
            reclaimed = sum(allocated_bytes(path) for path in paths)
            for path in paths:
                _remove_and_verify(path)
            request = job.get("request") or {}
            if request.get("purpose") == "voiceprint_import":
                sample = get_voiceprint_sample(request.get("voiceprint_sample_id", ""))
                if sample is not None and sample.get("state") != "ready":
                    sample_path = Path(sample["audio_path"]).resolve() if sample.get("audio_path") else None
                    if sample_path and settings.voiceprints_dir.resolve() in sample_path.parents:
                        _remove_and_verify(sample_path)
                    delete_voiceprint_sample_record(sample["id"])
            if not delete_job_record(job_id):
                raise RuntimeError("任务文件已删除，但数据库记录未能清理；请重试删除")
            deleted.append({"id": job_id, "reclaimed_bytes": reclaimed})
        except Exception as exc:
            failed.append({"id": job_id, "code": "purge_failed", "message": str(exc)[:500]})

    database_compacted = True
    maintenance_error: str | None = None
    if deleted and compact:
        try:
            compact_database()
        except Exception as exc:
            database_compacted = False
            maintenance_error = str(exc)[:500]
    database_after = database_allocated_bytes()
    database_reclaimed = max(0, database_before - database_after)
    return {
        "requested_count": len(unique_ids),
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "reclaimed_bytes": sum(item["reclaimed_bytes"] for item in deleted) + database_reclaimed,
        "database_reclaimed_bytes": database_reclaimed,
        "database_compacted": database_compacted,
        "maintenance_error": maintenance_error,
        "deleted": deleted,
        "failed": failed,
    }
