"""Durable, independently queued reading drafts. Never modifies ASR results."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

from fastapi import HTTPException
from . import db
from .optimization_policy import PROMPT_VERSION, DEFAULT_MODE

SUMMARY = "COALESCE(json_extract(config_json, '$.prompt_version'), 1) AS prompt_version,json_extract(config_json, '$.rewrite_mode') AS rewrite_mode," + 'id,job_id,state,published,model,total_chars,completed_chars,completed_units,total_units,needs_review_units,activity_chars,error,revision,created_at,updated_at'
ACTIVE = ('queued', 'running', 'pausing')


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS optimization_settings (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), value_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transcript_optimizations (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            key_hash TEXT NOT NULL, fingerprint TEXT NOT NULL,
            state TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL, config_json TEXT NOT NULL, hotwords_json TEXT NOT NULL,
            source_hash TEXT NOT NULL, total_chars INTEGER NOT NULL,
            completed_chars INTEGER NOT NULL DEFAULT 0, completed_units INTEGER NOT NULL DEFAULT 0,
            total_units INTEGER NOT NULL, activity_chars INTEGER NOT NULL DEFAULT 0,
            error TEXT, revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(job_id,key_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_optimization_queue ON transcript_optimizations(state,created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_optimization_candidate ON transcript_optimizations(job_id) WHERE published=0;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_optimization_published ON transcript_optimizations(job_id) WHERE published=1;
        CREATE TABLE IF NOT EXISTS optimization_units (
            run_id TEXT NOT NULL REFERENCES transcript_optimizations(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL, source_json TEXT NOT NULL, optimized_text TEXT,
            PRIMARY KEY(run_id,ordinal)
        );
        UPDATE schema_meta SET version=15 WHERE version<15;
    ''')
    # Add result metadata separately from immutable source/configuration snapshots.
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        for table, columns in {
            'optimization_units': {'review_status': 'TEXT', 'review_reason': 'TEXT'},
            'transcript_optimizations': {'needs_review_units': 'INTEGER NOT NULL DEFAULT 0'},
        }.items():
            existing = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            for name, declaration in columns.items():
                if name not in existing:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        connection.execute('UPDATE schema_meta SET version=16 WHERE version<16')


def settings() -> dict:
    with db.connect() as connection:
        row = connection.execute('SELECT value_json FROM optimization_settings WHERE singleton=1').fetchone()
    return json.loads(row[0]) if row else {'base_url': '', 'model': '', 'use_proxy': False, 'enabled': False}


def save_settings(value: dict) -> None:
    with db.connect() as connection:
        connection.execute('INSERT INTO optimization_settings VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET value_json=excluded.value_json', (encode(value),))


def summary(row: Any) -> dict:
    item = dict(row)
    item['published'] = bool(item['published'])
    version = item.pop('prompt_version')
    item['requires_regeneration'] = not item['published'] and version != PROMPT_VERSION
    item['progress'] = item['completed_chars'] / max(1, item['total_chars'])
    return item


def status(job_id: str) -> dict:
    with db.connect() as connection:
        rows = connection.execute(f'SELECT {SUMMARY} FROM transcript_optimizations WHERE job_id=? ORDER BY published DESC', (job_id,)).fetchall()
    values = [summary(row) for row in rows]
    return {'published': next((x for x in values if x['published']), None), 'candidate': next((x for x in values if not x['published']), None)}


def summaries(job_ids: list[str] | None = None) -> dict[str, dict]:
    with db.connect() as connection:
        if job_ids is not None and not job_ids:
            return {}
        where = ' WHERE job_id IN (' + ','.join('?' for _ in job_ids) + ')' if job_ids is not None else ''
        rows = connection.execute(f'SELECT {SUMMARY} FROM transcript_optimizations{where} ORDER BY published DESC', job_ids or []).fetchall()
    return {row['job_id']: summary(row) for row in rows}


def get(run_id: str) -> dict:
    with db.connect() as connection:
        row = connection.execute('SELECT * FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Optimization not found / 优化任务不存在')
    item = dict(row)
    for field in ('config', 'hotwords'):
        item[field] = json.loads(item.pop(field + '_json'))
    return item


def units(run_id: str) -> list[dict]:
    with db.connect() as connection:
        rows = connection.execute('SELECT ordinal,source_json,optimized_text,review_status,review_reason FROM optimization_units WHERE run_id=? ORDER BY ordinal', (run_id,)).fetchall()
    return [{**json.loads(row['source_json']), 'ordinal': row['ordinal'], 'text': row['optimized_text'],
             'review_status': row['review_status'], 'review_reason': row['review_reason']} for row in rows]


def replay(job_id: str, key: str, regenerate: bool, rewrite_mode: str | None = None) -> bool:
    with db.connect() as connection:
        row = connection.execute('SELECT fingerprint FROM transcript_optimizations WHERE job_id=? AND key_hash=?', (job_id, digest(key))).fetchone()
    if row is None:
        return False
    fingerprints = [digest({'regenerate': regenerate, 'rewrite_mode': rewrite_mode or DEFAULT_MODE})]
    if rewrite_mode is None:
        fingerprints.append(digest({'regenerate': regenerate}))
    if row['fingerprint'] not in fingerprints:
        raise HTTPException(409, 'Idempotency key conflict')
    return True


def create(job_id: str, key: str, regenerate: bool, config: dict, source_hash: str, hotwords: list[str], paragraphs: list[dict], minimum_free: int = 0) -> tuple[str, bool]:
    import shutil
    fingerprint = digest({'regenerate': regenerate, **({'rewrite_mode': config['rewrite_mode']} if 'rewrite_mode' in config else {})})
    key_hash = digest(key)
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        existing = connection.execute('SELECT id,fingerprint FROM transcript_optimizations WHERE job_id=? AND key_hash=?', (job_id, key_hash)).fetchone()
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise HTTPException(409, 'Idempotency key conflict')
            return existing['id'], True
        parent = connection.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not parent or parent['state'] != 'succeeded':
            raise HTTPException(409, 'Completed transcription required / 需要已完成的转写')
        old = connection.execute('SELECT state,published,config_json FROM transcript_optimizations WHERE job_id=?', (job_id,)).fetchall()
        if any(row['state'] in ACTIVE or (row['state'] == 'paused' and not (regenerate and json.loads(row['config_json']).get('prompt_version', 1) != PROMPT_VERSION)) for row in old):
            raise HTTPException(409, 'Resume or delete the existing optimization / 请继续或删除现有优化任务')
        if old and not regenerate:
            raise HTTPException(409, 'Set regenerate=true / 请明确选择重新优化')
        if connection.execute("SELECT count(*) FROM transcript_optimizations WHERE state='queued'").fetchone()[0] >= 5:
            raise HTTPException(429, 'Optimization queue is full / 优化队列已满', headers={'Retry-After': '5'})
        if shutil.disk_usage(db.settings.data_dir).free < minimum_free:
            raise HTTPException(429, 'Insufficient disk space / 磁盘空间不足', headers={'Retry-After': '30'})
        connection.execute('DELETE FROM transcript_optimizations WHERE job_id=? AND published=0', (job_id,))
        identifier, now = uuid.uuid4().hex, db.utcnow()
        connection.execute('''INSERT INTO transcript_optimizations
            (id,job_id,key_hash,fingerprint,state,model,config_json,hotwords_json,source_hash,total_chars,total_units,created_at,updated_at)
            VALUES(?,?,?,?,'queued',?,?,?,?,?,?,?,?)''',
            (identifier, job_id, key_hash, fingerprint, config['model'], encode(config), encode(hotwords), source_hash,
             sum(len(p['original_text']) for p in paragraphs), len(paragraphs), now, now))
        connection.executemany('INSERT INTO optimization_units(run_id,ordinal,source_json) VALUES(?,?,?)', [(identifier, i, encode(p)) for i, p in enumerate(paragraphs)])
        connection.commit()
    return identifier, False


def transition(run_id: str, state: str, allowed: tuple[str, ...], error: str | None = None) -> bool:
    with db.connect() as connection:
        cursor = connection.execute(f"UPDATE transcript_optimizations SET state=?,error=?,revision=revision+1,updated_at=? WHERE id=? AND state IN ({','.join('?' for _ in allowed)})", (state, error, db.utcnow(), run_id, *allowed))
    return cursor.rowcount == 1


def resume(run_id: str) -> None:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT state FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Optimization not found')
        if row['state'] in ACTIVE:
            return
        if row['state'] not in ('paused', 'failed'):
            raise HTTPException(409, 'Task cannot be resumed')
        if connection.execute("SELECT count(*) FROM transcript_optimizations WHERE state='queued'").fetchone()[0] >= 5:
            raise HTTPException(429, 'Optimization queue is full', headers={'Retry-After': '5'})
        connection.execute("UPDATE transcript_optimizations SET state='queued',error=NULL,revision=revision+1,updated_at=? WHERE id=?", (db.utcnow(), run_id))
        connection.commit()


def checkpoint(run_id: str, values: list[tuple[int, str]], *, review_reason: str | None = None) -> bool:
    if review_reason not in (None, 'review_failed', 'invalid_output'):
        raise ValueError('Unknown review reason')
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT job_id FROM transcript_optimizations WHERE id=? AND state='running'", (run_id,)).fetchone()
        if not row:
            return False
        for ordinal, text in values:
            if review_reason:
                source = connection.execute('SELECT source_json FROM optimization_units WHERE run_id=? AND ordinal=?', (run_id, ordinal)).fetchone()
                if source is None or text != json.loads(source[0])['original_text']:
                    raise ValueError('Review fallback must preserve the exact original')
            connection.execute('UPDATE optimization_units SET optimized_text=?,review_status=?,review_reason=? WHERE run_id=? AND ordinal=? AND optimized_text IS NULL',
                               (text, 'needs_review' if review_reason else 'accepted', review_reason, run_id, ordinal))
        completed = connection.execute('SELECT source_json FROM optimization_units WHERE run_id=? AND optimized_text IS NOT NULL', (run_id,)).fetchall()
        count = sum(len(json.loads(p[0])['original_text']) for p in completed)
        needs_review = connection.execute("SELECT count(*) FROM optimization_units WHERE run_id=? AND review_status='needs_review'", (run_id,)).fetchone()[0]
        connection.execute('UPDATE transcript_optimizations SET completed_chars=?,completed_units=?,needs_review_units=?,activity_chars=0,revision=revision+1,updated_at=? WHERE id=?', (count, len(completed), needs_review, db.utcnow(), run_id))
        connection.commit()
    return True


def publish(run_id: str) -> bool:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT job_id FROM transcript_optimizations WHERE id=? AND state='running' AND completed_units=total_units", (run_id,)).fetchone()
        if not row:
            return False
        connection.execute('DELETE FROM transcript_optimizations WHERE job_id=? AND published=1', (row['job_id'],))
        connection.execute("UPDATE transcript_optimizations SET published=1,state='succeeded',revision=revision+1,updated_at=? WHERE id=?", (db.utcnow(), run_id))
        connection.commit()
    return True


def activity(run_id: str, count: int) -> None:
    with db.connect() as connection:
        connection.execute("UPDATE transcript_optimizations SET activity_chars=?,revision=revision+1,updated_at=? WHERE id=? AND state='running' AND activity_chars<>?", (count, db.utcnow(), run_id, count))


def delete(job_id: str) -> None:
    with db.connect() as connection:
        connection.execute('DELETE FROM transcript_optimizations WHERE job_id=?', (job_id,))
    clear_export_caches()


def clear_export_caches() -> None:
    from . import transcript_docx, transcript_pdf
    for module in (transcript_docx, transcript_pdf):
        with module._lock:
            module._cache_generation += 1
            module._cache.clear()
