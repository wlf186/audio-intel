"""Strict, sampled screenshot confirmation, independent of section boundaries."""
from __future__ import annotations

import copy
import io
import zipfile

import numpy as np
import pytest
from pypdf import PdfReader

from audio_intel import db, worker
from asr import pipeline
from audio_intel.video_detection import Detector, materialize, preview, select_representatives
from audio_intel.video_frames import decode
from test_video_detection import completed_video, pages, wait_preview
from test_video_transcription import client, local, video_file


def selected(frames, hold=5, change=3):
    detector = Detector({'min_change_percent': change, 'min_stable_seconds': hold})
    samples = [(i * .5, frame) for i, frame in enumerate(frames)]
    for timestamp, rgb in samples:
        detector.feed(rgb, timestamp)
    detector.flush(len(frames) * .5)
    select_representatives(samples, detector.scenes, hold, lambda *args: None)
    return detector.scenes


def test_small_end_notification_never_replaces_confirmed_picture():
    a, b = pages()
    flash = a.copy()
    flash[130:150, 270:310] = 20  # 1.4%: below the 3% section threshold.
    scenes = selected([a] * 21 + [flash] + [b] * 12)
    assert [(s['start'], s['end']) for s in scenes] == [(0, 11), (11, 17)]
    assert [s['frame_time'] for s in scenes] == [0, 11]
    assert selected([a] * 21 + [flash])[0]['frame_time'] == 0
    assert selected([a] * 6 + [flash] + [a] * 11)[0]['frame_time'] == 3.5


@pytest.mark.parametrize('hold,last,qualifies', [(.5, .49, False), (.5, .5, True),
    (.6, .5, False), (.6, .6, True), (1.5, 1.49, False), (1.5, 1.5, True),
    (5, 4.99, False), (5, 5, True), (5, 5.5, True)])
def test_actual_elapsed_time_threshold(hold, last, qualifies):
    a, _ = pages()
    scenes = [{'start': 0, 'end': last + .1}]
    select_representatives([(0, a), (last, a)], scenes, hold, lambda *args: None)
    assert (scenes[0]['frame_time'] == 0) is qualifies


def test_runs_cannot_accumulate_or_cross_boundaries_or_pad_eof():
    a, _ = pages()
    moving = a.copy(); moving[130:150, 270:310] = 20
    assert selected([a, moving] * 12, hold=.5)[0]['frame_time'] is None
    assert selected([a] * 10, hold=5)[0]['frame_time'] is None
    scenes = [{'start': 0, 'end': 1}, {'start': 1, 'end': 2}]
    select_representatives([(i * .5, a) for i in range(4)], scenes, 1, lambda *args: None)
    assert all(s['frame_time'] is None for s in scenes)


def test_latest_qualified_build_and_pointer_noise():
    a, _ = pages()
    built = a.copy(); built[70:85, 20:220] = 25
    assert selected([a] * 12 + [built] * 12)[0]['frame_time'] == 6
    assert selected([a] * 12 + [built] * 4)[0]['frame_time'] == 0
    frames = []
    for i in range(12):
        value = a.copy(); value[160:163, i:i + 3] = 0
        frames.append(value)
    assert selected(frames)[0]['frame_time'] == 0


def test_materialize_empty_picture_never_decodes_or_creates_directory(tmp_path):
    scenes = [{'frame_time': None, 'image': None}]
    materialize(tmp_path / 'missing.mp4', scenes, tmp_path / 'frames', None, 0, lambda *args: None,
                [{'frame_time': None, 'image': None}], tmp_path / 'missing')
    assert not (tmp_path / 'frames').exists()


def test_real_video_versions_cache_and_audio_are_compatible(tmp_path):
    source = tmp_path / 'video.mp4'; video_file(source)
    class Context:
        def progress(self, *args, **kwargs): pass
    settings = {'min_change_percent': 3, 'min_stable_seconds': 5}
    audio, rate, new = decode(source, tmp_path / 'new.wav', tmp_path / 'new/frames', None, Context(), settings)
    old_audio, old_rate, old = decode(source, tmp_path / 'old.wav', tmp_path / 'old/frames', None, Context(), settings, 'presentation-v2')
    assert np.array_equal(audio, old_audio) and rate == old_rate
    assert new['contract_version'] == 2 and new['detector'] == 'presentation-v3'
    assert old['contract_version'] == 1 and old['detector'] == 'presentation-v2'
    assert all(s['image'] is None and s['frame_time'] is None for s in new['scenes'])
    assert all(s['image'] for s in old['scenes'])
    request = {'source': str(source), 'cache': str(tmp_path / 'new/scene-features'), 'roi': None,
               'settings': settings, 'duration': new['duration'], 'time_origin': 0, 'min_free': 0,
               'existing_frames': str(tmp_path / 'new/frames'), 'existing_scenes': new['scenes']}
    cached = preview(request, tmp_path, lambda *args: None)
    assert cached['cache_hit'] and cached['scenes'] == new['scenes']
    # Force both the rebuild and the no-cache selection path.
    (tmp_path / 'new/scene-features/000000.npz').write_bytes(b'corrupt')
    from unittest.mock import patch
    with patch('audio_intel.video_detection.CACHE_LIMIT', 1):
        rebuilt = preview(request, tmp_path, lambda *args: None)
    assert not rebuilt['cache_hit'] and rebuilt['scenes'] == cached['scenes']


