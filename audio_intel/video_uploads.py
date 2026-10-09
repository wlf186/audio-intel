"""Durable sequential uploads. One data file, no chunk files or concatenation copy."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import db
from .utils import safe_filename

AUDIO_FORMATS = ['wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus', 'aiff']
VIDEO_FORMATS = ['mp4', 'm4v', 'mov', 'mkv', 'webm', 'avi', 'mpeg', 'mpg', 'ts']


def migrate(connection: Any) -> None:
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS video_uploads (
            id TEXT PRIMARY KEY, key_hash TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, size_bytes INTEGER NOT NULL,
            offset INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
            updated_at REAL NOT NULL, expires_at REAL NOT NULL,
            job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS video_upload_chunks (
            upload_id TEXT NOT NULL REFERENCES video_uploads(id) ON DELETE CASCADE,
            offset INTEGER NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
            PRIMARY KEY(upload_id,offset)
        );
        CREATE INDEX IF NOT EXISTS idx_video_upload_expiry ON video_uploads(expires_at) WHERE job_id IS NULL;
        UPDATE schema_meta SET version=12 WHERE version<12;
    ''')


def remaining_bytes() -> int:
    with db.connect() as connection:
        return int(connection.execute('SELECT COALESCE(SUM(size_bytes-offset),0) FROM video_uploads WHERE job_id IS NULL').fetchone()[0])


def get(upload_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        row = connection.execute('SELECT * FROM video_uploads WHERE id=?', (upload_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Upload not found / 上传不存在或已清理')
    result = dict(row)
    if result['job_id'] is None and result['expires_at'] <= time.time():
        raise HTTPException(410, 'Upload expired / 上传已过期')
    return result


def public(row: dict[str, Any], config: Any) -> dict[str, Any]:
    with db.connect() as connection:
        chunks = [dict(value) for value in connection.execute(
            'SELECT offset,size_bytes,sha256 FROM video_upload_chunks WHERE upload_id=? ORDER BY offset', (row['id'],))]
    return {**{k: row[k] for k in ('id', 'name', 'size_bytes', 'offset', 'expires_at', 'job_id')},
            'chunk_bytes': config.video_upload_chunk_bytes, 'chunks': chunks}


def paths(config: Any, row: dict[str, Any]) -> tuple[Path, Path]:
    folder = config.jobs_dir / row['id'] / 'input'
    return folder / 'video.partial', folder / row['name']


def create(config: Any, name: str, size: int, key: str, reserved: int = 0) -> dict[str, Any]:
    name = safe_filename(name)
    if Path(name).suffix.lower().lstrip('.') not in VIDEO_FORMATS:
        raise HTTPException(422, 'Unsupported video container / 不支持的视频格式')
    if not 0 < size <= config.max_video_upload_bytes:
        raise HTTPException(413, 'Video exceeds upload limit / 视频超过上传上限')
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    now = time.time()
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute('SELECT * FROM video_uploads WHERE key_hash=?', (key_hash,)).fetchone()
        if old:
            if old['name'] != name or old['size_bytes'] != size:
                raise HTTPException(409, 'Idempotency-Key conflict')
            return get(old['id'])
        pending = connection.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes-offset),0) FROM video_uploads WHERE job_id IS NULL').fetchone()
        if pending[0] >= 8:
            raise HTTPException(429, 'Too many unfinished uploads / 未完成上传过多', headers={'Retry-After': '60'})
        if shutil.disk_usage(config.data_dir).free - pending[1] - reserved - size < config.min_free_disk_bytes:
            raise HTTPException(429, 'Insufficient storage / 可用磁盘空间不足', headers={'Retry-After': '300'})
        upload_id = uuid.uuid4().hex
        connection.execute('INSERT INTO video_uploads(id,key_hash,name,size_bytes,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?)',
                           (upload_id, key_hash, name, size, now, now, now + config.video_upload_idle_seconds))
        connection.execute('COMMIT')
    return get(upload_id)


def prepare_file(config: Any, row: dict[str, Any]):
    partial, target = paths(config, row)
    partial.parent.mkdir(parents=True, exist_ok=True)
    # A crash between rename and DB commit leaves an unclaimed final name.
    if target.exists() and not partial.exists() and not row['job_id']:
        os.replace(target, partial)
    if not partial.exists() and row['offset']:
        raise HTTPException(409, 'Upload file is missing; cancel and upload again')
    output = partial.open('r+b' if partial.exists() else 'w+b')
    if output.seek(0, 2) < row['offset']:
        output.close()
        raise HTTPException(409, 'Upload is shorter than its confirmed offset')
    output.truncate(row['offset'])
    output.seek(row['offset'])
    return output


def acknowledge(config: Any, row: dict[str, Any], size: int, digest: str) -> None:
    now = time.time()
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        changed = connection.execute('UPDATE video_uploads SET offset=offset+?,updated_at=?,expires_at=? WHERE id=? AND offset=? AND job_id IS NULL',
            (size, now, min(now + config.video_upload_idle_seconds, row['created_at'] + config.video_upload_lifetime_seconds), row['id'], row['offset']))
        if changed.rowcount != 1:
            raise HTTPException(409, 'Upload offset changed')
        connection.execute('INSERT INTO video_upload_chunks VALUES(?,?,?,?)', (row['id'], row['offset'], size, digest))
        connection.execute('COMMIT')


def discard(config: Any, upload_id: str, *, expired_only: bool = False) -> bool:
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT * FROM video_uploads WHERE id=?', (upload_id,)).fetchone()
        if row is None:
            return False
        if row['job_id']:
            if expired_only:
                return False
            raise HTTPException(409, 'Upload already belongs to a task; delete the task instead')
        if expired_only and row['expires_at'] > time.time():
            return False
        # Only server-created upload IDs determine the directory to remove.
        folder = config.jobs_dir / row['id']
        if folder.exists():
            shutil.rmtree(folder)
        connection.execute('DELETE FROM video_uploads WHERE id=?', (upload_id,))
        connection.execute('COMMIT')
    return True


def expired_ids() -> list[str]:
    with db.connect() as connection:
        return [row[0] for row in connection.execute('SELECT id FROM video_uploads WHERE job_id IS NULL AND expires_at<=? LIMIT 100', (time.time(),))]


def complete(config: Any, row: dict[str, Any], request: dict[str, Any], key_hash: str, fingerprint: str) -> tuple[dict[str, Any], bool]:
    """Rename and enqueue with recoverable filesystem/SQLite ordering."""
    partial, target = paths(config, row)
    with db.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        old = connection.execute("SELECT * FROM job_idempotency WHERE operation='submit_video' AND key_hash=?", (key_hash,)).fetchone()
        if old:
            if old['request_hash'] != fingerprint:
                raise HTTPException(409, 'Idempotency-Key conflict')
            return db.get_job(old['job_id']), True
        if row['job_id']:
            raise HTTPException(409, 'Upload already submitted')
        if row['offset'] != row['size_bytes']:
            raise HTTPException(409, 'Upload is not complete')
        file = partial if partial.exists() else target
        if not file.exists() or file.stat().st_size != row['size_bytes']:
            raise HTTPException(409, 'Upload file is incomplete')
        now = db.utcnow()
        request = {**request, 'input_path': str(target), 'original_name': row['name'], 'size_bytes': row['size_bytes']}
        if partial.exists():
            os.replace(partial, target)
        try:
            connection.execute("INSERT INTO jobs(id,kind,display_name,request_json,queue_seq,stage_code,created_at,updated_at) VALUES(?,'asr',?,?,?,'queued',?,?)",
                               (row['id'], row['name'], json.dumps(request, ensure_ascii=False), db._next_queue_seq(connection), now, now))
            connection.execute("INSERT INTO job_idempotency VALUES('submit_video',?,?,?,?)", (key_hash, fingerprint, row['id'], now))
            connection.execute('UPDATE video_uploads SET job_id=? WHERE id=?', (row['id'], row['id']))
            connection.execute('DELETE FROM video_upload_chunks WHERE upload_id=?', (row['id'],))
            connection.execute('COMMIT')
        except BaseException:
            connection.execute('ROLLBACK')
            if target.exists():
                os.replace(target, partial)
            raise
    return db.get_job(row['id']), False
