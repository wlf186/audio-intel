from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings


JOB_STATES = {"queued", "running", "succeeded", "failed", "cancelled"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings.ensure_directories()
    connection = sqlite3.connect(settings.database_path, timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA secure_delete=ON")
    try:
        yield connection
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK(kind IN ('asr','tts')),
                state TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'queued',
                display_name TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error_code TEXT,
                error_message TEXT,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                worker_id TEXT,
                heartbeat_at TEXT,
                processing_seconds REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(kind, state, created_at);
            CREATE TABLE IF NOT EXISTS voices (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                language TEXT NOT NULL,
                ref_audio_path TEXT NOT NULL,
                ref_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workers (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                pid INTEGER NOT NULL,
                state TEXT NOT NULL,
                current_job_id TEXT,
                details_json TEXT NOT NULL DEFAULT '{}',
                heartbeat_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL);
            INSERT INTO schema_meta(version)
              SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_meta);
            """
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
        if "processing_seconds" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN processing_seconds REAL NOT NULL DEFAULT 0")
            db.execute(
                """UPDATE jobs SET processing_seconds=MAX(0,
                   (julianday(finished_at)-julianday(started_at))*86400)
                   WHERE started_at IS NOT NULL AND finished_at IS NOT NULL"""
            )
        db.execute("UPDATE schema_meta SET version=2 WHERE version<2")
        version = db.execute("SELECT MIN(version) FROM schema_meta").fetchone()[0]
        if version < 3:
            from .gpu import gpu_snapshot

            snapshot = gpu_snapshot(0)
            if snapshot is not None:
                for row in db.execute("SELECT id,kind,request_json FROM jobs").fetchall():
                    request = json.loads(row["request_json"])
                    device = request.get("compute_device") or ("gpu" if row["kind"] == "asr" else "cpu")
                    changed = request.get("compute_device") != device
                    if changed:
                        request["compute_device"] = device
                    if device == "gpu" and not request.get("compute_device_name"):
                        request["compute_device_name"] = str(snapshot["name"])
                        changed = True
                    elif device == "cpu" and not request.get("compute_device_name"):
                        request["compute_device_name"] = "CPU"
                        changed = True
                    if changed:
                        db.execute(
                            "UPDATE jobs SET request_json=?,updated_at=? WHERE id=?",
                            (json.dumps(request, ensure_ascii=False), utcnow(), row["id"]),
                        )
                db.execute("UPDATE schema_meta SET version=3 WHERE version<3")


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in ("request_json", "result_json", "details_json"):
        if field in item:
            raw = item.pop(field)
            item[field.removesuffix("_json")] = json.loads(raw) if raw else None
    if "cancel_requested" in item:
        item["cancel_requested"] = bool(item["cancel_requested"])
    return item


def create_job(kind: str, display_name: str, request: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    if kind not in {"asr", "tts"}:
        raise ValueError("Unsupported job kind")
    job_id = job_id or uuid.uuid4().hex
    now = utcnow()
    with connect() as db:
        db.execute(
            "INSERT INTO jobs(id,kind,display_name,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (job_id, kind, display_name, json.dumps(request, ensure_ascii=False), now, now),
        )
    return get_job(job_id)  # type: ignore[return-value]


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as db:
        return _decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def list_jobs(kind: str | None = None, state: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if state:
        where.append("state=?")
        params.append(state)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM jobs {clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, min(max(limit, 1), 500), max(offset, 0)),
        ).fetchall()
    return [_decode(row) for row in rows]  # type: ignore[misc]


def update_job(job_id: str, **values: Any) -> dict[str, Any] | None:
    allowed = {
        "state", "progress", "stage", "result_json", "error_code", "error_message",
        "cancel_requested", "worker_id", "heartbeat_at", "started_at", "finished_at", "attempts",
    }
    changes: dict[str, Any] = {key: value for key, value in values.items() if key in allowed}
    if "state" in changes and changes["state"] not in JOB_STATES:
        raise ValueError("Invalid job state")
    if "result_json" in changes and not isinstance(changes["result_json"], str):
        changes["result_json"] = json.dumps(changes["result_json"], ensure_ascii=False)
    changes["updated_at"] = utcnow()
    assignment = ",".join(f"{key}=?" for key in changes)
    with connect() as db:
        db.execute(f"UPDATE jobs SET {assignment} WHERE id=?", (*changes.values(), job_id))
    return get_job(job_id)


def finish_job(job_id: str, state: str, **values: Any) -> dict[str, Any] | None:
    if state not in {"succeeded", "failed", "cancelled"}:
        raise ValueError("finish_job requires a terminal state")
    allowed = {
        "progress", "stage", "result_json", "error_code", "error_message",
        "cancel_requested", "worker_id", "heartbeat_at",
    }
    changes: dict[str, Any] = {key: value for key, value in values.items() if key in allowed}
    if "result_json" in changes and not isinstance(changes["result_json"], str):
        changes["result_json"] = json.dumps(changes["result_json"], ensure_ascii=False)
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT started_at FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            db.execute("ROLLBACK")
            return None
        changes.update({"state": state, "finished_at": now, "updated_at": now})
        assignment = ",".join(f"{key}=?" for key in changes)
        db.execute(
            f"""UPDATE jobs SET {assignment}, processing_seconds=processing_seconds+
                CASE WHEN started_at IS NULL THEN 0 ELSE
                MAX(0,(julianday(?) - julianday(started_at))*86400) END WHERE id=?""",
            (*changes.values(), now, job_id),
        )
        db.execute("COMMIT")
    return get_job(job_id)


def claim_job(kind: str, worker_id: str) -> dict[str, Any] | None:
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id FROM jobs WHERE kind=? AND state='queued' ORDER BY created_at LIMIT 1", (kind,)
        ).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        db.execute(
            "UPDATE jobs SET state='running',stage='starting',progress=0.01,worker_id=?,heartbeat_at=?,"
            "started_at=?,finished_at=NULL,attempts=attempts+1,updated_at=? WHERE id=?",
            (worker_id, now, now, now, row["id"]),
        )
        db.execute("COMMIT")
    return get_job(row["id"])


def request_cancel(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if job is None:
        return None
    if job["state"] == "queued":
        return finish_job(job_id, "cancelled", stage="cancelled", progress=job["progress"], cancel_requested=1)
    if job["state"] == "running":
        return update_job(job_id, cancel_requested=1, stage="cancelling")
    return job


def retry_job(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if job is None:
        return None
    if job["state"] not in {"failed", "cancelled"}:
        raise ValueError("Only failed or cancelled jobs can be retried")
    return update_job(
        job_id, state="queued", stage="queued", progress=0, result_json=None,
        error_code=None, error_message=None, cancel_requested=0, worker_id=None,
        heartbeat_at=None, started_at=None, finished_at=None,
    )


def prepare_job_for_purge(job_id: str) -> dict[str, Any] | None:
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        if row["state"] == "running":
            db.execute("COMMIT")
            return _decode(row)
        if row["state"] == "queued":
            db.execute(
                "UPDATE jobs SET state='cancelled',stage='cancelled',cancel_requested=1,finished_at=?,updated_at=? WHERE id=?",
                (now, now, job_id),
            )
        db.execute("COMMIT")
    return get_job(job_id)


def delete_job_record(job_id: str) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM jobs WHERE id=? AND state!='running'", (job_id,))
        return cursor.rowcount == 1


def compact_database() -> None:
    with connect() as db:
        db.execute("VACUUM")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def recover_stale(kind: str) -> int:
    now = utcnow()
    with connect() as db:
        cursor = db.execute(
            """UPDATE jobs SET processing_seconds=processing_seconds+
               CASE WHEN started_at IS NULL THEN 0 ELSE MAX(0,
               (julianday(COALESCE(heartbeat_at,updated_at))-julianday(started_at))*86400) END,
               state='queued',stage='recovered',worker_id=NULL,heartbeat_at=NULL,started_at=NULL,updated_at=? """
            "WHERE kind=? AND state='running'",
            (now, kind),
        )
        return cursor.rowcount


def upsert_worker(worker_id: str, kind: str, pid: int, state: str, current_job_id: str | None = None, details: dict[str, Any] | None = None) -> None:
    now = utcnow()
    with connect() as db:
        db.execute(
            """INSERT INTO workers(id,kind,pid,state,current_job_id,details_json,heartbeat_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET pid=excluded.pid,state=excluded.state,
               current_job_id=excluded.current_job_id,details_json=excluded.details_json,heartbeat_at=excluded.heartbeat_at""",
            (worker_id, kind, pid, state, current_job_id, json.dumps(details or {}), now),
        )


def list_workers() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """SELECT worker.* FROM workers AS worker
               WHERE worker.id = (
                   SELECT latest.id FROM workers AS latest
                   WHERE latest.kind = worker.kind
                   ORDER BY latest.heartbeat_at DESC, latest.id DESC
                   LIMIT 1
               )
               ORDER BY worker.kind"""
        ).fetchall()
    return [_decode(row) for row in rows]  # type: ignore[misc]


def create_voice(name: str, language: str, ref_audio_path: str, ref_text: str) -> dict[str, Any]:
    voice_id = "voice_" + uuid.uuid4().hex[:16]
    now = utcnow()
    with connect() as db:
        db.execute(
            "INSERT INTO voices(id,name,language,ref_audio_path,ref_text,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (voice_id, name, language, ref_audio_path, ref_text, now, now),
        )
    return get_voice(voice_id)  # type: ignore[return-value]


def get_voice(voice_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM voices WHERE id=?", (voice_id,)).fetchone()
    return dict(row) if row else None


def list_voices() -> list[dict[str, Any]]:
    with connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM voices ORDER BY created_at DESC").fetchall()]


def delete_voice_record(voice_id: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM voices WHERE id=?", (voice_id,))
