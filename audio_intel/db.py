from __future__ import annotations

import json
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings
from .hotwords import (
    LEGACY_SYSTEM_HOTWORD_LIST_NAME,
    MAX_HOTWORD_LISTS,
    RESERVED_SYSTEM_HOTWORD_LIST_NAMES,
    SYSTEM_HOTWORD_LIST_ID,
    SYSTEM_HOTWORD_LIST_NAME,
    SYSTEM_SHORT_HOTWORD_LIST_ID,
    SYSTEM_SHORT_HOTWORD_LIST_NAME,
    derive_voiceprint_short_name,
    hotword_name_key,
    normalize_hotword_name,
    normalize_hotword_terms,
)


JOB_STATES = {"queued", "running", "succeeded", "failed", "cancelled"}
VOICEPRINT_SAMPLE_STATES = {"pending", "ready", "failed"}
MAX_VOICEPRINT_NOTE_CHARS = 20
_UNSET = object()
_RESERVED_HOTWORD_NAME_KEYS = frozenset(
    hotword_name_key(name) for name in RESERVED_SYSTEM_HOTWORD_LIST_NAMES
)


class IdempotencyConflict(ValueError):
    pass


class ReadOnlyHotwordListError(ValueError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def normalize_person_name(name: str) -> str:
    clean_name = " ".join(unicodedata.normalize("NFKC", name).strip().split())
    if not clean_name:
        raise ValueError("Voiceprint person name is required")
    return clean_name


def normalize_person_note(note: str | None) -> str | None:
    if note is None:
        return None
    clean_note = " ".join(unicodedata.normalize("NFKC", note).strip().split())
    if not clean_note:
        return None
    if len(clean_note) > MAX_VOICEPRINT_NOTE_CHARS:
        raise ValueError(
            f"Voiceprint person note must not exceed {MAX_VOICEPRINT_NOTE_CHARS} characters"
        )
    return clean_note


def _upsert_system_hotword_list(
    db: sqlite3.Connection,
    item_id: str,
    name: str,
    terms: list[str],
    timestamp: str,
) -> None:
    terms_json = json.dumps(terms, ensure_ascii=False)
    row = db.execute("SELECT * FROM asr_hotword_lists WHERE id=?", (item_id,)).fetchone()
    if row is None:
        db.execute(
            """INSERT INTO asr_hotword_lists(
               id,name,name_key,terms_json,kind,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                item_id, name, hotword_name_key(name), terms_json,
                "system", timestamp, timestamp,
            ),
        )
        return
    if (
        row["name"] != name
        or row["name_key"] != hotword_name_key(name)
        or row["terms_json"] != terms_json
        or row["kind"] != "system"
    ):
        db.execute(
            """UPDATE asr_hotword_lists
               SET name=?,name_key=?,terms_json=?,kind='system',updated_at=? WHERE id=?""",
            (name, hotword_name_key(name), terms_json, timestamp, item_id),
        )


def _sync_voiceprint_hotword_lists(db: sqlite3.Connection, now: str | None = None) -> None:
    timestamp = now or utcnow()
    people = db.execute(
        """SELECT name FROM voiceprint_people
           WHERE include_in_hotword_library=1 ORDER BY name_key,id"""
    ).fetchall()
    full_names = [str(row["name"]) for row in people]
    short_names: list[str] = []
    seen_short_names: set[str] = set()
    for name in full_names:
        short_name = derive_voiceprint_short_name(name)
        if short_name is None:
            continue
        key = short_name.casefold()
        if key not in seen_short_names:
            seen_short_names.add(key)
            short_names.append(short_name)
    _upsert_system_hotword_list(
        db, SYSTEM_HOTWORD_LIST_ID, SYSTEM_HOTWORD_LIST_NAME, full_names, timestamp,
    )
    _upsert_system_hotword_list(
        db, SYSTEM_SHORT_HOTWORD_LIST_ID, SYSTEM_SHORT_HOTWORD_LIST_NAME,
        short_names, timestamp,
    )


def _preserve_reserved_custom_hotword_lists(db: sqlite3.Connection) -> None:
    system_ids = (SYSTEM_HOTWORD_LIST_ID, SYSTEM_SHORT_HOTWORD_LIST_ID)
    for reserved_name in (
        LEGACY_SYSTEM_HOTWORD_LIST_NAME,
        SYSTEM_HOTWORD_LIST_NAME,
        SYSTEM_SHORT_HOTWORD_LIST_NAME,
    ):
        conflict = db.execute(
            """SELECT id FROM asr_hotword_lists
               WHERE name_key=? AND id NOT IN (?,?)""",
            (hotword_name_key(reserved_name), *system_ids),
        ).fetchone()
        if conflict is None:
            continue
        suffix = 1
        while True:
            candidate = (
                f"{reserved_name}（原自定义）" if suffix == 1
                else f"{reserved_name}（原自定义 {suffix}）"
            )
            candidate_key = hotword_name_key(candidate)
            exists = db.execute(
                "SELECT 1 FROM asr_hotword_lists WHERE name_key=?", (candidate_key,)
            ).fetchone()
            if exists is None:
                break
            suffix += 1
        db.execute(
            "UPDATE asr_hotword_lists SET name=?,name_key=?,updated_at=? WHERE id=?",
            (candidate, candidate_key, utcnow(), conflict["id"]),
        )


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
        version = db.execute("SELECT MIN(version) FROM schema_meta").fetchone()[0]
        if version < 4:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS voiceprint_people (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS voiceprint_samples (
                    id TEXT PRIMARY KEY,
                    person_id TEXT NOT NULL REFERENCES voiceprint_people(id) ON DELETE CASCADE,
                    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','ready','failed')),
                    language TEXT NOT NULL DEFAULT 'Auto',
                    audio_path TEXT,
                    transcript TEXT,
                    words_json TEXT,
                    duration REAL,
                    embedding BLOB,
                    embedding_model TEXT,
                    embedding_error TEXT,
                    source_job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
                    source_segment_id INTEGER,
                    source_speaker_id TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_voiceprint_source_segment
                    ON voiceprint_samples(source_job_id, source_segment_id)
                    WHERE source_job_id IS NOT NULL AND source_segment_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_voiceprint_samples_person
                    ON voiceprint_samples(person_id, created_at);
                CREATE TABLE IF NOT EXISTS voiceprint_aliases (
                    alias_id TEXT PRIMARY KEY,
                    person_id TEXT NOT NULL REFERENCES voiceprint_people(id) ON DELETE CASCADE
                );
                """
            )
            for row in db.execute("SELECT * FROM voices ORDER BY created_at,id").fetchall():
                key = person_name_key(row["name"])
                person = db.execute(
                    "SELECT id FROM voiceprint_people WHERE name_key=?", (key,)
                ).fetchone()
                person_id = person["id"] if person else row["id"]
                if person is None:
                    db.execute(
                        "INSERT INTO voiceprint_people(id,name,name_key,created_at,updated_at) VALUES(?,?,?,?,?)",
                        (person_id, row["name"], key, row["created_at"], row["updated_at"]),
                    )
                db.execute(
                    "INSERT OR IGNORE INTO voiceprint_aliases(alias_id,person_id) VALUES(?,?)",
                    (row["id"], person_id),
                )
                db.execute(
                    """INSERT INTO voiceprint_samples(
                       id,person_id,state,language,audio_path,transcript,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        "sample_" + uuid.uuid4().hex[:16], person_id, "ready", row["language"],
                        row["ref_audio_path"], row["ref_text"], row["created_at"], row["updated_at"],
                    ),
                )
            db.execute("UPDATE schema_meta SET version=4 WHERE version<4")
        version = db.execute("SELECT MIN(version) FROM schema_meta").fetchone()[0]
        if version < 5:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            additions = {
                "queue_seq": "INTEGER NOT NULL DEFAULT 0",
                "stage_code": "TEXT",
                "stage_current": "INTEGER",
                "stage_total": "INTEGER",
                "input_duration_seconds": "REAL",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS job_idempotency (
                    operation TEXT NOT NULL,
                    key_hash TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(operation,key_hash)
                );
                CREATE TABLE IF NOT EXISTS job_stage_timings (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    attempt INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    stage_code TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    duration_seconds REAL,
                    PRIMARY KEY(job_id,attempt,sequence)
                );
                CREATE TABLE IF NOT EXISTS queue_sequence (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    value INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_queue_v5
                    ON jobs(kind,state,queue_seq);
                CREATE INDEX IF NOT EXISTS idx_stage_timings_cohort
                    ON job_stage_timings(stage_code,finished_at);
                """
            )
            db.execute("UPDATE jobs SET queue_seq=rowid WHERE queue_seq=0")
            db.execute("UPDATE schema_meta SET version=5 WHERE version<5")
        version = db.execute("SELECT MIN(version) FROM schema_meta").fetchone()[0]
        if version < 6:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
            additions = {
                "progress_basis": "TEXT NOT NULL DEFAULT 'observed'",
                "stage_progress": "REAL",
                "stage_unit": "TEXT",
                "progress_activity_json": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            db.execute("UPDATE schema_meta SET version=6 WHERE version<6")
        version = db.execute("SELECT MIN(version) FROM schema_meta").fetchone()[0]
        if version < 7:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS asr_hotword_lists (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    name_key TEXT NOT NULL UNIQUE,
                    terms_json TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'custom',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_asr_hotword_lists_name
                    ON asr_hotword_lists(name_key,id);
                """
            )
            db.execute("UPDATE schema_meta SET version=7 WHERE version<7")
        version = db.execute("SELECT MIN(version) FROM schema_meta").fetchone()[0]
        if version < 8:
            people_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(voiceprint_people)").fetchall()
            }
            if "note" not in people_columns:
                db.execute("ALTER TABLE voiceprint_people ADD COLUMN note TEXT")
            if "include_in_hotword_library" not in people_columns:
                db.execute(
                    """ALTER TABLE voiceprint_people ADD COLUMN
                       include_in_hotword_library INTEGER NOT NULL DEFAULT 1"""
                )
            hotword_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(asr_hotword_lists)").fetchall()
            }
            if "kind" not in hotword_columns:
                db.execute(
                    "ALTER TABLE asr_hotword_lists ADD COLUMN kind TEXT NOT NULL DEFAULT 'custom'"
                )
            db.execute("UPDATE schema_meta SET version=8 WHERE version<8")
        version = db.execute("SELECT MIN(version) FROM schema_meta").fetchone()[0]
        if version < 9:
            _preserve_reserved_custom_hotword_lists(db)
            _sync_voiceprint_hotword_lists(db)
            db.execute("UPDATE schema_meta SET version=9 WHERE version<9")
        else:
            _sync_voiceprint_hotword_lists(db)
        db.execute(
            """CREATE TABLE IF NOT EXISTS queue_sequence (
               singleton INTEGER PRIMARY KEY CHECK(singleton=1),value INTEGER NOT NULL)"""
        )
        db.execute(
            """INSERT OR IGNORE INTO queue_sequence(singleton,value)
               VALUES(1,(SELECT COALESCE(MAX(queue_seq),0) FROM jobs))"""
        )


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in ("request_json", "result_json", "details_json", "progress_activity_json"):
        if field in item:
            raw = item.pop(field)
            item[field.removesuffix("_json")] = json.loads(raw) if raw else None
    if "cancel_requested" in item:
        item["cancel_requested"] = bool(item["cancel_requested"])
    return item


def person_name_key(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).strip().split()).casefold()


def _decode_hotword_list(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    item["terms"] = json.loads(item.pop("terms_json"))
    return item


def create_hotword_list(name: str, terms: list[Any]) -> dict[str, Any]:
    clean_name = normalize_hotword_name(name)
    if hotword_name_key(clean_name) in _RESERVED_HOTWORD_NAME_KEYS:
        raise sqlite3.IntegrityError("The hotword list name is reserved for a system list")
    clean_terms = normalize_hotword_terms(terms)
    now = utcnow()
    item_id = "hotwords_" + uuid.uuid4().hex[:16]
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        count = int(db.execute(
            "SELECT COUNT(*) FROM asr_hotword_lists WHERE kind='custom'"
        ).fetchone()[0])
        if count >= MAX_HOTWORD_LISTS:
            db.execute("ROLLBACK")
            raise OverflowError(f"No more than {MAX_HOTWORD_LISTS} hotword lists may be created")
        db.execute(
            """INSERT INTO asr_hotword_lists(
               id,name,name_key,terms_json,kind,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                item_id, clean_name, hotword_name_key(clean_name),
                json.dumps(clean_terms, ensure_ascii=False), "custom", now, now,
            ),
        )
        db.execute("COMMIT")
    return get_hotword_list(item_id)  # type: ignore[return-value]


def get_hotword_list(item_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM asr_hotword_lists WHERE id=?", (item_id,)).fetchone()
    return _decode_hotword_list(row)


def list_hotword_lists() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """SELECT * FROM asr_hotword_lists
               ORDER BY CASE kind WHEN 'system' THEN 0 ELSE 1 END,name_key,id"""
        ).fetchall()
    return [_decode_hotword_list(row) for row in rows]  # type: ignore[misc]


def update_hotword_list(
    item_id: str,
    *,
    name: str | None = None,
    terms: list[Any] | None = None,
) -> dict[str, Any] | None:
    current = get_hotword_list(item_id)
    if current is not None and current.get("kind") == "system":
        raise ReadOnlyHotwordListError("System hotword lists cannot be modified")
    changes: dict[str, Any] = {}
    if name is not None:
        clean_name = normalize_hotword_name(name)
        if hotword_name_key(clean_name) in _RESERVED_HOTWORD_NAME_KEYS:
            raise sqlite3.IntegrityError("The hotword list name is reserved for a system list")
        changes.update({"name": clean_name, "name_key": hotword_name_key(clean_name)})
    if terms is not None:
        changes["terms_json"] = json.dumps(normalize_hotword_terms(terms), ensure_ascii=False)
    if not changes:
        raise ValueError("At least one of name or terms must be provided")
    changes["updated_at"] = utcnow()
    assignment = ",".join(f"{key}=?" for key in changes)
    with connect() as db:
        cursor = db.execute(
            f"UPDATE asr_hotword_lists SET {assignment} WHERE id=?",
            (*changes.values(), item_id),
        )
    return get_hotword_list(item_id) if cursor.rowcount else None


def delete_hotword_list(item_id: str) -> bool:
    with connect() as db:
        current = db.execute(
            "SELECT kind FROM asr_hotword_lists WHERE id=?", (item_id,)
        ).fetchone()
        if current is not None and current["kind"] == "system":
            raise ReadOnlyHotwordListError("System hotword lists cannot be deleted")
        cursor = db.execute("DELETE FROM asr_hotword_lists WHERE id=?", (item_id,))
    return cursor.rowcount == 1


def _next_queue_seq(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT value FROM queue_sequence WHERE singleton=1").fetchone()
    if row is None:
        current = int(db.execute("SELECT COALESCE(MAX(queue_seq),0) FROM jobs").fetchone()[0])
        db.execute("INSERT INTO queue_sequence(singleton,value) VALUES(1,?)", (current,))
    else:
        current = int(row["value"])
    next_value = current + 1
    db.execute("UPDATE queue_sequence SET value=? WHERE singleton=1", (next_value,))
    return next_value


def create_job_idempotent(
    kind: str,
    display_name: str,
    request: dict[str, Any],
    job_id: str | None,
    operation: str,
    key_hash: str,
    request_hash: str,
) -> tuple[dict[str, Any], bool]:
    if kind not in {"asr", "tts"}:
        raise ValueError("Unsupported job kind")
    job_id = job_id or uuid.uuid4().hex
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT request_hash,job_id FROM job_idempotency WHERE operation=? AND key_hash=?",
            (operation, key_hash),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != request_hash:
                db.execute("ROLLBACK")
                raise IdempotencyConflict("Idempotency-Key was already used with a different request")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (existing["job_id"],)).fetchone()
            db.execute("COMMIT")
            if row is None:  # pragma: no cover - protected by the foreign key
                raise RuntimeError("Idempotency record refers to a missing job")
            return _decode(row), True  # type: ignore[return-value]
        db.execute(
            """INSERT INTO jobs(
               id,kind,display_name,request_json,queue_seq,stage_code,created_at,updated_at
               ) VALUES(?,?,?,?,?,'queued',?,?)""",
            (
                job_id, kind, display_name, json.dumps(request, ensure_ascii=False),
                _next_queue_seq(db), now, now,
            ),
        )
        db.execute(
            "INSERT INTO job_idempotency(operation,key_hash,request_hash,job_id,created_at) VALUES(?,?,?,?,?)",
            (operation, key_hash, request_hash, job_id, now),
        )
        db.execute("COMMIT")
    return get_job(job_id), False  # type: ignore[return-value]


def find_idempotent_job(operation: str, key_hash: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            """SELECT jobs.* FROM job_idempotency
               JOIN jobs ON jobs.id=job_id
               WHERE operation=? AND key_hash=?""",
            (operation, key_hash),
        ).fetchone()
    return _decode(row)


def create_job(kind: str, display_name: str, request: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
    if kind not in {"asr", "tts"}:
        raise ValueError("Unsupported job kind")
    job_id = job_id or uuid.uuid4().hex
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """INSERT INTO jobs(
               id,kind,display_name,request_json,queue_seq,stage_code,created_at,updated_at
               ) VALUES(?,?,?,?,?,'queued',?,?)""",
            (job_id, kind, display_name, json.dumps(request, ensure_ascii=False), _next_queue_seq(db), now, now),
        )
        db.execute("COMMIT")
    return get_job(job_id)  # type: ignore[return-value]


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as db:
        return _decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def _job_filters(
    kind: str | None = None,
    state: str | None = None,
    query: str | None = None,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if kind:
        where.append("kind=?")
        params.append(kind)
    if state:
        where.append("state=?")
        params.append(state)
    if query and query.strip():
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        where.append(
            "(id COLLATE NOCASE LIKE ? ESCAPE '\\' "
            "OR display_name COLLATE NOCASE LIKE ? ESCAPE '\\')"
        )
        params.extend((pattern, pattern))
    return (f"WHERE {' AND '.join(where)}" if where else ""), params


JOB_SUMMARY_COLUMNS = ",".join((
    "id", "kind", "state", "progress", "stage", "display_name", "request_json",
    "error_code", "error_message", "cancel_requested", "attempts", "worker_id",
    "heartbeat_at", "processing_seconds", "created_at", "started_at", "finished_at",
    "updated_at", "queue_seq", "stage_code", "stage_current", "stage_total",
    "input_duration_seconds", "progress_basis", "stage_progress", "stage_unit",
    "progress_activity_json",
))


def list_jobs(kind: str | None = None, state: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    clause, params = _job_filters(kind, state)
    with connect() as db:
        rows = db.execute(
            f"SELECT {JOB_SUMMARY_COLUMNS} FROM jobs {clause} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
            (*params, min(max(limit, 1), 500), max(offset, 0)),
        ).fetchall()
    return [_decode(row) for row in rows]  # type: ignore[misc]


def list_jobs_page(
    kind: str | None = None,
    state: str | None = None,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return one stable newest-first page and its filtered total from one read snapshot."""
    clause, params = _job_filters(kind, state, query)
    safe_limit = min(max(limit, 1), 500)
    safe_offset = max(offset, 0)
    with connect() as db:
        db.execute("BEGIN")
        total = int(db.execute(f"SELECT COUNT(*) FROM jobs {clause}", params).fetchone()[0])
        rows = db.execute(
            f"SELECT {JOB_SUMMARY_COLUMNS} FROM jobs {clause} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
            (*params, safe_limit, safe_offset),
        ).fetchall()
        db.execute("COMMIT")
    return [_decode(row) for row in rows], total  # type: ignore[misc]


def touch_job_heartbeat(job_id: str, heartbeat_at: str | None = None) -> None:
    """Record executor liveness without changing the task's semantic version."""
    with connect() as db:
        db.execute(
            "UPDATE jobs SET heartbeat_at=? WHERE id=?",
            (heartbeat_at or utcnow(), job_id),
        )


def update_job(job_id: str, **values: Any) -> dict[str, Any] | None:
    allowed = {
        "state", "progress", "stage", "result_json", "error_code", "error_message",
        "cancel_requested", "worker_id", "heartbeat_at", "started_at", "finished_at", "attempts",
        "queue_seq", "stage_code", "stage_current", "stage_total", "input_duration_seconds",
        "progress_basis", "stage_progress", "stage_unit", "progress_activity_json",
    }
    changes: dict[str, Any] = {key: value for key, value in values.items() if key in allowed}
    if "state" in changes and changes["state"] not in JOB_STATES:
        raise ValueError("Invalid job state")
    if "result_json" in changes and not isinstance(changes["result_json"], str):
        changes["result_json"] = json.dumps(changes["result_json"], ensure_ascii=False)
    if "progress_activity_json" in changes and not isinstance(changes["progress_activity_json"], str):
        changes["progress_activity_json"] = json.dumps(changes["progress_activity_json"], ensure_ascii=False)
    changes["updated_at"] = utcnow()
    assignment = ",".join(f"{key}=?" for key in changes)
    with connect() as db:
        db.execute(f"UPDATE jobs SET {assignment} WHERE id=?", (*changes.values(), job_id))
    return get_job(job_id)


def update_job_progress(
    job_id: str,
    progress: float,
    stage: str,
    stage_code: str,
    stage_current: int | None = None,
    stage_total: int | None = None,
    stage_progress: float | None = None,
    stage_unit: str | None = None,
    progress_basis: str = "observed",
    activity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if progress_basis not in {"observed", "estimated"}:
        raise ValueError("progress_basis must be observed or estimated")
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT attempts,stage_code,progress,stage_progress FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            db.execute("ROLLBACK")
            return None
        attempt = max(1, int(row["attempts"] or 1))
        stage_changed = row["stage_code"] != stage_code
        if stage_changed:
            previous = db.execute(
                """SELECT sequence,started_at FROM job_stage_timings
                   WHERE job_id=? AND attempt=? AND finished_at IS NULL
                   ORDER BY sequence DESC LIMIT 1""",
                (job_id, attempt),
            ).fetchone()
            if previous is not None:
                duration = max(
                    0.0,
                    (datetime.fromisoformat(now) - datetime.fromisoformat(previous["started_at"])).total_seconds(),
                )
                db.execute(
                    """UPDATE job_stage_timings SET finished_at=?,duration_seconds=?
                       WHERE job_id=? AND attempt=? AND sequence=?""",
                    (now, duration, job_id, attempt, previous["sequence"]),
                )
            sequence = int(db.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM job_stage_timings WHERE job_id=? AND attempt=?",
                (job_id, attempt),
            ).fetchone()[0])
            db.execute(
                """INSERT INTO job_stage_timings(
                   job_id,attempt,sequence,stage_code,started_at
                   ) VALUES(?,?,?,?,?)""",
                (job_id, attempt, sequence, stage_code, now),
            )
        progress = round(max(float(row["progress"] or 0), max(0.0, min(float(progress), 0.99))), 4)
        if stage_progress is None and stage_current is not None and stage_total:
            stage_progress = stage_current / stage_total
        if stage_progress is not None:
            stage_progress = max(0.0, min(float(stage_progress), 1.0))
            if not stage_changed and row["stage_progress"] is not None:
                stage_progress = max(float(row["stage_progress"]), stage_progress)
            stage_progress = round(stage_progress, 4)
        if activity is not None:
            activity = {**activity, "updated_at": activity.get("updated_at") or now}
        activity_json = json.dumps(activity, ensure_ascii=False) if activity is not None else None
        db.execute(
            """UPDATE jobs SET progress=?,stage=?,stage_code=?,stage_current=?,stage_total=?,
               stage_progress=?,stage_unit=?,progress_basis=?,progress_activity_json=?,
               heartbeat_at=?,updated_at=? WHERE id=?""",
            (
                progress, stage, stage_code, stage_current, stage_total, stage_progress,
                stage_unit, progress_basis, activity_json, now, now, job_id,
            ),
        )
        db.execute("COMMIT")
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
        changes.update({
            "state": state, "finished_at": now, "updated_at": now,
            "progress_basis": "observed", "stage_progress": 1.0 if state == "succeeded" else None,
            "stage_current": None, "stage_total": None, "stage_unit": None,
            "progress_activity_json": None,
        })
        changes["stage_code"] = state
        assignment = ",".join(f"{key}=?" for key in changes)
        db.execute(
            f"""UPDATE jobs SET {assignment}, processing_seconds=processing_seconds+
                CASE WHEN started_at IS NULL THEN 0 ELSE
                MAX(0,(julianday(?) - julianday(started_at))*86400) END WHERE id=?""",
            (*changes.values(), now, job_id),
        )
        timing = db.execute(
            """SELECT attempt,sequence,started_at FROM job_stage_timings
               WHERE job_id=? AND finished_at IS NULL ORDER BY attempt DESC,sequence DESC LIMIT 1""",
            (job_id,),
        ).fetchone()
        if timing is not None:
            duration = max(
                0.0,
                (datetime.fromisoformat(now) - datetime.fromisoformat(timing["started_at"])).total_seconds(),
            )
            db.execute(
                """UPDATE job_stage_timings SET finished_at=?,duration_seconds=?
                   WHERE job_id=? AND attempt=? AND sequence=?""",
                (now, duration, job_id, timing["attempt"], timing["sequence"]),
            )
        db.execute("COMMIT")
    return get_job(job_id)


def claim_job(kind: str, worker_id: str) -> dict[str, Any] | None:
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id FROM jobs WHERE kind=? AND state='queued' ORDER BY queue_seq LIMIT 1", (kind,)
        ).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        db.execute(
            "UPDATE jobs SET state='running',stage='starting',stage_code='starting',progress=0.01,"
            "stage_current=NULL,stage_total=NULL,stage_progress=NULL,stage_unit=NULL,"
            "progress_basis='observed',progress_activity_json=NULL,worker_id=?,heartbeat_at=?,"
            "started_at=?,finished_at=NULL,attempts=attempts+1,updated_at=? WHERE id=?",
            (worker_id, now, now, now, row["id"]),
        )
        db.execute("COMMIT")
    return get_job(row["id"])


def request_cancel(job_id: str) -> dict[str, Any] | None:
    now = utcnow()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            db.execute("COMMIT")
            return None
        if row["state"] == "queued":
            db.execute(
                """UPDATE jobs SET state='cancelled',stage='cancelled',cancel_requested=1,
                   stage_code='cancelled',progress_basis='observed',stage_progress=NULL,
                   stage_current=NULL,stage_total=NULL,stage_unit=NULL,progress_activity_json=NULL,
                   finished_at=?,updated_at=? WHERE id=? AND state='queued'""",
                (now, now, job_id),
            )
        elif row["state"] == "running":
            db.execute(
                """UPDATE jobs SET cancel_requested=1,stage='cancelling',updated_at=?
                   ,stage_code='cancelling',progress_basis='observed',stage_progress=NULL,
                   stage_current=NULL,stage_total=NULL,stage_unit=NULL,progress_activity_json=NULL
                   WHERE id=? AND state='running'""",
                (now, job_id),
            )
        db.execute("COMMIT")
    return get_job(job_id)


def retry_job(job_id: str) -> dict[str, Any] | None:
    job = get_job(job_id)
    if job is None:
        return None
    if job["state"] not in {"failed", "cancelled"}:
        raise ValueError("Only failed or cancelled jobs can be retried")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """UPDATE jobs SET state='queued',stage='queued',stage_code='queued',progress=0,
               stage_current=NULL,stage_total=NULL,result_json=NULL,error_code=NULL,error_message=NULL,
               stage_progress=NULL,stage_unit=NULL,progress_basis='observed',progress_activity_json=NULL,
               cancel_requested=0,worker_id=NULL,heartbeat_at=NULL,started_at=NULL,finished_at=NULL,
               queue_seq=?,updated_at=? WHERE id=?""",
            (_next_queue_seq(db), utcnow(), job_id),
        )
        db.execute("COMMIT")
    return get_job(job_id)


def queued_count(kind: str) -> int:
    with connect() as db:
        return int(db.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind=? AND state='queued'", (kind,)
        ).fetchone()[0])


def active_jobs() -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """SELECT * FROM jobs WHERE state IN ('queued','running')
               ORDER BY kind,CASE state WHEN 'running' THEN 0 ELSE 1 END,queue_seq"""
        ).fetchall()
    return [_decode(row) for row in rows]  # type: ignore[misc]


def successful_jobs(kind: str, limit: int = 200) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            """SELECT * FROM jobs WHERE kind=? AND state='succeeded'
               ORDER BY finished_at DESC LIMIT ?""",
            (kind, min(max(limit, 1), 500)),
        ).fetchall()
    return [_decode(row) for row in rows]  # type: ignore[misc]


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
                """UPDATE jobs SET state='cancelled',stage='cancelled',stage_code='cancelled',
                   cancel_requested=1,finished_at=?,updated_at=? WHERE id=?""",
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
               state='queued',stage='recovered',stage_code='queued',stage_current=NULL,stage_total=NULL,
               worker_id=NULL,heartbeat_at=NULL,started_at=NULL,updated_at=? """
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


def event_revision(job_limit: int = 100) -> dict[str, Any]:
    """Return the small, heartbeat-free version vector for the global event stream."""
    with connect() as db:
        jobs = db.execute(
            "SELECT id,updated_at FROM jobs ORDER BY created_at DESC,id DESC LIMIT ?",
            (min(max(job_limit, 1), 500),),
        ).fetchall()
        workers = db.execute(
            """SELECT worker.id,worker.kind,worker.pid,worker.state,
                      worker.current_job_id,worker.details_json
               FROM workers AS worker
               WHERE worker.id = (
                   SELECT latest.id FROM workers AS latest
                   WHERE latest.kind = worker.kind
                   ORDER BY latest.heartbeat_at DESC, latest.id DESC LIMIT 1
               ) ORDER BY worker.kind"""
        ).fetchall()
    return {
        "jobs": [tuple(row) for row in jobs],
        "workers": [tuple(row) for row in workers],
    }


def create_voiceprint_person(
    name: str,
    person_id: str | None = None,
    *,
    note: str | None = None,
    include_in_hotword_library: bool = True,
) -> dict[str, Any]:
    clean_name = normalize_person_name(name)
    clean_note = normalize_person_note(note)
    now = utcnow()
    person_id = person_id or "voice_" + uuid.uuid4().hex[:16]
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                """INSERT INTO voiceprint_people(
                   id,name,name_key,note,include_in_hotword_library,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    person_id, clean_name, person_name_key(clean_name), clean_note,
                    int(include_in_hotword_library), now, now,
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO voiceprint_aliases(alias_id,person_id) VALUES(?,?)",
                (person_id, person_id),
            )
            _sync_voiceprint_hotword_lists(db, now)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return get_voiceprint_person(person_id)  # type: ignore[return-value]


