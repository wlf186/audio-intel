from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import numpy as np
import pytest

from audio_intel.video_detection import Detector, FeatureWriter, cached_samples
from test_video_transcription import client, local, upload, video_file
from audio_intel import db, worker
from asr import pipeline


def pages():
    first = np.full((180, 320, 3), 245, dtype=np.uint8)
    first[20:35, 20:190] = (20, 40, 160)
    second = np.full_like(first, 245)
    second[40:140, 180:220] = (170, 30, 40)
    return first, second


def detect(frames, hold=1, change=3):
    detector = Detector({'min_change_percent': change, 'min_stable_seconds': hold})
    for index, frame in enumerate(frames):
        detector.feed(frame, index * .5)
    detector.flush(len(frames) * .5)
    return detector.scenes


def test_subtitles_flash_return_and_real_page():
    a, b = pages()
    subtitles = []
    for index in range(12):
        frame = a.copy()
        frame[171:177, 100 + index:140 + index] = (0, 0, 0)
        subtitles.append(frame)
    scenes = detect(subtitles + [b, a, a, b, b, b, b])
    assert len(scenes) == 2
    assert scenes[1]['start'] == 7.5  # Never reuse the earlier flash timestamp.
    assert scenes[0]['end'] == scenes[1]['start']
    assert scenes[-1]['end'] == 9.5


def test_duration_threshold_and_replaced_candidate():
    a, b = pages()
    c = np.full_like(a, 100)
    assert len(detect([a]*4 + [b]*2 + [a]*4)) == 1
    scenes = detect([a]*4 + [b]*2 + [c]*4)
    assert len(scenes) == 2 and scenes[1]['start'] == 3
    assert len(detect([a]*4 + [b]*4, hold=3)) == 1


def test_builds_repeat_and_same_template_replacement():
    a, b = pages()
    built = a.copy()
    built[70:85, 20:220] = (25, 25, 25)
    scenes = detect([a]*4 + [built]*4 + [b]*4 + [built]*4)
    assert [s['start'] for s in scenes] == [0, 4, 6]
    assert scenes[0]['frame_time'] == 3.5
    replace = built.copy()
    replace[70:85, 20:220] = 245
    replace[95:110, 20:220] = 25
    assert len(detect([built]*4 + [replace]*4)) == 2


def test_area_threshold_blank_and_color_changes():
    a, _ = pages()
    b = a.copy()
    b[120:140, 100:160] = 0
    # Removing rather than adding content makes this an actual small replacement.
    assert len(detect([b]*4 + [a]*4, change=1)) == 2
    assert len(detect([b]*4 + [a]*4, change=5)) == 1
    scenes = detect([np.full_like(a, 255)] + [a]*5)
    assert len(scenes) == 1 and scenes[0]['start'] == 0
    red = np.full_like(a, (150, 0, 0))
    green = np.full_like(a, (0, 76, 0))
    assert len(detect([red]*4 + [green]*4)) == 2


def test_feature_cache_round_trip_identity_and_corruption(tmp_path):
    source = tmp_path/'source'; source.write_bytes(b'video')
    folder = tmp_path/'features'
    frames = [pages()[i % 2] for i in range(65)]
    writer = FeatureWriter(folder, source, None)
    for i, frame in enumerate(frames):
        writer.add(frame, i*.5)
    assert writer.finish(32.5, 0)
    loaded = list(cached_samples(folder, source, None))
    assert [time for time, _ in loaded] == [i*.5 for i in range(65)]
    assert all(np.array_equal(frame, rgb) for frame, (_, rgb) in zip(frames, loaded))
    with pytest.raises(ValueError):
        list(cached_samples(folder, source, {'x':0,'y':0,'width':1,'height':1}))
    (folder/'000000.npz').write_bytes(b'broken')
    import zipfile
    with pytest.raises(zipfile.BadZipFile):
        list(cached_samples(folder, source, None))


