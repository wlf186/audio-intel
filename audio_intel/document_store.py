"""Durable document imports and per-job checkpoints (schema v11)."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_import_lock = threading.RLock()


@contextmanager
def import_files(*, deleting: bool = False):
    """Serialize deletion with the short source-snapshot copy, across API threads."""
    acquired = _import_lock.acquire(blocking=not deleting)
    if not acquired:
        from fastapi import HTTPException
        raise HTTPException(409, "Document import is being snapshotted")
    try:
        yield
    finally:
        _import_lock.release()


def active_count() -> int:
    from .db import connect
    with connect() as db:
        return db.execute("SELECT COUNT(*) FROM document_imports WHERE state IN ('queued','running')").fetchone()[0]


def migrate(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS document_imports (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, suffix TEXT NOT NULL,
            state TEXT NOT NULL, key_hash TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
            process_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS document_sections (
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            id TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL,
            start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', retries INTEGER NOT NULL DEFAULT 0,
            artifact_json TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY(job_id,id), UNIQUE(job_id,position)
        );
        CREATE INDEX IF NOT EXISTS document_import_state ON document_imports(state,created_at);
        UPDATE schema_meta SET version=11 WHERE version<11;
    """)


def _decode(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    value = dict(row)
    for key in ("metadata_json", "artifact_json", "process_json"):
        if key in value:
            value[key.removesuffix("_json")] = json.loads(value.pop(key) or "null")
    return value


def imports(offset: int = 0, limit: int | None = None) -> list[dict[str, Any]]:
    from .db import connect
    with connect() as db:
        return [_decode(r) for r in db.execute("SELECT * FROM document_imports ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (-1 if limit is None else limit, offset))]  # type: ignore[misc]


def pending_import() -> dict[str, Any] | None:
    from .db import connect
    with connect() as db:
        return _decode(db.execute("SELECT * FROM document_imports WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone())


def get_import(identifier: str) -> dict[str, Any] | None:
    from .db import connect
    with connect() as db:
        return _decode(db.execute("SELECT * FROM document_imports WHERE id=?", (identifier,)).fetchone())


def update_import(identifier: str, **values: Any) -> None:
    from .db import connect, utcnow
    changes = {k: v for k, v in values.items() if k in {"state", "error", "metadata_json", "process_json"}}
    changes["updated_at"] = utcnow()
    with connect() as db:
        db.execute("UPDATE document_imports SET " + ",".join(f"{k}=?" for k in changes) + " WHERE id=?", (*changes.values(), identifier))


def sections(job_id: str) -> list[dict[str, Any]]:
    from .db import connect
    with connect() as db:
        return [_decode(r) for r in db.execute("SELECT * FROM document_sections WHERE job_id=? ORDER BY position", (job_id,))]  # type: ignore[misc]


def ensure_sections(job_id: str, items: list[dict[str, Any]]) -> None:
    from .db import connect, utcnow
    with connect() as db:
        db.executemany("""INSERT OR IGNORE INTO document_sections
            (job_id,id,position,title,start_offset,end_offset,updated_at) VALUES(?,?,?,?,?,?,?)""",
            [(job_id, p["id"], i, p["title"], p["start"], p["end"], utcnow()) for i, p in enumerate(items, 1)])


def checkpoint(job_id: str, section_id: str, state: str, artifact: dict[str, Any] | None = None, retries: int | None = None) -> None:
    from .db import connect, utcnow
    with connect() as db:
        db.execute("""UPDATE document_sections SET state=?,artifact_json=?,
            retries=COALESCE(?,retries),updated_at=? WHERE job_id=? AND id=?""",
            (state, json.dumps(artifact) if artifact else None, retries, utcnow(), job_id, section_id))


def clean_partials(job_id: str, root: Path) -> None:
    output = root / job_id / "output"
    if output.is_dir():
        for path in output.glob("document-*.partial"):
            path.unlink(missing_ok=True)


def main() -> None:
    """Parser child; writes only to its caller-owned import directory."""
    import argparse
    from .document_text import extract
    from .utils import atomic_json
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--max-chars", type=int, required=True)
    parser.add_argument("--archive-limit", type=int, required=True)
    args = parser.parse_args()
    result = extract(args.source, args.max_chars, args.archive_limit)
    atomic_json(args.source.parent / "parsed.json", result)


if __name__ == "__main__":
    main()
