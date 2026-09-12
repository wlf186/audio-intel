from __future__ import annotations

import json
import threading
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from audio_intel import waveforms
from audio_intel.document_download import reader_lease, deletion_guard, lease


@pytest.mark.parametrize('format', ['wav', 'flac', 'mp3'])
def test_encoded_waveform_time_and_silence(tmp_path, format):
    from tts.pipeline import encode
    tone = np.sin(np.arange(8000) * 2 * np.pi * 600 / 24000)
    pcm = np.concatenate([tone * .2, np.zeros(8000), tone * .8]).astype('float32')
    path = encode(tmp_path / ('audio.' + format), pcm, 24000, format)
    value = waveforms.file_waveform(path)
    assert value['duration'] == pytest.approx(1, abs=.06)
    assert len(value['waveform']) == 240
    decoded, _ = sf.read(path, always_2d=True)
    expected = [round(min(1., float(np.max(np.abs(decoded[i * len(decoded) // 240:(i + 1) * len(decoded) // 240])))), 4) for i in range(240)]
    assert value['waveform'] == pytest.approx(expected, abs=.0001)
    assert value['waveform'][20] > .1
    assert value['waveform'][120] < .001
    assert value['waveform'][200] > value['waveform'][20] * 3.5


def test_pcm_uniform_bins_short_silence_and_stereo(tmp_path):
    assert waveforms.pcm_peaks([0, -.5, .2, 1]) == [0, .5, .2, 1]
    assert waveforms.pcm_peaks([0] * 500) == [0] * 240
    assert waveforms.pcm_peaks([2, -2]) == [1, 1]
    with pytest.raises(ValueError):
        waveforms.pcm_peaks([float('nan')])
    path = tmp_path / 'stereo.wav'
    sf.write(path, np.tile([.5, -.5], (24000, 1)), 24000, subtype='FLOAT')
    assert waveforms.file_waveform(path)['waveform'] == [.5] * 240


def test_cache_invalidates_without_rewriting_audio(tmp_path, monkeypatch):
    path = tmp_path / 'job/output/audio.wav'
    path.parent.mkdir(parents=True)
    sf.write(path, [.25] * 24000, 24000)
    original = (path.read_bytes(), path.stat().st_mtime_ns)
    value = waveforms.prepare(path)
    assert waveforms.cached(path) == value
    assert original == (path.read_bytes(), path.stat().st_mtime_ns)
    monkeypatch.setattr(waveforms, 'file_waveform', lambda _: pytest.fail('cache must avoid decoding'))
    assert waveforms.retrieve(path) == value
    sf.write(path, [.5] * 24000, 24000)
    assert waveforms.cached(path) is None
    sidecar = waveforms.cache_path(path)
    sidecar.write_text('{broken')
    assert waveforms.cached(path) is None
    assert not list(sidecar.parent.glob('*.partial'))


def test_decode_memory_is_bounded(tmp_path):
    path = tmp_path / 'long.wav'
    block = np.full(24000, .3, dtype='float32')
    with sf.SoundFile(path, 'w', samplerate=24000, channels=1, subtype='PCM_16') as stream:
        for _ in range(600):
            stream.write(block)
    # Warm up codec imports before measuring Python/native numpy allocations.
    waveforms.file_waveform(path)
    tracemalloc.start()
    value = waveforms.file_waveform(path)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert value['duration'] == 600
    assert peak < 8 * 1024 * 1024


def test_concurrent_requests_coalesce_and_have_independent_download_budget(tmp_path, monkeypatch):
    paths = [tmp_path / f'job/output/{index}.wav' for index in range(3)]
    paths[0].parent.mkdir(parents=True)
    for path in paths:
        sf.write(path, [.1] * 240, 24000)
    entered = threading.Barrier(3)
    release = threading.Event()
    calls = []
    original = waveforms.prepare
    def blocked(path):
        calls.append(path)
        entered.wait(timeout=5)
        release.wait(timeout=5)
        return original(path)
    monkeypatch.setattr(waveforms, 'prepare', blocked)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(waveforms.retrieve, paths[0])
        second = pool.submit(waveforms.retrieve, paths[1])
        entered.wait(timeout=5)
        same = pool.submit(waveforms.retrieve, paths[0])
        try:
            with pytest.raises(waveforms.WaveformBusy):
                waveforms.retrieve(paths[2])
            with reader_lease('waveform-test'):
                with lease('download-test', 1):
                    with deletion_guard('waveform-test') as allowed:
                        assert not allowed
        finally:
            release.set()
        assert same.result() == first.result()
        second.result()
    assert len(calls) == 2
    with deletion_guard('waveform-test') as allowed:
        assert allowed
        with pytest.raises(FileNotFoundError):
            with reader_lease('waveform-test'):
                pass


def test_waveform_api_auth_history_errors_schema_and_purge(tmp_path, monkeypatch):
    from audio_intel import api, db, purge
    from audio_intel.config import settings
    local = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp', api_key='wave-test', min_free_disk_bytes=0)
    for module in (api, db, purge):
        monkeypatch.setattr(module, 'settings', local)
    with TestClient(api.create_app()) as client:
        job = db.create_job('tts', 'history', {})
        path = local.jobs_dir / job['id'] / 'output/章节.wav'
        path.parent.mkdir(parents=True)
        sf.write(path, [.2] * 24000, 24000)
        result = {'artifacts': [{'name': path.name, 'path': str(path), 'mime_type': 'audio/wav', 'size_bytes': path.stat().st_size}]}
        url = f'/api/v1/jobs/{job["id"]}/artifacts/{path.name}/waveform'
        headers = {'Authorization': 'Bearer wave-test'}
        assert client.get(url).status_code == 401
        assert client.get(url, headers=headers).status_code == 409
        db.finish_job(job['id'], 'succeeded', result_json=result)
        response = client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        assert len(response.json()['waveform']) == 240
        assert db.get_job(job['id'])['result'] == result
        assert waveforms.cache_path(path).is_file()
        assert client.get(url.replace(path.name, 'missing.wav'), headers=headers).status_code == 404
        assert client.post('/api/v1/auth/session', headers=headers).status_code == 204
        assert client.get(url).json() == response.json()
        with reader_lease(job['id']):
            assert client.delete(f'/api/v1/jobs/{job["id"]}?purge=true', headers=headers).status_code == 409
        schema = client.get('/openapi.json').json()
        operation = schema['paths']['/api/v1/jobs/{job_id}/artifacts/{name}/waveform']['get']
        assert operation['operationId'] == 'getArtifactWaveform'
        assert {'200', '401', '404', '409', '415', '422', '429'} <= operation['responses'].keys()
        assert schema['components']['schemas']['ArtifactWaveformResponse']['properties']['waveform']['maxItems'] == 240
        with monkeypatch.context() as patch:
            def busy(_):
                raise waveforms.WaveformBusy()
            patch.setattr(waveforms, 'retrieve', busy)
            busy_response = client.get(url)
            assert busy_response.status_code == 429
            assert busy_response.headers['Retry-After'] == '2'
        waveforms.cache_path(path).unlink()
        path.write_bytes(b'broken')
        assert client.get(url).status_code == 422
        outside = tmp_path / 'outside.wav'
        outside.write_bytes(b'broken')
        result['artifacts'][0]['path'] = str(outside)
        db.update_job(job['id'], result_json=result)
        assert client.get(url).status_code == 404
        result['artifacts'][0]['path'] = str(path.with_suffix('.json'))
        path.with_suffix('.json').write_text('{}')
        db.update_job(job['id'], result_json=result)
        assert client.get(url).status_code == 415
        assert client.delete('/api/v1/auth/session').status_code == 204
        assert client.get(url).status_code == 401
        assert client.delete(f'/api/v1/jobs/{job["id"]}?purge=true', headers=headers).status_code == 204
        assert not path.parent.parent.exists()


def test_optional_cache_write_failure_preserves_audio_and_cleans_partial(tmp_path, monkeypatch):
    path = tmp_path / 'job/output/audio.wav'
    path.parent.mkdir(parents=True)
    sf.write(path, [.2] * 24000, 24000)
    original = path.read_bytes()
    def fail(*_):
        raise OSError('disk full')
    monkeypatch.setattr(waveforms.os, 'replace', fail)
    assert waveforms.prepare_optional(path)['waveform']
    assert path.read_bytes() == original
    assert not list(waveforms.cache_path(path).parent.glob('*.partial'))
    monkeypatch.setattr(waveforms, 'file_waveform', fail)
    assert waveforms.prepare_optional(path) is None


def test_interrupted_waveform_write_cleanup_keeps_completed_cache(tmp_path):
    from audio_intel.document_store import clean_partials
    folder = tmp_path / 'job/waveforms'
    folder.mkdir(parents=True)
    partial = folder / 'waveform-interrupted.partial'
    partial.write_text('{')
    complete = folder / 'complete.json'
    complete.write_text('{}')
    clean_partials('job', tmp_path)
    assert not partial.exists()
    assert complete.read_text() == '{}'