def test_cache_budget_is_optional(tmp_path, monkeypatch):
    from audio_intel import video_detection
    source=tmp_path/'source'; source.write_bytes(b'video')
    monkeypatch.setattr(video_detection, 'CACHE_LIMIT', 1)
    writer=FeatureWriter(tmp_path/'cache', source, None)
    writer.add(pages()[0],0)
    assert not writer.finish(1,0)
    assert not list(tmp_path.glob('cache*'))


def completed_video(client, local):
    data=video_file(local.data_dir/'fixture.mp4')
    uploaded=upload(client,data)
    response=client.post('/api/v1/asr/video-jobs',json={'upload_id':uploaded['id'],'compute_device':'cpu'},headers={'Idempotency-Key':'detection-job-0001'})
    assert response.status_code==202,response.text
    identifier=response.json()['id']
    assert db.claim_job('asr','detector-test')['id']==identifier
    worker._run_one_job('asr',identifier,'detector-test',1,pipeline.process_job)
    job=db.get_job(identifier)
    assert job['state']=='succeeded',job.get('error_message')
    return job


def wait_preview(client, url):
    until=time.monotonic()+30
    while time.monotonic()<until:
        response=client.get(url)
        assert response.status_code==200,response.text
        value=response.json()
        if value['state']!='running':
            assert value['state']=='ready',value
            return value
        time.sleep(.05)
    pytest.fail('Preview did not complete')


def test_preview_apply_restore_and_history(client,local):
    job=completed_video(client,local)
    original=copy.deepcopy(job['result'])
    root=f"/api/v1/jobs/{job['id']}"
    settings={'min_change_percent':3,'min_stable_seconds':1}
    body={'revision':1,'settings':settings}
    headers={'Idempotency-Key':'preview-test-0001'}
    response=client.post(root+'/video-scene-previews',json=body,headers=headers)
    assert response.status_code==202,response.text
    url=root+'/video-scene-previews/'+response.json()['id']
    assert client.post(root+'/video-scene-previews',json=body,headers=headers).status_code==200
    assert client.post(root+'/video-scene-previews',json={**body,'settings':{**settings,'min_change_percent':4}},headers=headers).status_code==409
    preview=wait_preview(client,url)
    assert db.get_job(job['id'])['result']==original
    assert client.get(preview['scenes'][0]['preview_url']).headers['content-type']=='image/jpeg'
    applied=client.patch(root+'/video-scenes',json={'revision':1,'action':'apply_detection','preview_id':preview['id']})
    assert applied.status_code==200,applied.text
    result=applied.json()
    assert result['video']['revision']==2
    assert result['video']['scene_detection']==settings
    assert result['segments']==original['segments']
    assert result['video']['original_scenes']==original['video']['original_scenes']
    merged=client.patch(root+'/video-scenes',json={'revision':2,'action':'merge_next','scene_id':result['video']['scenes'][0]['id']})
    assert merged.status_code==200
    restored=client.patch(root+'/video-scenes',json={'revision':3,'action':'restore'})
    assert restored.json()['video']['scenes']==result['video']['automatic_scenes']
    assert client.delete(url).status_code==204
    assert client.get(url).status_code==410


def test_preview_validation_revision_and_cancel(client,local):
    job=completed_video(client,local);root=f"/api/v1/jobs/{job['id']}"
    body={'revision':1,'settings':{'min_change_percent':3,'min_stable_seconds':1}}
    assert client.post(root+'/video-scene-previews',json=body).status_code==422
    for settings in ({'min_change_percent':0}, {'min_stable_seconds':7}, {'unknown':1}):
        assert client.post(root+'/video-scene-previews',json={**body,'settings':settings},headers={'Idempotency-Key':'invalid-preview-0001'}).status_code==422
    assert client.post(root+'/video-scene-previews',json={**body,'revision':2},headers={'Idempotency-Key':'stale-preview-0001'}).status_code==409
    response=client.post(root+'/video-scene-previews',json=body,headers={'Idempotency-Key':'cancel-preview-0001'})
    assert response.status_code==202
    url=root+'/video-scene-previews/'+response.json()['id']
    assert client.delete(url).status_code==204
    assert db.get_job(job['id'])['result']['video']['revision']==1
    assert not list((local.temp_dir/job['id']/'scene-previews').glob('*/process.json'))


