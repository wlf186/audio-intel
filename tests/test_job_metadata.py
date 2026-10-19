from dataclasses import replace
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from audio_intel import api, db, purge, job_storage, waveform_ranges, waveforms
from audio_intel.config import settings
from audio_intel.document_download import deletion_guard


@pytest.fixture
def local(tmp_path, monkeypatch):
    value = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp', api_key='metadata-test', min_free_disk_bytes=0)
    for module in (api, db, purge):
        monkeypatch.setattr(module, 'settings', value)
    with TestClient(api.create_app()) as client:
        client.headers['Authorization'] = 'Bearer metadata-test'
        yield value, client


def audio_job(local):
    config, _ = local
    job = db.create_job('asr', 'synthetic', {})
    output = config.jobs_dir/job['id']/'output'
    output.mkdir(parents=True)
    samples = np.repeat(np.array([.1, .3, .5, .7], dtype='float32'), 16000)
    sf.write(output/'playback.wav', samples, 16000, subtype='PCM_16')
    sf.write(output/'enhanced.wav', samples*.5, 16000, subtype='PCM_16')
    result = {'playback_url': f'/api/v1/jobs/{job["id"]}/playback',
              'audio_enhancement': {'playback_url': f'/api/v1/jobs/{job["id"]}/artifacts/enhanced.wav'},
              'artifacts': [{'name': 'enhanced.wav', 'path': str(output/'enhanced.wav')}]}
    db.finish_job(job['id'], 'succeeded', result_json=result)
    return job, output


def test_storage_cache_refresh_missing_and_no_result_reads(local, monkeypatch):
    config, client = local
    job, output = audio_job(local)
    url = '/api/v1/jobs/storage'
    payload = {'job_ids': [job['id'], 'missing', job['id']]}
    first = client.post(url, json=payload)
    assert first.status_code == 200, first.text
    ready, missing = first.json()['items']
    assert ready['status'] == 'ready' and ready['bytes'] > 0
    assert ready['basis'] in {'allocated', 'logical'}
    assert missing['status'] == 'not_found' and missing['bytes'] is None
    calls = []
    original = job_storage.measure
    monkeypatch.setattr(job_storage, 'measure', lambda paths: (calls.append(paths), original(paths))[1])
    assert client.post(url, json=payload).json()['items'][0] == ready
    assert not calls
    temp = config.temp_dir/job['id']; temp.mkdir(parents=True)
    (temp/'large').write_bytes(b'a'*100000)
    fresh = client.post(url, json={**payload, 'refresh': True}).json()['items'][0]
    assert fresh['bytes'] > ready['bytes']
    assert len(calls) == 1
    # Corrupt historical JSON must not affect this metadata-only query.
    with db.connect() as connection:
        connection.execute('UPDATE jobs SET result_json=? WHERE id=?', ('{invalid', job['id']))
    assert client.post(url, json=payload).json()['items'][0]['status'] == 'ready'
    for bad in [[], ['x']*101]:
        assert client.post(url, json={'job_ids': bad}).status_code == 422
    assert client.post(url, json={'job_ids': ['x'], 'unknown': True}).status_code == 422
    client.headers.clear()
    assert client.post(url, json=payload).status_code == 401


def test_storage_does_not_follow_links_and_errors_are_unknown(tmp_path, local, monkeypatch):
    config, client = local
    job, output = audio_job(local)
    baseline = job_storage.measure([output.parent])
    outside = tmp_path/'outside'; outside.mkdir(); (outside/'secret').write_bytes(b'x'*100000)
    try:
        (output/'link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink privilege unavailable')
    assert job_storage.measure([output.parent])[0] < baseline[0]+100000
    def fail(_):
        raise PermissionError('denied')
    monkeypatch.setattr(job_storage, 'measure', fail)
    item = client.post('/api/v1/jobs/storage', json={'job_ids': [job['id']], 'refresh': True}).json()['items'][0]
    assert item['status'] == 'error' and item['bytes'] is None


