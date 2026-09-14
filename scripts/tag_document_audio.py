"""Preview or apply MP3 album tags to one completed document job, with offline rollback.

Run with the API runtime. The default is read-only; --apply requires the service
using this data directory to be stopped. Backups stay under data/backups/.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audio_intel.document_metadata import snapshot, tags
from audio_intel.utils import atomic_json


@contextmanager
def maintenance_lock(data_dir: Path):
    """An OS lock is released on process exit, including interrupted repairs."""
    with (data_dir / '.document-metadata.lock').open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as handle:
        while block := handle.read(256 * 1024):
            value.update(block)
    return value.hexdigest()


def audio_identity(path: Path) -> tuple[str, str, int]:
    import av
    packets = hashlib.sha256()
    pcm = hashlib.sha256()
    count = 0
    with av.open(str(path)) as source:
        if len(source.streams.audio) != 1:
            raise ValueError('Expected one audio stream')
        for packet in source.demux(audio=0):
            if packet.dts is not None:
                packets.update(bytes(packet))
    with av.open(str(path)) as source:
        for frame in source.decode(audio=0):
            count += frame.samples
            pcm.update(frame.to_ndarray().tobytes())
    return packets.hexdigest(), pcm.hexdigest(), count


def retag(source_path: Path, destination: Path, metadata: dict[str, str]) -> None:
    import av
    with av.open(str(source_path)) as source, av.open(
        str(destination), 'w', format='mp3', options={'id3v2_version': '3', 'write_id3v1': '0'},
    ) as target:
        target.metadata.update(source.metadata)
        target.metadata.update(metadata)
        stream = target.add_stream_from_template(source.streams.audio[0])
        for packet in source.demux(audio=0):
            if packet.dts is not None:
                packet.stream = stream
                target.mux(packet)
    with destination.open('r+b') as handle:
        os.fsync(handle.fileno())
    if audio_identity(source_path) != audio_identity(destination):
        raise ValueError('Retagging changed encoded or decoded audio')
    with av.open(str(destination)) as check:
        if any(check.metadata.get(k) != v for k, v in metadata.items()):
            raise ValueError('MP3 tag verification failed')


def require_offline(data_dir: Path) -> None:
    import psutil
    for process in psutil.process_iter(['pid', 'cmdline']):
        try:
            args = process.info['cmdline'] or []
            if not any(a in {'audio_intel.worker', 'audio_intel.api'} or a.startswith('audio_intel.api:') for a in args):
                continue
            environ = process.environ()
            root = Path(environ.get('AUDIO_INTEL_DATA_DIR', str(Path(process.cwd()) / 'data'))).resolve()
            if root == data_dir:
                raise RuntimeError(f'Stop the audio-intel service using {data_dir} before applying tags')
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied as exc:
            raise RuntimeError('Cannot verify that the audio-intel service is stopped') from exc


def load_job(db: sqlite3.Connection, job_id: str) -> tuple[dict, dict, dict, list[dict]]:
    row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if row is None:
        raise ValueError('Job not found')
    job = dict(row)
    request = json.loads(job['request_json'])
    result = json.loads(job['result_json'] or '{}')
    if job['state'] != 'succeeded' or request.get('purpose') != 'tts_document':
        raise ValueError('Only completed document jobs can be tagged')
    sections = [dict(r) for r in db.execute('SELECT * FROM document_sections WHERE job_id=? ORDER BY position', (job_id,))]
    if not sections or any(s['state'] != 'complete' for s in sections):
        raise ValueError('Document checkpoints are incomplete')
    return job, request, result, sections


def artifact_path(data_dir: Path, job_id: str, artifact: dict) -> Path:
    root = (data_dir / 'jobs' / job_id / 'output').resolve()
    path = Path(artifact['path']).resolve()
    if path.parent != root or path.name != artifact['name'] or path.suffix != '.mp3':
        raise ValueError('Invalid document artifact path')
    return path


def inspect(db: sqlite3.Connection, data_dir: Path, job_id: str) -> tuple[dict, dict, dict, list[dict]]:
    job, request, result, sections = load_job(db, job_id)
    artifacts = result.get('artifacts', [])
    if len(artifacts) != len(sections) or len({a['name'] for a in artifacts}) != len(artifacts):
        raise ValueError('Artifact/checkpoint count mismatch')
    for artifact, section in zip(artifacts, sections, strict=True):
        if json.loads(section['artifact_json']) != artifact:
            raise ValueError('Artifact/checkpoint mismatch')
        path = artifact_path(data_dir, job_id, artifact)
        if path.stat().st_size != artifact['size_bytes'] or digest(path) != artifact['sha256']:
            raise ValueError('Document artifact checksum mismatch')
    return job, request, result, sections


def restore(db: sqlite3.Connection, data_dir: Path, job_id: str, backup: Path) -> None:
    journal = json.loads((backup / 'journal.json').read_text(encoding='utf-8'))
    if journal['job_id'] != job_id or journal['data_dir'] != str(data_dir):
        raise ValueError('Backup belongs to a different task or data directory')
    job, request, result, sections = load_job(db, job_id)
    before = journal['before']
    after = journal['after']
    if any(job[key] not in (before[key], after[key]) for key in ('request_json', 'result_json', 'updated_at')):
        raise ValueError('Task changed since backup; refusing rollback')
    originals = json.loads(before['result_json'])['artifacts']
    replacements = json.loads(after['result_json'])['artifacts']
    for original, replacement in zip(originals, replacements, strict=True):
        path = artifact_path(data_dir, job_id, original)
        saved = backup / 'original' / path.name
        if digest(saved) != original['sha256'] or digest(path) not in {original['sha256'], replacement['sha256']}:
            raise ValueError('Files changed since backup; refusing rollback')
    db.execute('BEGIN IMMEDIATE')
    try:
        for original in originals:
            path = artifact_path(data_dir, job_id, original)
            temporary = path.with_suffix('.restore.partial')
            try:
                shutil.copy2(backup / 'original' / path.name, temporary)
                with temporary.open('r+b') as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        db.execute('UPDATE jobs SET request_json=?,result_json=?,updated_at=? WHERE id=?',
                   (before['request_json'], before['result_json'], before['updated_at'], job_id))
        for section in journal['sections']:
            db.execute('UPDATE document_sections SET artifact_json=?,updated_at=? WHERE job_id=? AND id=?',
                       (section['artifact_json'], section['updated_at'], job_id, section['id']))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    journal['status'] = 'rolled_back'
    atomic_json(backup / 'journal.json', journal)


def apply(db: sqlite3.Connection, data_dir: Path, job_id: str) -> Path | None:
    import av
    from audio_intel.db import utcnow
    job, request, result, sections = inspect(db, data_dir, job_id)
    if request['document'].get('audio_metadata'):
        for index, (artifact, section) in enumerate(zip(result['artifacts'], sections, strict=True), 1):
            with av.open(str(artifact_path(data_dir, job_id, artifact))) as source:
                expected = tags(request, section['title'], index, len(sections))
                if any(source.metadata.get(k) != v for k, v in expected.items()):
                    raise ValueError('Existing metadata is inconsistent; restore the backup before retrying')
        return None
    request['document']['audio_metadata'] = snapshot(db, request, job['created_at'])
    sizes = [a['size_bytes'] for a in result['artifacts']]
    database_bytes = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
    if shutil.disk_usage(data_dir).free < 2 * sum(sizes) + max(sizes) + database_bytes + 64 * 1024**2:
        raise OSError('Insufficient space for verified staging and rollback backups')
    backup = data_dir / 'backups' / 'document-metadata' / f'{job_id}-{uuid.uuid4().hex}'
    (backup / 'original').mkdir(parents=True)
    (backup / 'tagged').mkdir()
    with sqlite3.connect(backup / 'database.sqlite3') as target:
        db.backup(target)
    for index, (artifact, section) in enumerate(zip(result['artifacts'], sections, strict=True), 1):
        path = artifact_path(data_dir, job_id, artifact)
        shutil.copy2(path, backup / 'original' / path.name)
        staged = backup / 'tagged' / path.name
        retag(path, staged, tags(request, section['title'], index, len(sections)))
        artifact.update(size_bytes=staged.stat().st_size, sha256=digest(staged))
        print(f'Validated {index}/{len(sections)}', flush=True)
    # Complete staging and checks before the first production file is replaced.
    inspect(db, data_dir, job_id)
    after = {'request_json': json.dumps(request, ensure_ascii=False),
             'result_json': json.dumps(result, ensure_ascii=False), 'updated_at': utcnow()}
    journal = {'job_id': job_id, 'data_dir': str(data_dir), 'status': 'prepared',
               'before': {k: job[k] for k in after}, 'after': after, 'sections': sections}
    atomic_json(backup / 'journal.json', journal)
    print(f'Backup: {backup}', flush=True)
    try:
        journal['status'] = 'applying'
        atomic_json(backup / 'journal.json', journal)
        db.execute('BEGIN IMMEDIATE')
        for artifact, section in zip(result['artifacts'], sections, strict=True):
            path = artifact_path(data_dir, job_id, artifact)
            temporary = path.with_suffix('.metadata.partial')
            try:
                shutil.copy2(backup / 'tagged' / path.name, temporary)
                with temporary.open('r+b') as handle:
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            db.execute('UPDATE document_sections SET artifact_json=?,updated_at=? WHERE job_id=? AND id=?',
                       (json.dumps(artifact, ensure_ascii=False), after['updated_at'], job_id, section['id']))
        db.execute('UPDATE jobs SET request_json=?,result_json=?,updated_at=? WHERE id=?',
                   (after['request_json'], after['result_json'], after['updated_at'], job_id))
        db.commit()
        journal['status'] = 'applied'
        atomic_json(backup / 'journal.json', journal)
    except BaseException:
        db.rollback()
        restore(db, data_dir, job_id, backup)
        raise
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true')
    action.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if len(args.job_id) != 32 or any(c not in '0123456789abcdef' for c in args.job_id):
        parser.error('job-id must be a full 32-character hexadecimal task ID')
    data_dir = args.data_dir.resolve()
    database = data_dir / 'audio_intel.sqlite3'
    changing = args.apply or args.rollback is not None
    with maintenance_lock(data_dir) if changing else nullcontext(), sqlite3.connect(
        database.as_uri() + ('?mode=rw' if changing else '?mode=ro'), uri=True, isolation_level=None,
    ) as db:
        if changing:
            require_offline(data_dir)
        db.row_factory = sqlite3.Row
        if args.rollback:
            backup = args.rollback.resolve()
            if backup.parent != data_dir / 'backups' / 'document-metadata':
                raise ValueError('Rollback requires a backup within this data directory')
            restore(db, data_dir, args.job_id, backup)
            print('Rolled back')
        elif args.apply:
            backup = apply(db, data_dir, args.job_id)
            print('Already tagged' if backup is None else 'Applied successfully')
        else:
            job, request, result, sections = inspect(db, data_dir, args.job_id)
            metadata = request['document'].get('audio_metadata') or snapshot(db, request, job['created_at'])
            print(json.dumps({'job_id': args.job_id, 'sections': len(sections), 'metadata': metadata,
                              'already_tagged': bool(request['document'].get('audio_metadata'))}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
