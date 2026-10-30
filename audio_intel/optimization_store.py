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

SUMMARY = "COALESCE(json_extract(config_json, '$.prompt_version'), 1) AS prompt_version,json_extract(config_json, '$.rewrite_mode') AS rewrite_mode," + 'id,job_id,state,phase,review_failures,reviewed_parts,total_parts,published,model,total_chars,completed_chars,completed_units,total_units,needs_review_units,review_state,reviewed_units,uncertain_units,unavailable_units,basic_cleanup_units,review_error,activity_chars,error,revision,created_at,updated_at,summary_version,summary_state,summary_counts_json,summary_revision'
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
            'optimization_units': {'review_status': 'TEXT', 'review_reason': 'TEXT', 'candidate_text': 'TEXT', 'review_details_json': 'TEXT', 'generation_method': 'TEXT', 'review_outcome': 'TEXT'},
            'transcript_optimizations': {'phase': "TEXT NOT NULL DEFAULT 'generation'", 'review_failures': 'INTEGER NOT NULL DEFAULT 0', 'reviewed_parts': 'INTEGER NOT NULL DEFAULT 0', 'total_parts': 'INTEGER NOT NULL DEFAULT 0', 'needs_review_units': 'INTEGER NOT NULL DEFAULT 0', 'review_state': 'TEXT', 'reviewed_units': 'INTEGER NOT NULL DEFAULT 0', 'uncertain_units': 'INTEGER NOT NULL DEFAULT 0', 'unavailable_units': 'INTEGER NOT NULL DEFAULT 0', 'basic_cleanup_units': 'INTEGER NOT NULL DEFAULT 0', 'review_error': 'TEXT'},
        }.items():
            existing = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            for name, declaration in columns.items():
                if name not in existing:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        connection.execute('''CREATE TABLE IF NOT EXISTS optimization_parts (
            run_id TEXT NOT NULL, unit_ordinal INTEGER NOT NULL, part_ordinal INTEGER NOT NULL,
            source_json TEXT NOT NULL, text TEXT, generation_method TEXT, seam_done INTEGER NOT NULL DEFAULT 0,
            review_outcome TEXT, review_details_json TEXT,
            PRIMARY KEY(run_id,unit_ordinal,part_ordinal),
            FOREIGN KEY(run_id,unit_ordinal) REFERENCES optimization_units(run_id,ordinal) ON DELETE CASCADE
        )''')
        connection.execute('UPDATE schema_meta SET version=18 WHERE version<18')
        if connection.execute('SELECT version FROM schema_meta').fetchone()[0] < 19:
            # Explicit one-time reset: source grouping and publication contracts changed.
            connection.execute('DELETE FROM optimization_parts')
            connection.execute('DELETE FROM optimization_units')
            connection.execute('DELETE FROM transcript_optimizations')
            connection.execute('UPDATE schema_meta SET version=19')

    from .optimization_summaries import migrate as migrate_summaries
    migrate_summaries(connection)


def settings() -> dict:
    with db.connect() as connection:
        row = connection.execute('SELECT value_json FROM optimization_settings WHERE singleton=1').fetchone()
    return json.loads(row[0]) if row else {'base_url': '', 'model': '', 'use_proxy': False, 'enabled': False, 'context_length': 16384}


def save_settings(value: dict) -> None:
    with db.connect() as connection:
        connection.execute('INSERT INTO optimization_settings VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET value_json=excluded.value_json', (encode(value),))


def summary(row: Any) -> dict:
    item = dict(row)
    from .optimization_summaries import status as insight_status
    item['summary_status'] = insight_status(item)
    for key in ('summary_version','summary_state','summary_counts_json','summary_revision'):
        item.pop(key, None)
    item['published'] = bool(item['published'])
    version = item.pop('prompt_version')
    item['requires_regeneration'] = not item['published'] and version != PROMPT_VERSION
    item['review_total_units'] = item['total_units']
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