def test_apply_conflict_auth_and_expiry_preserve_result(client,local):
    job=completed_video(client,local);root=f"/api/v1/jobs/{job['id']}"
    body={'revision':1,'settings':{}}
    assert client.post(root+'/video-scene-previews',json=body,headers={'Authorization':'Bearer wrong','Idempotency-Key':'auth-preview-0001'}).status_code==401
    response=client.post(root+'/video-scene-previews',json=body,headers={'Idempotency-Key':'conflict-preview-0001'})
    url=root+'/video-scene-previews/'+response.json()['id']
    preview=wait_preview(client,url)
    assert client.get(url+'/frames/not-a-frame.jpg').status_code==404
    scene=job['result']['video']['scenes'][0]
    edited=client.patch(root+'/video-scenes',json={'revision':1,'action':'merge_next','scene_id':scene['id']})
    assert edited.status_code==200
    before=db.get_job(job['id'])['result']
    conflict=client.patch(root+'/video-scenes',json={'revision':1,'action':'apply_detection','preview_id':preview['id']})
    assert conflict.status_code==409
    assert db.get_job(job['id'])['result']==before
    assert client.delete(url).status_code==204
    assert client.patch(root+'/video-scenes',json={'revision':2,'action':'apply_detection','preview_id':preview['id']}).status_code==410


def test_legacy_retry_policy_and_submission_snapshot(client,local):
    job=completed_video(client,local)
    assert job['request']['video_detector']=='presentation-v2'
    assert job['request']['video_scene_detection']=={'min_change_percent':3,'min_stable_seconds':1}
    # Historical caller snapshots omit the new optional field. Replaying the
    # same upload/key must not rewrite the persisted request to the new policy.
    old=copy.deepcopy(job['request'])
    old.pop('video_detector');old.pop('video_scene_detection')
    with db.connect() as connection:
        connection.execute('UPDATE jobs SET request_json=? WHERE id=?',(json.dumps(old),job['id']))
    body={'upload_id':old['upload_id'],'compute_device':'cpu'}
    response=client.post('/api/v1/asr/video-jobs',json=body,headers={'Idempotency-Key':'detection-job-0001'})
    assert response.status_code==200,response.text
    assert 'video_detector' not in db.get_job(job['id'])['request']
    assert client.post('/api/v1/asr/video-jobs',json={**body,'video_scene_detection':{}},headers={'Idempotency-Key':'detection-job-0001'}).status_code==409