def get_voiceprint_person(person_id: str) -> dict[str, Any] | None:
    with connect() as db:
        alias = db.execute(
            "SELECT person_id FROM voiceprint_aliases WHERE alias_id=?", (person_id,)
        ).fetchone()
        resolved = alias["person_id"] if alias else person_id
        row = db.execute("SELECT * FROM voiceprint_people WHERE id=?", (resolved,)).fetchone()
    return dict(row) if row else None


def find_voiceprint_person(name: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM voiceprint_people WHERE name_key=?", (person_name_key(name),)
        ).fetchone()
    return dict(row) if row else None


def update_voiceprint_person(
    person_id: str,
    *,
    name: str | object = _UNSET,
    note: str | None | object = _UNSET,
    include_in_hotword_library: bool | object = _UNSET,
) -> dict[str, Any] | None:
    changes: dict[str, Any] = {}
    sync_hotwords = False
    if name is not _UNSET:
        if not isinstance(name, str):
            raise ValueError("Voiceprint person name is required")
        clean_name = normalize_person_name(name)
        changes.update({"name": clean_name, "name_key": person_name_key(clean_name)})
        sync_hotwords = True
    if note is not _UNSET:
        if note is not None and not isinstance(note, str):
            raise ValueError("Voiceprint person note must be a string or null")
        changes["note"] = normalize_person_note(note)
    if include_in_hotword_library is not _UNSET:
        if not isinstance(include_in_hotword_library, bool):
            raise ValueError("include_in_hotword_library must be a boolean")
        changes["include_in_hotword_library"] = int(include_in_hotword_library)
        sync_hotwords = True
    if not changes:
        raise ValueError("At least one person field must be provided")
    now = utcnow()
    changes["updated_at"] = now
    assignment = ",".join(f"{key}=?" for key in changes)
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            cursor = db.execute(
                f"UPDATE voiceprint_people SET {assignment} WHERE id=?",
                (*changes.values(), person_id),
            )
            if cursor.rowcount and sync_hotwords:
                _sync_voiceprint_hotword_lists(db, now)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return get_voiceprint_person(person_id) if cursor.rowcount else None


