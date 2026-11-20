from pathlib import Path

import numpy as np
import pytest

from audio_intel.video_auto import AutoDetector
from audio_intel.video_detail import DetailDetector
from audio_intel import video_auto_index
from test_video_detection import completed_video, wait_preview
from test_video_transcription import client, local


def slide():
    page = np.full((540, 960), 240, dtype=np.uint8)
    page[45:60, 50:480] = 32
    page[110:125, 60:200] = 60
    return page


def test_stable_replacement_addition_pointer_and_return():
    first = slide()
    replace = first.copy()
    replace[110:125, 178:182] = 240
    built = replace.copy()
    built[180:191, 60:150] = 20
    detector = AutoDetector()
    detailed_detector = DetailDetector()
    for index, page in enumerate([first]*8 + [replace]*8 + [built]*8 + [first]*8):
        frame = page.copy()
        frame[400:403, 700+index:703+index] = 0
        detector.feed(frame, index * .5)
        detailed_detector.feed(frame, index * .5)
    detector.flush(16)
    detailed_detector.flush(16)
    expected = {
        'concise': [(0, 8), (12, 12)],
        'balanced': [(0, 0), (4, 8), (12, 12)],
        'detailed': [(0, 0), (4, 4), (8, 8), (12, 12)],
    }
    for level, timeline in expected.items():
        assert [(s['start'], s['frame_time']) for s in detailed_detector.result(level)] == timeline
    assert [(s['start'], s['frame_time']) for s in detector.scenes] == [(0, 0), (4, 8), (12, 12)]


def test_index_reselect_observed_frames_within_new_boundaries(tmp_path):
    index = video_auto_index.SampleIndex()
    for i in range(12):
        index.add(i*.5, 0. if i < 6 else 3.)
    source = tmp_path/'source'
    source.write_bytes(b'source')
    path = tmp_path/video_auto_index.NAME
    index.save(path, source, None, [], 0)
    _, records = video_auto_index.read(path, source, None)
    sections = [dict(start=1.1, end=3.), dict(start=3., end=3.9)]
    video_auto_index.select(records, sections)
    assert [s['frame_time'] for s in sections] == [1.5, None]
    with pytest.raises(ValueError):
        video_auto_index.read(path, source, {})


def test_range_preview_never_applies_and_whole_auto_reuses_snapshot(client, local):
    job = completed_video(client, local)
    root = '/api/v1/jobs/'+job['id']
    for i, extra in enumerate([{'time_range':[1, 4]}, {}]):
        response = client.post(root+'/video-scene-previews', json={'revision':1,'mode':'auto',**extra},
                               headers={'Idempotency-Key':f'auto-range-preview-{i}'})
        assert response.status_code == 202
        url = root+'/video-scene-previews/'+response.json()['id']
        preview = wait_preview(client, url)
        assert preview['cache_hit'] and preview['mode']=='auto'
        applied = client.patch(root+'/video-scenes',json=dict(revision=1,action='apply_detection',preview_id=preview['id']))
        assert applied.status_code == (409 if extra else 200)
        client.delete(url)


@pytest.mark.parametrize('hold',[.5,.6,1,2.5,5])
def test_one_pass_custom_matches_original_two_pass(hold):
    from audio_intel.video_detection import Detector, StableDetector, select_representatives
    from test_video_detection import pages
    a,b=pages()
    build=a.copy()
    build[120:132,60:210]=0
    rng=np.random.default_rng(51)
    samples=[]
    for i in range(80):
        picture=[a,b,build][int(rng.integers(0,3))]
        for _ in range(int(rng.integers(1,11))):
            samples.append((len(samples)*.51,picture))
    old=Detector({'min_stable_seconds':hold})
    new=StableDetector({'min_stable_seconds':hold})
    for t,frame in samples:
        old.feed(frame,t)
        new.feed(frame,t)
    # Last sample exactly at EOF must not be used for confirmation.
    old.flush(t)
    new.flush(t)
    select_representatives(samples,old.scenes,hold,lambda *args:None)
    assert new.scenes==[{k:v for k,v in s.items() if k!='image'} for s in old.scenes]