def _units(connection, run_id: str) -> list[dict]:
    rows = connection.execute('SELECT * FROM optimization_units WHERE run_id=? ORDER BY ordinal', (run_id,)).fetchall()
    return [{**json.loads(row['source_json']), 'ordinal': row['ordinal'], 'text': row['optimized_text'],
             'review_status': row['review_status'], 'review_reason': row['review_reason'],
             'candidate_text': row['candidate_text'], 'generation_method': row['generation_method'],
             'review_outcome': row['review_outcome'],
             'review_details': json.loads(row['review_details_json']) if row['review_details_json'] else None} for row in rows]


def units(run_id: str) -> list[dict]:
    with db.connect() as connection:
        return _units(connection, run_id)


def snapshot(run_id: str) -> tuple[dict, list[dict]]:
    """One SQLite read transaction for a consistent text/review export snapshot."""
    with db.connect() as connection:
        connection.execute('BEGIN')
        row = connection.execute('SELECT * FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Optimization not found')
        run = dict(row)
        for name in ('config', 'hotwords'):
            run[name] = json.loads(run.pop(name + '_json'))
        from .optimization_summaries import read
        run['insights'] = read(run_id, connection)
        return run, _units(connection, run_id)


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
        if connection.execute("SELECT count(*) FROM transcript_optimizations WHERE state='queued' OR summary_state='queued'").fetchone()[0] >= 5:
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
        connection.executemany('INSERT INTO optimization_units(run_id,ordinal,source_json) VALUES(?,?,?)', [(identifier, i, encode({k: v for k, v in p.items() if k != '_parts'})) for i, p in enumerate(paragraphs)])
        if config.get('prompt_version', 1) >= 6:
            from .optimization_engine import prepare_parts
            for ordinal, unit in enumerate(paragraphs):
                parts = unit.get('_parts') or prepare_parts(config, unit, hotwords)
                connection.executemany('INSERT INTO optimization_parts(run_id,unit_ordinal,part_ordinal,source_json) VALUES(?,?,?,?)',
                    [(identifier, ordinal, i, encode(part)) for i, part in enumerate(parts)])
        from .optimization_summaries import initialize
        initialize(connection, identifier, config, paragraphs)
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
        if connection.execute("SELECT count(*) FROM transcript_optimizations WHERE state='queued' OR summary_state='queued'").fetchone()[0] >= 5:
            raise HTTPException(429, 'Optimization queue is full', headers={'Retry-After': '5'})
        connection.execute("UPDATE transcript_optimizations SET state='queued',error=NULL,revision=revision+1,updated_at=? WHERE id=?", (db.utcnow(), run_id))
        connection.commit()


def checkpoint(run_id: str, values: list[tuple[int, str]], *, review_reason: str | None = None, candidate_text: str | None = None, review_details: dict | None = None) -> bool:
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
                if candidate_text is not None:
                    from .optimization_engine import validate_draft
                    validate_draft({'action': 'keep', 'text': candidate_text}, text)
            connection.execute('UPDATE optimization_units SET optimized_text=?,review_status=?,review_reason=?,candidate_text=?,review_details_json=? WHERE run_id=? AND ordinal=? AND optimized_text IS NULL',
                               (text, 'needs_review' if review_reason else 'accepted', review_reason, candidate_text if review_reason else None, encode(review_details) if review_reason and review_details else None, run_id, ordinal))
        completed = connection.execute('SELECT source_json FROM optimization_units WHERE run_id=? AND optimized_text IS NOT NULL', (run_id,)).fetchall()
        count = sum(len(json.loads(p[0])['original_text']) for p in completed)
        needs_review = connection.execute("SELECT count(*) FROM optimization_units WHERE run_id=? AND review_status='needs_review'", (run_id,)).fetchone()[0]
        connection.execute('UPDATE transcript_optimizations SET completed_chars=?,completed_units=?,needs_review_units=?,activity_chars=0,revision=revision+1,updated_at=? WHERE id=?', (count, len(completed), needs_review, db.utcnow(), run_id))
        connection.commit()
    return True