def test_window_peaks_tracks_validation_auth_and_schema(local):
    _, client = local
    job, output = audio_job(local)
    url = f'/api/v1/jobs/{job["id"]}/waveform'
    value = client.get(url+'?start=1&end=3&points=4').json()
    assert value['start'] == 1 and value['end'] == 3 and value['duration'] == 4
    assert value['waveform'] == pytest.approx([.3, .3, .5, .5], abs=.0001)
    enhanced = client.get(url+'?track=enhanced&start=1&end=3&points=4').json()
    assert enhanced['waveform'] == pytest.approx([.15, .15, .25, .25], abs=.0001)
    for query in ['start=4', 'end=1&start=2', 'start=nan', 'end=inf', 'points=2049', 'track=invalid']:
        assert client.get(url+'?'+query).status_code == 422, query
    schema = client.get('/openapi.json').json()
    assert schema['components']['schemas']['JobWaveformResponse']['properties']['waveform']['maxItems'] == 2048
    assert schema['components']['schemas']['ArtifactWaveformResponse']['properties']['waveform']['maxItems'] == 240
    (output/'enhanced.wav').unlink()
    assert client.get(url+'?track=enhanced').status_code == 404
    client.headers.clear()
    assert client.get(url).status_code == 401


def test_window_reader_blocks_deletion_and_shares_busy_budget(local, monkeypatch):
    _, client = local
    job, _ = audio_job(local)
    url = f'/api/v1/jobs/{job["id"]}/waveform'
    entered, release = threading.Event(), threading.Event()
    original = waveform_ranges.retrieve
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(waveform_ranges, 'retrieve', blocked)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(client.get, url)
        assert entered.wait(5)
        try:
            assert client.delete(f'/api/v1/jobs/{job["id"]}?purge=true').status_code == 409
        finally:
            release.set()
        assert pending.result().status_code == 200
    def busy(*_):
        raise waveforms.WaveformBusy()
    monkeypatch.setattr(waveform_ranges, 'retrieve', busy)
    response = client.get(url)
    assert response.status_code == 429 and response.headers['Retry-After'] == '2'
    with deletion_guard(job['id']):
        assert client.get(url).status_code == 404


def test_storage_ttl_active_and_terminal_and_deleted_cache(local, monkeypatch):
    config, client = local
    job = db.create_job('asr', 'empty', {})
    clock = [100.]
    monkeypatch.setattr(job_storage.time, 'monotonic', lambda: clock[0])
    calls = []
    monkeypatch.setattr(job_storage, 'measure', lambda _: (calls.append(1) or 0, 'logical'))
    def read():
        return client.post('/api/v1/jobs/storage', json={'job_ids': [job['id']]}).json()['items'][0]
    assert read()['bytes'] == 0
    clock[0] += 4
    assert read()['bytes'] == 0 and len(calls) == 1
    clock[0] += 2
    read(); assert len(calls) == 2
    db.finish_job(job['id'], 'cancelled')
    read(); assert len(calls) == 3
    clock[0] += 29
    read(); assert len(calls) == 3
    clock[0] += 2
    read(); assert len(calls) == 4
    assert client.delete(f'/api/v1/jobs/{job["id"]}?purge=true').status_code == 204
    assert read()['status'] == 'not_found'


@pytest.mark.skipif(__import__('os').name != 'nt', reason='Native Windows junction behavior')
def test_storage_skips_windows_junction(tmp_path):
    import subprocess
    root = tmp_path/'owned'; root.mkdir()
    outside = tmp_path/'shared'; outside.mkdir()
    (outside/'large').write_bytes(b'x'*100000)
    baseline = job_storage.measure([root])[0]
    link = root/'junction'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    try:
        assert job_storage.measure([root])[0] < baseline + 100000
    finally:
        link.rmdir()


def test_window_decode_memory_is_bounded_and_invalid_file_is_422(local):
    import tracemalloc
    _, client = local
    job, output = audio_job(local)
    path = output/'playback.wav'
    with sf.SoundFile(path, 'w', samplerate=16000, channels=1, subtype='PCM_16') as stream:
        for _ in range(600):
            stream.write(np.full(16000, .25, dtype='float32'))
    tracemalloc.start()
    value = waveform_ranges.retrieve(path, 'original', 590, 600, 2048)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert value['duration'] == 600 and value['start'] == 590
    assert value['waveform'] == [.25]*2048 and peak < 4*1024*1024
    path.write_bytes(b'broken wav')
    assert client.get(f'/api/v1/jobs/{job["id"]}/waveform').status_code == 422