def test_auto_conflicting_inputs_and_interval_validation(client, local):
    from audio_intel.video_api import VideoJobRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        VideoJobRequest(upload_id='a'*32,video_scene_mode='auto',video_scene_detection={})
    job=completed_video(client,local)
    url=f"/api/v1/jobs/{job['id']}/video-scene-previews"
    for i, payload in enumerate([{'mode':'auto','settings':{}}, {'time_range':[0,0]},
                                 {'time_range':[-1,2]}, {'time_range':[1,10000]}]):
        reply=client.post(url,json={'revision':1,**payload},headers={'Idempotency-Key':f'bad-interval-{i:04d}'})
        assert reply.status_code==422


def test_partial_feature_cache_retains_exact_interval(tmp_path, monkeypatch):
    from audio_intel import video_detection as module
    source=tmp_path/'source.mp4'
    source.touch()
    rng=np.random.default_rng(20)
    frames=rng.integers(0,256,size=(4,180,320,3),dtype=np.uint8)
    monkeypatch.setattr(module,'CHUNK_SIZE',2)
    monkeypatch.setattr(module,'CACHE_LIMIT',500_000)
    cache=tmp_path/'features'
    writer=module.FeatureWriter(cache,source,None)
    for i,frame in enumerate(frames):
        writer.add(frame,i*.5)
    assert writer.finish(2.,0.)
    assert np.array_equal(next(module.cached_samples(cache,source,None,0,.5))[1],frames[0])
    with pytest.raises(ValueError):
        list(module.cached_samples(cache,source,None))


def test_detail_preview_index_scope_and_legacy_fallback(tmp_path, monkeypatch):
    from audio_intel import video_detection
    from test_video_transcription import video_file
    source = tmp_path/'demo.webm'
    video_file(source)
    request = dict(source=str(source), cache=str(tmp_path/'scene-features'), settings=None,
                   roi=None, duration=8., time_origin=0., existing_frames=str(tmp_path/'none'),
                   mode='auto', video_scene_detail='balanced', min_free=0)
    scan = video_detection.source_samples
    calls = []

    def observed(*args):
        calls.append(args[2:4])
        return scan(*args)

    monkeypatch.setattr(video_detection, 'source_samples', observed)
    for i,(scope,level,cached) in enumerate([([1,4],'balanced',False),([1,4],'detailed',True),
                                              ([2,5],'concise',False),(None,'concise',False),
                                              ([1,4],'balanced',True),(None,'detailed',True)]):
        folder = tmp_path/str(i)
        folder.mkdir()
        before = len(calls)
        result = video_detection.preview(dict(request,time_range=scope,video_scene_detail=level),folder,lambda *args:None)
        assert result['cache_hit'] == cached
        assert len(calls)-before == int(not cached)
        assert result['detector'] == 'presentation-v7'
    index = tmp_path/video_auto_index.NAME
    payload = bytearray(index.read_bytes())
    payload[10] ^= 1
    index.write_bytes(payload)
    folder = tmp_path/'rebuild'
    folder.mkdir()
    assert not video_detection.preview(request,folder,lambda *args:None)['cache_hit']


def test_detail_section_limit_applies_per_level():
    from audio_intel.video_detail import DetailDetector
    detector = DetailDetector(6*3600)
    # Feed alternating stable, additive pictures into the selector without decoding media.
    a = slide().astype(np.int16)
    b = a.copy()
    b[220:232, 200:270] = 30
    detector.outputs['detailed'] = [dict(start=i,end=i+1,frame_time=i) for i in range(2000)]
    detector.anchor = a
    detector.frame_time = detector.start
    detector.flush(detector.start+2)
    detector.start += 2
    detector.anchor = b
    detector.frame_time = detector.start
    detector.flush(detector.start+2)
    assert detector.result('balanced')[-1]['end'] == 6*3600+4
    with pytest.raises(ValueError,match='2000'):
        detector.result('detailed')


def test_detail_contract_and_snapshot_identity(client,local):
    job = completed_video(client,local)
    url = f"/api/v1/jobs/{job['id']}/video-scene-previews"
    headers = {'Idempotency-Key':'detail-validation-01'}
    for body in ({'mode':'custom','video_scene_detail':'concise'}, {'settings':{},'video_scene_detail':'detailed'},
                 {'video_scene_detail':'unknown'}):
        assert client.post(url,headers=headers,json=dict(revision=1,**body)).status_code==422
    reply=client.post(url,headers=headers,json={'revision':1,'video_scene_detail':'concise'})
    assert reply.status_code==202
    ready=wait_preview(client,url+'/'+reply.json()['id'])
    assert ready['video_scene_detail']=='concise'
    assert ready['cache_source']=='auto_index'
    conflict=client.post(url,headers=headers,json={'revision':1,'video_scene_detail':'balanced'})
    assert conflict.status_code==409
    applied=client.patch(url.replace('video-scene-previews','video-scenes'),json={
        'revision':1,'action':'apply_detection','preview_id':ready['id']})
    assert applied.status_code==200
    assert applied.json()['video']['video_scene_detail']=='concise'