def publish(run_id: str) -> bool:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute("SELECT job_id FROM transcript_optimizations WHERE id=? AND state='running' AND completed_units=total_units AND review_state='completed' AND (summary_version IS NULL OR summary_state IN ('completed','partial'))", (run_id,)).fetchone()
        if not row:
            return False
        connection.execute('DELETE FROM transcript_optimizations WHERE job_id=? AND published=1', (row['job_id'],))
        connection.execute("UPDATE transcript_optimizations SET published=1,state='succeeded',phase='complete',review_state='completed',revision=revision+1,updated_at=? WHERE id=?", (db.utcnow(), run_id))
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


def parts(run_id: str, ordinal: int) -> list[dict]:
    with db.connect() as connection:
        rows = connection.execute('SELECT * FROM optimization_parts WHERE run_id=? AND unit_ordinal=? ORDER BY part_ordinal', (run_id, ordinal)).fetchall()
    return [{**dict(row), 'source': json.loads(row['source_json']),
             'review_details': json.loads(row['review_details_json']) if row['review_details_json'] else None} for row in rows]


def _generation_running(connection, run_id):
    return connection.execute("SELECT 1 FROM transcript_optimizations WHERE id=? AND state='running' AND published=0", (run_id,)).fetchone()


def _progress(connection, run_id):
    completed = connection.execute('SELECT source_json FROM optimization_parts WHERE run_id=? AND text IS NOT NULL', (run_id,)).fetchall()
    count = sum(len(json.loads(row[0])['original_text']) for row in completed)
    connection.execute('''UPDATE transcript_optimizations SET completed_chars=?,
        completed_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND optimized_text IS NOT NULL),
        basic_cleanup_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND generation_method IN ('basic_cleanup','mixed')),
        activity_chars=0,revision=revision+1,updated_at=? WHERE id=?''', (count, run_id, run_id, db.utcnow(), run_id))


def checkpoint_part(run_id: str, ordinal: int, part_ordinal: int, text: str, method: str) -> bool:
    if method not in ('model', 'basic_cleanup'):
        raise ValueError('Unknown generation method')
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not _generation_running(connection, run_id):
            return False
        connection.execute('UPDATE optimization_parts SET text=?,generation_method=? WHERE run_id=? AND unit_ordinal=? AND part_ordinal=? AND text IS NULL',
                           (text, method, run_id, ordinal, part_ordinal))
        _progress(connection, run_id)
        connection.commit()
    return True


def checkpoint_seam(run_id: str, ordinal: int, left: int, texts: tuple[str, str]) -> bool:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not _generation_running(connection, run_id):
            return False
        row = connection.execute('SELECT seam_done FROM optimization_parts WHERE run_id=? AND unit_ordinal=? AND part_ordinal=?', (run_id, ordinal, left)).fetchone()
        if row is None or row[0]:
            return bool(row)
        connection.execute('UPDATE optimization_parts SET text=?,seam_done=1 WHERE run_id=? AND unit_ordinal=? AND part_ordinal=?', (texts[0], run_id, ordinal, left))
        connection.execute('UPDATE optimization_parts SET text=? WHERE run_id=? AND unit_ordinal=? AND part_ordinal=?', (texts[1], run_id, ordinal, left + 1))
        _progress(connection, run_id)
        connection.commit()
    return True


def finish_unit(run_id: str, ordinal: int) -> bool:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not _generation_running(connection, run_id):
            return False
        rows = connection.execute('SELECT * FROM optimization_parts WHERE run_id=? AND unit_ordinal=? ORDER BY part_ordinal', (run_id, ordinal)).fetchall()
        if not rows or any(row['text'] is None or (json.loads(row['source_json'])['forced_after'] and not row['seam_done']) for row in rows):
            return False
        methods = {row['generation_method'] for row in rows}
        method = next(iter(methods)) if len(methods) == 1 else 'mixed'
        text = '\n\n'.join(row['text'] for row in rows if row['text'])
        connection.execute("UPDATE optimization_units SET optimized_text=?,generation_method=?,review_outcome='pending' WHERE run_id=? AND ordinal=? AND optimized_text IS NULL", (text, method, run_id, ordinal))
        _progress(connection, run_id)
        connection.commit()
    return True