def test_preview_process_cancel_holds_lease_and_releases_slot(client, local, monkeypatch):
    import asyncio
    import subprocess
    import sys
    import psutil
    from fastapi import HTTPException
    from audio_intel.video_previews import PreviewManager, PreviewRequest
    from audio_intel.document_download import deletion_guard

    job = completed_video(client, local)
    original = subprocess.Popen
    def slow_child(command, **kwargs):
        return original([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)

    async def exercise():
        manager = PreviewManager(local)
        body = PreviewRequest(revision=1, settings={})
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, 'Popen', slow_child)
            item, _ = manager.create(job['id'], body, 'running-cancel-0001')
            for _ in range(100):
                identity = item['folder'] / 'process.json'
                if identity.exists():
                    break
                await asyncio.sleep(.01)
            process = psutil.Process(json.loads(identity.read_text())['pid'])
            assert process.is_running()
            with deletion_guard(job['id']) as allowed:
                assert not allowed
            with pytest.raises(HTTPException) as busy:
                manager.create(job['id'], body, 'running-other-0001')
            assert busy.value.status_code == 429
            await manager.discard(job['id'], item['id'])
            assert not process.is_running()
            assert not item['folder'].exists()
            assert not manager.tasks
            with deletion_guard(job['id']) as allowed:
                assert allowed
                with pytest.raises(HTTPException) as deleting:
                    manager.create(job['id'], body, 'during-delete-0001')
                assert deleting.value.status_code == 404
        # The next real preview can complete without restarting anything.
        item, _ = manager.create(job['id'], body, 'after-cancel-0001')
        await asyncio.wait_for(manager.tasks[item['id']], 30)
        assert item['state'] == 'ready'
        item['expires_at'] = time.time() - 1
        with pytest.raises(HTTPException) as expired:
            manager.get(job['id'], item['id'])
        assert expired.value.status_code == 410
        await manager.close()
        assert not item['folder'].exists()
    asyncio.run(exercise())
    assert db.get_job(job['id'])['result'] == job['result']


def test_v2_picture_detection_preserves_decoded_audio(tmp_path):
    from audio_intel.video_frames import decode
    class Context:
        def progress(self, *args, **kwargs):
            pass
    source = tmp_path/'audio-parity.mp4'
    video_file(source)
    legacy = decode(source, tmp_path/'v1.wav', tmp_path/'v1-frames', None, Context())
    current = decode(source, tmp_path/'v2.wav', tmp_path/'v2-frames', None, Context(),
                     detection_settings={'min_change_percent':3, 'min_stable_seconds':1})
    assert legacy[1] == current[1] == 16000
    assert np.array_equal(legacy[0], current[0])
    assert (tmp_path/'v1.wav').read_bytes() == (tmp_path/'v2.wav').read_bytes()
    assert legacy[2]['duration'] == current[2]['duration']


def test_export_visibility_validation_persistence_and_pdf(client, local):
    import io
    from pypdf import PdfReader
    job = completed_video(client, local)
    root = f"/api/v1/jobs/{job['id']}"
    video = job['result']['video']
    ids = [scene['id'] for scene in video['scenes']]
    frames = local.jobs_dir / job['id'] / 'output/frames'
    original_files = {p.name: p.read_bytes() for p in frames.glob('*.jpg')}
    original_pdf = client.get(root + '/transcript.pdf').content
    def update(revision, hidden):
        return client.patch(root + '/video-scenes', json={'revision': revision, 'action': 'set_export_visibility', 'export_hidden_scene_ids': hidden})
    assert update(1, ['missing']).status_code == 422
    assert update(1, [ids[0], ids[0]]).status_code == 422
    assert client.patch(root+'/video-scenes', json={'revision':1,'action':'set_export_visibility'}).status_code == 422
    assert update(9, ids).status_code == 409
    response = update(1, ids)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['video']['revision'] == 2
    assert saved['video']['export_hidden_scene_ids'] == ids
    assert saved['segments'] == job['result']['segments']
    for key in ('scenes', 'original_scenes', 'automatic_scenes'):
        assert saved['video'][key] == video[key]
    assert client.get(root).json()['result']['video']['export_hidden_scene_ids'] == ids
    assert update(2, ids).json()['video']['revision'] == 2  # No-op writes do not invalidate previews.
    for redact in (False, True):
        exported = client.post(root+'/transcript-export', json={'format':'json', 'redact':redact}).json()
        assert exported['video']['export_hidden_scene_ids'] == ids
        pdf = client.post(root+'/transcript-export', json={'format':'pdf','redact':redact})
        assert pdf.status_code == 200
        reader = PdfReader(io.BytesIO(pdf.content))
        assert sum(len(page.images) for page in reader.pages) == 0
        assert 'Picture hidden' in ''.join(page.extract_text() for page in reader.pages)
    hidden_pdf = client.get(root+'/transcript.pdf').content
    assert hidden_pdf != original_pdf
    reader = PdfReader(io.BytesIO(hidden_pdf))
    text = ''.join(page.extract_text() for page in reader.pages)
    for segment in saved['segments']:
        assert segment['text'] in text
    assert {p.name:p.read_bytes() for p in frames.glob('*.jpg')} == original_files
    assert update(2, []).json()['video']['export_hidden_scene_ids'] == []
    restored = PdfReader(io.BytesIO(client.get(root+'/transcript.pdf').content))
    assert sum(len(page.images) for page in restored.pages) > 0
    assert 'Picture hidden' not in ''.join(page.extract_text() for page in restored.pages)