def test_reading_detector_preserves_complete_reveal_after_exit():
    from audio_intel.video_detail import ReadingDetector
    base = slide()
    reveal = base.copy()
    # Sparse lettering appears on a populated page, then exits.
    reveal[240:270:4, 300:510] = 0
    reader = ReadingDetector()
    for index, frame in enumerate([base]*8 + [reveal]*8 + [base]*8):
        reader.feed(frame, index * .5)
    reader.flush(12)
    for level in reader.outputs:
        assert reader.result(level)[-1]['frame_time'] == 4
        assert reader.result(level)[-1]['end'] == 12


def test_registration_rejects_a_new_chart_and_accepts_a_small_pan():
    from audio_intel.video_detail import same_viewport
    rng = np.random.default_rng(5)
    page = np.repeat(np.repeat(rng.uniform(0, 255, (68, 120)), 4, axis=0), 4, axis=1)[:270].astype(np.float32)
    assert same_viewport(page, np.roll(page, 4, axis=1))
    assert not same_viewport(page, rng.uniform(0, 255, page.shape))


def test_persisted_v5_keeps_its_selector_and_index_format(tmp_path, monkeypatch):
    from audio_intel.video_detail import ReadingDetector
    from audio_intel.video_frames import decode
    from test_video_transcription import video_file

    source = tmp_path/'legacy.webm'
    video_file(source)
    monkeypatch.setattr(ReadingDetector, 'feed', lambda *args: pytest.fail('selected the new detector'))
    class Context:
        def progress(self, *args, **kwargs):
            pass

    _, _, video = decode(source, tmp_path/'audio.wav', tmp_path/'frames', None, Context(),
                         detector_version='presentation-v5', scene_detail='concise')
    assert video['detector'] == 'presentation-v5' and video['video_scene_detail'] == 'concise'
    path = tmp_path/video_auto_index.NAME
    old, _ = video_auto_index.read(path, source, None, version=2)
    assert len(old['concise']) == len(video['scenes'])
    with pytest.raises(ValueError):
        video_auto_index.read(path, source, None, version=3)


def test_page_build_preserves_nested_frames_and_complete_state_on_exit():
    from audio_intel.video_detail import PageDetector

    page = slide()
    # A fixed title and two columns of text built in sequence.
    page[70:90:4, 95:310] = 20
    left = page.copy()
    left[220:285:6, 110:280] = 45
    right = left.copy()
    right[212:290:6, 610:760] = 55
    detector = PageDetector()
    for index, frame in enumerate([page]*8 + [left]*8 + [right]*8 + [left]*8):
        detector.feed(frame, .5*index)
    detector.finish(16)
    concise, balanced, detailed = [
        [s['frame_time'] for s in detector.result(level)]
        for level in ('concise', 'balanced', 'detailed')
    ]
    assert concise == [8]
    assert balanced == [4, 8]
    assert set(concise) <= set(balanced) <= set(detailed)
    assert all(rows[-1]['end'] == 16 for rows in detector.outputs.values())


def test_v6_jobs_keep_the_reading_detector_and_index():
    from audio_intel.video_detail import DETECTORS, INDEX_VERSIONS, ReadingDetector

    assert DETECTORS['presentation-v6'] is ReadingDetector
    assert INDEX_VERSIONS['presentation-v6'] == 3


def test_inset_charts_under_fixed_browser_chrome_use_viewport_detector(monkeypatch):
    from audio_intel.video_detail import PageDetector, ReadingDetector, structure

    window = slide()
    window[:, :250] = 20
    window[80:120:4, 120:470] = 110
    first = window.copy()
    first[220:380:9, 300:480] = 32
    second = window.copy()
    second[250:430:12, 520:780] = 65
    def picture(frame):
        plane, edges = structure(frame.astype(np.int16))
        return plane, edges, np.zeros_like(edges)
    called = []
    def compare(reader, old, new):
        called.append(True)
        return 0, True
    monkeypatch.setattr(ReadingDetector, 'compare', compare)
    assert PageDetector().compare(picture(first), picture(second)) == (0, True)
    assert called
