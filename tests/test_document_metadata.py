from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import av
import pytest

from audio_intel.document_metadata import snapshot, tags
from scripts import tag_document_audio as maintenance
from test_documents import document_client, import_document, submit_document


@pytest.mark.parametrize('mode,fields,label', [
    ('preset', {'speaker': 'Vivian'}, 'Vivian'),
    ('voiceprint', {'voiceprint_person_name': '高晓松', 'speaker': 'wrong'}, '高晓松'),
    ('profile', {'voice_profile_name': '我的声音'}, '我的声音'),
    ('inline_clone', {}, '参考音频克隆'),
    ('voice_design', {}, '声音设计'),
])
def test_voice_snapshot_and_utc_collision(mode, fields, label):
    request = {'purpose': 'tts_document', 'document': {'title': '中文 & English'}, 'voice_mode': mode, **fields}
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE jobs(kind TEXT,request_json TEXT)')
        first = snapshot(db, request, '2026-09-13T00:29:59+08:00')
        assert first['album'] == f'中文 & English · {label} · 2609121629'
        request['document']['audio_metadata'] = first
        db.execute('INSERT INTO jobs VALUES(?,?)', ('tts', json.dumps(request)))
        second = snapshot(db, request, '2026-09-12T16:29:59Z')
        assert second['album'] == first['album'] + ' · 2'
        request['voiceprint_person_name'] = 'Renamed'
        assert tags(request, '第三章', 3, 42) == {
            'album': first['album'], 'artist': label, 'album_artist': label,
            'title': '第三章', 'track': '3/42',
        }
        assert 'track' not in tags(request, '完整音频')
    assert tags({'document': {}}, 'old') == {}


def synthesize(client, local, legacy=False):
    from audio_intel import db
    from audio_intel.worker import JobContext
    import tts.pipeline as pipeline
    identifier, preview = import_document(client, '# 第一章\n\n你好，世界。\n\n# 第二章\n\n这是第二章。')
    job, _ = submit_document(client, identifier, preview)
    if legacy:
        request = job['request']
        request['document'].pop('audio_metadata')
        with db.connect() as connection:
            connection.execute('UPDATE jobs SET request_json=? WHERE id=?', (json.dumps(request), job['id']))
    context = JobContext(db.get_job(job['id']), 'test')
    result = pipeline.process_job(context)
    db.finish_job(job['id'], 'succeeded', result_json=result)
    return db.get_job(job['id'])


def test_submission_allocates_distinct_albums_and_replays_original(document_client, monkeypatch):
    from audio_intel import db
    client, local = document_client
    monkeypatch.setattr(db, 'utcnow', lambda: '2026-09-13T12:29:00+00:00')
    identifier, preview = import_document(client)
    first, _ = submit_document(client, identifier, preview)
    second, _ = submit_document(client, identifier, preview)
    first_album = first['request']['document']['audio_metadata']['album']
    assert first_album == 'test · Vivian · 2609131229'
    assert second['request']['document']['audio_metadata']['album'] == first_album + ' · 2'
    assert db.get_job(first['id'])['request']['document']['audio_metadata']['album'] == first_album


def test_new_tags_downloads_and_stable_retry(document_client):
    from audio_intel import db
    from audio_intel.worker import JobContext
    import tts.pipeline as pipeline
    client, local = document_client
    schema = client.get('/openapi.json').json()
    for path, method in [('/api/v1/tts/document-jobs', 'post'), ('/api/v1/jobs/{job_id}/document/download', 'get')]:
        description = schema['paths'][path][method]['description']
        assert 'ID3v2.3' in description and 'audio_metadata' in description and 'UTC' in description
    job = synthesize(client, local)
    expected = job['request']['document']['audio_metadata']
    suffix = datetime.fromisoformat(job['created_at']).astimezone(timezone.utc).strftime('%y%m%d%H%M')
    assert expected['album'].endswith('Vivian · ' + suffix)
    files = [Path(a['path']) for a in job['result']['artifacts']]
    before = [p.read_bytes() for p in files]
    for index, path in enumerate(files, 1):
        assert path.read_bytes()[:4] == b'ID3\x03'
        with av.open(str(path)) as source:
            assert source.metadata['album'] == expected['album']
            assert source.metadata['track'] == f'{index}/2'
            assert source.metadata['artist'] == source.metadata['album_artist'] == 'Vivian'
            assert source.metadata['title'] == ['第一章', '第二章'][index-1]
    result = pipeline.process_job(JobContext(job, 'test'))
    assert [p.read_bytes() for p in files] == before
    complete = client.get(f"/api/v1/jobs/{job['id']}/document/download?mode=complete")
    assert complete.status_code == 200
    with av.open(io.BytesIO(complete.content)) as source:
        assert source.metadata['album'] == expected['album']
        assert source.metadata['title'].endswith(' · 完整音频')
        assert 'track' not in source.metadata
    individual = client.get(f"/api/v1/jobs/{job['id']}/artifacts/{files[0].name}", headers={'Range': 'bytes=0-99'})
    assert individual.status_code == 206 and individual.content == before[0][:100]