def test_detection_visibility_preserved_only_for_unchanged_parameters_and_frames(client, local):
    job = completed_video(client, local)
    root = f"/api/v1/jobs/{job['id']}"
    hidden = [job['result']['video']['scenes'][0]['id']]
    assert client.patch(root+'/video-scenes', json={'revision':1,'action':'set_export_visibility','export_hidden_scene_ids':hidden}).status_code == 200
    for revision, settings, expected in ((2, {'min_change_percent':3,'min_stable_seconds':1}, hidden),
                                         (3, {'min_change_percent':4,'min_stable_seconds':1}, [])):
        response = client.post(root+'/video-scene-previews', json={'revision':revision,'settings':settings}, headers={'Idempotency-Key':f'visibility-preview-{revision:04d}'})
        assert response.status_code == 202
        item = wait_preview(client, root+'/video-scene-previews/'+response.json()['id'])
        # Generating a preview alone must not clear markers.
        assert db.get_job(job['id'])['result']['video']['export_hidden_scene_ids'] == hidden
        saved = client.patch(root+'/video-scenes', json={'revision':revision,'action':'apply_detection','preview_id':item['id']})
        assert saved.status_code == 200, saved.text
        assert saved.json()['video']['export_hidden_scene_ids'] == expected
        assert client.delete(root+'/video-scene-previews/'+item['id']).status_code == 204


def test_visibility_never_follows_reused_ids_or_changed_manual_frames():
    from audio_intel.video_visibility import retained_hidden
    original = {'id':'scene_1','start':0,'end':4,'frame_time':1,'image':'a.jpg'}
    previous = {'scenes':[original], 'export_hidden_scene_ids':['scene_1']}
    assert retained_hidden(previous, [{**original,'id':'scene_2'}]) == ['scene_2']
    for difference in ({'end':5}, {'frame_time':2}, {'image':'b.jpg'}):
        assert retained_hidden(previous, [{**original,**difference}]) == []
    assert retained_hidden({'scenes':[original]}, [original]) == []


@pytest.mark.parametrize("detection_settings", [None, {"min_change_percent": 3, "min_stable_seconds": 1}])
def test_video_frames_do_not_decode_images_with_pillow(tmp_path, monkeypatch, detection_settings):
    from PIL import Image
    from audio_intel.video_frames import decode, picture_at, save_picture

    source = tmp_path / "pixel-boundary.mp4"
    video_file(source)

    def reject_file_decode(*args, **kwargs):
        pytest.fail("Model video processing must consume decoded pixels, not Pillow image files")

    monkeypatch.setattr(Image, "open", reject_file_decode)

    class Context:
        def progress(self, *args, **kwargs):
            pass

    audio, rate, result = decode(source, tmp_path / "audio.wav", tmp_path / "frames", None,
                                 Context(), detection_settings=detection_settings)
    assert rate == 16000 and len(audio) > 0
    assert len(result["scenes"]) == 2
    picture = picture_at(source, 1, {"x": 0, "y": 0, "width": .9, "height": .9})
    name = save_picture(picture, tmp_path / "preview")
    assert (tmp_path / "preview" / name).read_bytes().startswith(b"\xff\xd8")