def checkpoint_review(run_id: str, ordinal: int, part_ordinal: int, result: dict) -> bool:
    outcome = result['outcome']
    if outcome not in ('pass', 'fact', 'uncertain', 'unavailable'):
        raise ValueError('Invalid review outcome')
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        if not connection.execute("SELECT 1 FROM transcript_optimizations WHERE id=? AND published=0 AND state='running' AND phase='review' AND review_state='running'", (run_id,)).fetchone():
            return False
        connection.execute('UPDATE optimization_parts SET review_outcome=?,review_details_json=? WHERE run_id=? AND unit_ordinal=? AND part_ordinal=? AND review_outcome IS NULL',
                           (outcome, encode(result['details']), run_id, ordinal, part_ordinal))
        reviews = connection.execute('SELECT review_outcome,review_details_json FROM optimization_parts WHERE run_id=? AND unit_ordinal=? ORDER BY part_ordinal', (run_id, ordinal)).fetchall()
        if all(row[0] for row in reviews):
            values = {row[0] for row in reviews}
            final = next(value for value in ('fact', 'unavailable', 'uncertain', 'pass') if value in values)
            issues = [issue for row in reviews for issue in json.loads(row[1]).get('issues', [])]
            unique = list({encode(issue): issue for issue in issues}.values())
            details = {'stage': 'review', 'issues': unique}
            diagnostics = [json.loads(row[1]).get('diagnostic') for row in reviews]
            if any(diagnostics):
                details['diagnostic'] = '; '.join(dict.fromkeys(d for d in diagnostics if d))[:400]
            connection.execute('UPDATE optimization_units SET review_outcome=?,review_status=?,review_reason=?,review_details_json=? WHERE run_id=? AND ordinal=?',
                (final, 'accepted' if final == 'pass' else 'needs_review' if final == 'fact' else None,
                 'review_failed' if final == 'fact' else None, encode(details), run_id, ordinal))
        connection.execute('''UPDATE transcript_optimizations SET
            reviewed_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND review_outcome IN ('pass','fact','uncertain','unavailable')),
            needs_review_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND review_outcome='fact'),
            uncertain_units=(SELECT COUNT(*) FROM optimization_units WHERE run_id=? AND review_outcome='uncertain'),
            unavailable_units=(SELECT COUNT(DISTINCT unit_ordinal) FROM optimization_parts WHERE run_id=? AND review_outcome='unavailable'),
            reviewed_parts=(SELECT COUNT(*) FROM optimization_parts WHERE run_id=? AND review_outcome IS NOT NULL),
            revision=revision+1,updated_at=? WHERE id=?''', (run_id, run_id, run_id, run_id, run_id, db.utcnow(), run_id))
        connection.execute("UPDATE transcript_optimizations SET review_state='completed' WHERE id=? AND reviewed_units=total_units", (run_id,))
        connection.commit()
    clear_export_caches()
    return True


def begin_review(run_id: str) -> bool:
    with db.connect() as connection:
        cursor = connection.execute("""UPDATE transcript_optimizations SET phase='review',review_state=CASE WHEN reviewed_units=total_units THEN 'completed' ELSE 'running' END,
            total_parts=(SELECT COUNT(*) FROM optimization_parts WHERE run_id=?),revision=revision+1,updated_at=?
            WHERE id=? AND state='running' AND published=0 AND completed_units=total_units""", (run_id, db.utcnow(), run_id))
    return cursor.rowcount == 1


def record_review_connection(run_id: str, failed: bool) -> int:
    with db.connect() as connection:
        connection.execute("UPDATE transcript_optimizations SET review_failures=CASE WHEN ? THEN review_failures+1 ELSE 0 END WHERE id=? AND state='running'", (failed, run_id))
        row = connection.execute('SELECT review_failures FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
    return row[0] if row else 3


def invalidate_scene_change(connection: sqlite3.Connection, job_id: str, before: dict, after: dict) -> bool:
    """Commit invalidation in the same transaction as the scene result update."""
    if not connection.in_transaction:
        raise ValueError('Scene invalidation requires the scene update transaction')
    from .optimization_engine import source_hash
    if source_hash(before) == source_hash(after):
        return False
    connection.execute('DELETE FROM transcript_optimizations WHERE job_id=?', (job_id,))
    return True