def rename_voiceprint_person(person_id: str, name: str) -> dict[str, Any] | None:
    return update_voiceprint_person(person_id, name=name)


def delete_voiceprint_person_record(person_id: str) -> bool:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            cursor = db.execute("DELETE FROM voiceprint_people WHERE id=?", (person_id,))
            if cursor.rowcount:
                _sync_voiceprint_hotword_lists(db)
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return cursor.rowcount == 1


def create_voiceprint_sample(
    person_id: str,
    *,
    state: str = "pending",
    language: str = "Auto",
    audio_path: str | None = None,
    transcript: str | None = None,
    words: list[dict[str, Any]] | None = None,
    duration: float | None = None,
    source_job_id: str | None = None,
    source_segment_id: int | None = None,
    source_speaker_id: str | None = None,
    sample_id: str | None = None,
) -> dict[str, Any]:
    if state not in VOICEPRINT_SAMPLE_STATES:
        raise ValueError("Invalid voiceprint sample state")
    sample_id = sample_id or "sample_" + uuid.uuid4().hex[:16]
    now = utcnow()
    with connect() as db:
        db.execute(
            """INSERT INTO voiceprint_samples(
               id,person_id,state,language,audio_path,transcript,words_json,duration,
               source_job_id,source_segment_id,source_speaker_id,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sample_id, person_id, state, language, audio_path, transcript,
                json.dumps(words, ensure_ascii=False) if words is not None else None,
                duration, source_job_id, source_segment_id, source_speaker_id, now, now,
            ),
        )
    return get_voiceprint_sample(sample_id)  # type: ignore[return-value]


def _decode_voiceprint_sample(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    raw_words = item.pop("words_json", None)
    item["words"] = json.loads(raw_words) if raw_words else []
    return item


def get_voiceprint_sample(sample_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM voiceprint_samples WHERE id=?", (sample_id,)).fetchone()
    return _decode_voiceprint_sample(row)


def list_voiceprint_samples(person_id: str | None = None) -> list[dict[str, Any]]:
    clause = "WHERE person_id=?" if person_id else ""
    params = (person_id,) if person_id else ()
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM voiceprint_samples {clause} ORDER BY created_at DESC,id", params
        ).fetchall()
    return [_decode_voiceprint_sample(row) for row in rows]  # type: ignore[misc]


def list_voiceprint_people() -> list[dict[str, Any]]:
    samples = list_voiceprint_samples()
    by_person: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        by_person.setdefault(sample["person_id"], []).append(sample)
    with connect() as db:
        rows = db.execute("SELECT * FROM voiceprint_people ORDER BY name_key,id").fetchall()
    return [{**dict(row), "samples": by_person.get(row["id"], [])} for row in rows]


def update_voiceprint_sample(sample_id: str, **values: Any) -> dict[str, Any] | None:
    allowed = {
        "state", "language", "audio_path", "transcript", "words_json", "duration",
        "embedding", "embedding_model", "embedding_error", "source_job_id",
        "source_segment_id", "source_speaker_id", "error_message",
    }
    changes = {key: value for key, value in values.items() if key in allowed}
    if "state" in changes and changes["state"] not in VOICEPRINT_SAMPLE_STATES:
        raise ValueError("Invalid voiceprint sample state")
    if "words_json" in changes and not isinstance(changes["words_json"], str):
        changes["words_json"] = json.dumps(changes["words_json"], ensure_ascii=False)
    if not changes:
        return get_voiceprint_sample(sample_id)
    changes["updated_at"] = utcnow()
    assignment = ",".join(f"{key}=?" for key in changes)
    with connect() as db:
        db.execute(f"UPDATE voiceprint_samples SET {assignment} WHERE id=?", (*changes.values(), sample_id))
    return get_voiceprint_sample(sample_id)


def delete_voiceprint_sample_record(sample_id: str) -> bool:
    with connect() as db:
        cursor = db.execute("DELETE FROM voiceprint_samples WHERE id=?", (sample_id,))
    return cursor.rowcount == 1


def create_voice(name: str, language: str, ref_audio_path: str, ref_text: str) -> dict[str, Any]:
    person = find_voiceprint_person(name) or create_voiceprint_person(name)
    create_voiceprint_sample(
        person["id"], state="ready", language=language,
        audio_path=ref_audio_path, transcript=ref_text,
    )
    return get_voice(person["id"])  # type: ignore[return-value]


def get_voice(voice_id: str) -> dict[str, Any] | None:
    person = get_voiceprint_person(voice_id)
    if person is None:
        return None
    samples = [
        sample for sample in list_voiceprint_samples(person["id"])
        if sample["state"] == "ready" and sample.get("audio_path") and sample.get("transcript")
    ]
    if not samples:
        return None
    sample = samples[0]
    return {
        "id": person["id"], "name": person["name"], "language": sample["language"],
        "ref_audio_path": sample["audio_path"], "ref_text": sample["transcript"],
        "sample_id": sample["id"], "words": sample.get("words") or [], "duration": sample.get("duration"),
        "created_at": person["created_at"], "updated_at": person["updated_at"],
    }


def list_voices() -> list[dict[str, Any]]:
    voices = []
    for person in list_voiceprint_people():
        voice = get_voice(person["id"])
        if voice is not None:
            voices.append(voice)
    return voices


def delete_voice_record(voice_id: str) -> None:
    person = get_voiceprint_person(voice_id)
    if person is not None:
        delete_voiceprint_person_record(person["id"])