def test_empty_preview_apply_edit_restore_exports_and_schema(client, local):
    job = completed_video(client, local)
    root = '/api/v1/jobs/' + job['id']
    original = copy.deepcopy(job['result'])
    response = client.post(root + '/video-scene-previews', json={'revision': 1,
        'settings': {'min_change_percent': 3, 'min_stable_seconds': 5}}, headers={'Idempotency-Key': 'strict-picture-0001'})
    assert response.status_code == 202, response.text
    url = root + '/video-scene-previews/' + response.json()['id']
    item = wait_preview(client, url)
    assert item['contract_version'] == 2
    assert all(s['image'] is None and s['frame_time'] is None and s['preview_url'] is None for s in item['scenes'])
    assert db.get_job(job['id'])['result'] == original
    applied = client.patch(root + '/video-scenes', json={'action': 'apply_detection', 'revision': 1, 'preview_id': item['id']})
    assert applied.status_code == 200, applied.text
    value = applied.json()
    persisted = db.get_job(job['id'])['result']
    assert persisted['segments'] == original['segments'] and persisted['speakers'] == original['speakers']
    assert value['video']['contract_version'] == 2
    assert value['video']['original_scenes'] == original['video']['original_scenes']
    assert client.get(root).json()['result']['video']['scenes'][0]['image'] is None
    assert client.get(root + '/video-scenes').json()['scenes'][0]['image'] is None
    pdf = client.get(root + '/transcript.pdf')
    assert pdf.status_code == 200
    text = ''.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
    assert 'No stable picture' in text
    docx = client.post(root + '/transcript-export', json={'format': 'docx', 'redact': False})
    assert docx.status_code == 200, docx.text[:200]
    with zipfile.ZipFile(io.BytesIO(docx.content)) as archive:
        assert 'No stable picture' in archive.read('word/document.xml').decode()
        assert not any(name.startswith('word/media/') for name in archive.namelist())
    exported = client.post(root + '/transcript-export', json={'format': 'json', 'redact': False})
    assert exported.json()['video']['scenes'][0]['image'] is None
    scene = value['video']['scenes'][0]
    split = client.patch(root + '/video-scenes', json={'action': 'split', 'revision': 2, 'scene_id': scene['id'], 'time': 1})
    assert split.status_code == 200, split.text
    assert all(s['image'] is None for s in split.json()['video']['scenes'])
    restored = client.patch(root + '/video-scenes', json={'action': 'restore', 'revision': 3})
    assert restored.status_code == 200 and restored.json()['video']['scenes'][0]['image'] is None
    manual = client.patch(root + '/video-scenes', json={'action': 'frame', 'revision': 4, 'scene_id': scene['id'], 'time': .5})
    assert manual.status_code == 200 and manual.json()['video']['scenes'][0]['image']
    schemas = client.get('/openapi.json').json()['components']['schemas']
    assert {'type': 'null'} in schemas['VideoSceneResponse']['properties']['image']['anyOf']
    assert client.get('/api/v1/capabilities').json()['asr']['video']['scene_detection']['min_stable_seconds']['step'] == .1


@pytest.mark.parametrize('detector', ['presentation-v1', 'presentation-v2'])
def test_historical_retry_keeps_detector_and_original_picture_contract(client, local, detector):
    import json
    job = completed_video(client, local)
    request = copy.deepcopy(job['request'])
    request['video_detector'] = detector
    if detector == 'presentation-v1':
        request.pop('video_scene_detection')
    else:
        request['video_scene_detection']['min_stable_seconds'] = 5
    with db.connect() as connection:
        connection.execute("UPDATE jobs SET request_json=?,state='failed' WHERE id=?", (json.dumps(request), job['id']))
    retried = client.post('/api/v1/jobs/' + job['id'] + '/retry')
    assert retried.status_code == 200, retried.text
    assert db.get_job(job['id'])['request'] == request
    assert db.claim_job('asr', 'historical-retry')['id'] == job['id']
    worker._run_one_job('asr', job['id'], 'historical-retry', 1, pipeline.process_job)
    saved = db.get_job(job['id'])
    assert saved['state'] == 'succeeded', saved.get('error_message')
    assert saved['result']['video']['contract_version'] == 1
    assert saved['result']['video']['detector'] == detector
    assert all(s['image'] is not None for s in saved['result']['video']['scenes'])