@pytest.fixture
def legacy_journal_locale(monkeypatch):
    """Exercise Windows' non-UTF-8 locale even on UTF-8 development hosts."""
    original_open = Path.open

    def open_with_legacy_default(path, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
        if path.name == 'journal.json' and 'r' in mode and 'b' not in mode and encoding in (None, 'locale'):
            encoding = 'cp1252'
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, 'open', open_with_legacy_default)


def test_targeted_backfill_and_rollback(document_client, legacy_journal_locale):
    client, local = document_client
    job = synthesize(client, local, legacy=True)
    other = synthesize(client, local, legacy=True)
    artifacts = job['result']['artifacts']
    originals = {a['name']: Path(a['path']).read_bytes() for a in artifacts}
    others = {a['path']: Path(a['path']).read_bytes() for a in other['result']['artifacts']}
    with sqlite3.connect(local.database_path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        backup = maintenance.apply(db, local.data_dir, job['id'])
        assert backup is not None
        after, request, result, sections = maintenance.inspect(db, local.data_dir, job['id'])
        assert after['created_at'] == job['created_at']
        assert maintenance.apply(db, local.data_dir, job['id']) is None
        for index, a in enumerate(result['artifacts'], 1):
            with av.open(a['path']) as source:
                assert source.metadata['track'] == f'{index}/2'
                assert source.metadata['album'] == request['document']['audio_metadata']['album']
        maintenance.restore(db, local.data_dir, job['id'], backup)
        restored, request, result, sections = maintenance.inspect(db, local.data_dir, job['id'])
        assert 'audio_metadata' not in request['document']
        assert {a['name']: Path(a['path']).read_bytes() for a in result['artifacts']} == originals
        assert {p: Path(p).read_bytes() for p in others} == others
        assert json.loads(restored['result_json']) == job['result']
    complete = client.get(f"/api/v1/jobs/{other['id']}/document/download?mode=complete")
    with av.open(io.BytesIO(complete.content)) as source:
        assert 'album' not in source.metadata


def test_backfill_failure_restores_files_and_database(document_client, monkeypatch, legacy_journal_locale):
    client, local = document_client
    job = synthesize(client, local, legacy=True)
    original = {a['path']: Path(a['path']).read_bytes() for a in job['result']['artifacts']}
    replace = maintenance.os.replace
    failed = False
    def fail_once(source, target):
        nonlocal failed
        if str(source).endswith('.metadata.partial') and '002_' in str(source) and not failed:
            failed = True
            raise OSError('Injected replacement failure')
        return replace(source, target)
    monkeypatch.setattr(maintenance.os, 'replace', fail_once)
    with sqlite3.connect(local.database_path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        with pytest.raises(OSError, match='Injected'):
            maintenance.apply(db, local.data_dir, job['id'])
        _, request, result, _ = maintenance.inspect(db, local.data_dir, job['id'])
        assert 'audio_metadata' not in request['document']
        assert result == job['result']
    assert {p: Path(p).read_bytes() for p in original} == original
    assert not list((local.jobs_dir / job['id'] / 'output').glob('*.partial'))


@pytest.mark.parametrize('problem', ['checkpoint', 'checksum'])
def test_backfill_rejects_invalid_input_before_backups(document_client, problem):
    client, local = document_client
    job = synthesize(client, local, legacy=True)
    with sqlite3.connect(local.database_path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        if problem == 'checkpoint':
            db.execute("UPDATE document_sections SET state='pending' WHERE job_id=?", (job['id'],))
        else:
            Path(job['result']['artifacts'][0]['path']).write_bytes(b'corrupted')
        with pytest.raises(ValueError):
            maintenance.apply(db, local.data_dir, job['id'])
        assert not (local.data_dir / 'backups').exists()


def test_offline_guard_and_exclusive_lock(tmp_path, monkeypatch):
    import psutil
    from types import SimpleNamespace
    process = SimpleNamespace(info={'cmdline': ['python', '-m', 'uvicorn', 'audio_intel.api:app']},
                              environ=lambda: {'AUDIO_INTEL_DATA_DIR': str(tmp_path)}, cwd=lambda: str(tmp_path))
    monkeypatch.setattr(psutil, 'process_iter', lambda fields: [process])
    with pytest.raises(RuntimeError, match='Stop the audio-intel'):
        maintenance.require_offline(tmp_path)
    maintenance.require_offline(tmp_path / 'another-instance')
    with maintenance.maintenance_lock(tmp_path):
        with pytest.raises(OSError):
            with maintenance.maintenance_lock(tmp_path):
                pytest.fail('Second repair acquired the lock')
    with maintenance.maintenance_lock(tmp_path):
        pass
