from __future__ import annotations

import json
import shutil
import wave
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asr import pipeline
from audio_intel import api, db, purge, worker
from audio_intel.config import settings
from audio_intel.document_store import clean_partials


def write_wave(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\x01\x00' * 32000)
    return path.read_bytes()


@pytest.fixture
def local(tmp_path, monkeypatch):
    value = replace(settings, data_dir=tmp_path / 'data', temp_dir=tmp_path / 'tmp',
                    mock_mode=True, api_key='playback-test', min_free_disk_bytes=0)
    for module in (pipeline, api, db, purge, worker):
        monkeypatch.setattr(module, 'settings', value)
    return value


def create_asr(local):
    job = db.create_job('asr', 'playback regression.wav', {'compute_device': 'cpu', 'align': True})
    source = local.jobs_dir / job['id'] / 'input/source.wav'
    write_wave(source)
    # Submission request snapshots are fixed before the worker claims this fixture.
    with db.connect() as connection:
        connection.execute('UPDATE jobs SET request_json=? WHERE id=?', (
            json.dumps({'input_path': str(source), 'compute_device': 'cpu', 'align': True}), job['id'],
        ))
    return db.get_job(job['id'])


def run_asr(job):
    claimed = db.claim_job('asr', 'test-playback-worker')
    assert claimed['id'] == job['id']
    outcome = worker._run_one_job('asr', job['id'], 'test-playback-worker', 1, pipeline.process_job)
    return outcome, db.get_job(job['id'])


def test_new_job_playback_survives_work_cleanup_auth_ranges_history_and_purge(local):
    with TestClient(api.create_app()) as client:
        job = create_asr(local)
        url = f"/api/v1/jobs/{job['id']}/playback"
        headers = {'Authorization': 'Bearer playback-test'}
        assert client.get(url).status_code == 401
        assert client.get(url, headers=headers).status_code == 409
        outcome, completed = run_asr(job)
        assert outcome['state'] == 'succeeded', completed.get('error_message')
        assert not (local.temp_dir / job['id']).exists()
        result = completed['result']
        assert result['playback_url'] == url
        output = local.jobs_dir / job['id'] / 'output'
        assert not list(output.glob('*.partial'))
        assert all(item['name'] != 'playback.wav' for item in result['artifacts'])
        assert json.loads((output / 'transcript.json').read_text(encoding='utf-8'))['playback_url'] == url
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        assert response.content == (output / 'playback.wav').read_bytes()
        assert response.headers['content-type'] == 'audio/wav'
        assert response.headers['accept-ranges'] == 'bytes'
        with wave.open(str(output / 'playback.wav')) as stream:
            assert (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) == (16000, 1, 2)
            assert stream.getnframes() / 16000 == result['duration']
        partial = client.get(url, headers={**headers, 'Range': 'bytes=0-43'})
        assert partial.status_code == 206
        assert partial.content == response.content[:44]
        assert partial.headers['content-range'] == f'bytes 0-43/{len(response.content)}'
        invalid = client.get(url, headers={**headers, 'Range': 'bytes=999999999-'})
        assert invalid.status_code == 416
        assert invalid.headers['content-range'] == f'bytes */{len(response.content)}'
        assert client.post('/api/v1/auth/session', headers=headers).status_code == 204
        assert client.get(url, headers={'Range': 'bytes=0-43'}).content == partial.content
        assert client.get(f"/api/v1/jobs/{job['id']}").json()['result']['playback_url'] == url
        assert client.get(f"/api/v1/jobs/{job['id']}/result").json()['playback_url'] == url
        assert 'result' not in client.get('/api/v1/jobs').json()['items'][0]
        source = Path(job['request']['input_path'])
        assert client.get(f"/api/v1/jobs/{job['id']}/source?download=true").content == source.read_bytes()
        historical = db.create_job('asr', 'historical', {'input_path': str(source)})
        db.finish_job(historical['id'], 'succeeded', result_json={'text': 'historical'})
        before = db.get_job(historical['id'])
        assert client.get(f"/api/v1/jobs/{historical['id']}/playback").status_code == 404
        assert 'playback_url' not in client.get(f"/api/v1/jobs/{historical['id']}/result").json()
        assert db.get_job(historical['id']) == before
        assert not (local.jobs_dir / historical['id'] / 'output').exists()
        tts = db.create_job('tts', 'tts', {})
        assert client.get(f"/api/v1/jobs/{tts['id']}/playback").status_code == 409
        assert client.get('/api/v1/jobs/missing/playback').status_code == 404
        schema = client.get('/openapi.json').json()
        operation = schema['paths']['/api/v1/jobs/{job_id}/playback']['get']
        assert operation['operationId'] == 'getJobPlayback'
        assert {'200', '206', '416', '401', '404', '409'} <= operation['responses'].keys()
        assert 'playback_url' in schema['components']['schemas']['JobResultResponse']['properties']
        assert client.delete('/api/v1/auth/session').status_code == 204
        assert client.get(url).status_code == 401
        (output / 'playback.wav').unlink()
        assert client.get(url, headers=headers).status_code == 404
        write_wave(output / 'playback.wav')
        assert client.delete(f"/api/v1/jobs/{job['id']}?purge=true", headers=headers).status_code == 204
        assert not output.parent.exists()


def test_playback_publication_exact_copy_failure_cleanup_and_retry(local, monkeypatch):
    with TestClient(api.create_app()):
        normalized = local.temp_dir / 'normalized.wav'
        expected = write_wave(normalized)
        url = pipeline.save_playback_audio('test', normalized)
        target = local.jobs_dir / 'test/output/playback.wav'
        assert url.endswith('/test/playback') and target.read_bytes() == expected
        real_replace = pipeline.os.replace
        def fail_replace(source, destination):
            assert Path(source).read_bytes() == expected
            # Windows requires the writer's file handle to be closed before replace.
            raise OSError('disk publication failed')
        monkeypatch.setattr(pipeline.os, 'replace', fail_replace)
        with pytest.raises(OSError, match='publication failed'):
            pipeline.save_playback_audio('test', normalized)
        assert target.read_bytes() == expected
        assert not target.with_suffix('.wav.partial').exists()
        monkeypatch.setattr(pipeline.os, 'replace', real_replace)
        job = create_asr(local)
        real_save = pipeline.save_playback_audio
        def fail_save(*_):
            raise OSError('playback disk full')
        monkeypatch.setattr(pipeline, 'save_playback_audio', fail_save)
        outcome, failed = run_asr(job)
        assert outcome['state'] == 'failed'
        assert 'playback disk full' in failed['error_message']
        assert failed['result'] is None
        assert not (local.temp_dir / job['id']).exists()
        monkeypatch.setattr(pipeline, 'save_playback_audio', real_save)
        db.retry_job(job['id'])
        outcome, completed = run_asr(job)
        assert outcome['state'] == 'succeeded'
        assert completed['result']['playback_url'].endswith('/playback')


def test_cancelled_playback_partial_is_removed_before_terminal_state(local):
    with TestClient(api.create_app()):
        job = create_asr(local)
        db.claim_job('asr', 'test-playback-worker')
        output = local.jobs_dir / job['id'] / 'output'
        output.mkdir()
        partial = output / 'playback.wav.partial'
        partial.write_bytes(b'interrupted copy')
        write_wave(local.temp_dir / job['id'] / 'normalized.wav')
        worker._forced_cancel(db.get_job(job['id']))
        assert db.get_job(job['id'])['state'] == 'cancelled'
        assert not partial.exists()
        assert not (local.temp_dir / job['id']).exists()
        db.retry_job(job['id'])
        outcome, completed = run_asr(job)
        assert outcome['state'] == 'succeeded'
        assert completed['result']['playback_url']


def test_playback_rejects_symlink_escape(local, tmp_path):
    with TestClient(api.create_app()) as client:
        job = create_asr(local)
        outcome, _ = run_asr(job)
        assert outcome['state'] == 'succeeded'
        output = local.jobs_dir / job['id'] / 'output'
        target = output / 'playback.wav'
        outside = tmp_path / 'outside.wav'
        write_wave(outside)
        target.unlink()
        try:
            target.symlink_to(outside)
        except OSError:
            pytest.skip('Symlinks require privileges on this Windows installation')
        response = client.get(f"/api/v1/jobs/{job['id']}/playback", headers={'Authorization': 'Bearer playback-test'})
        assert response.status_code == 404
        target.unlink()
        shutil.rmtree(output)
        output.symlink_to(tmp_path, target_is_directory=True)
        write_wave(tmp_path / 'playback.wav.partial')
        clean_partials(job['id'], local.jobs_dir)
        assert (tmp_path / 'playback.wav.partial').exists()
