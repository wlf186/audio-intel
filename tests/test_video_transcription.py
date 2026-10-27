from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from audio_intel import api, db, purge, worker, video_uploads
from audio_intel.config import settings
from audio_intel.transcript import paragraphs, scene_transcripts
from audio_intel.transcript_pdf import download
from audio_intel.video_frames import PresentationDetector, probe
from asr import pipeline


def video_file(path: Path) -> bytes:
    # Browser fixtures use open codecs; MP4 remains covered by backend tests.
    webm = path.suffix == '.webm'
    sample_rate = 48000 if webm else 16000
    samples_per_frame = sample_rate // 10
    with av.open(str(path), 'w') as output:
        video = output.add_stream('libvpx' if webm else 'libx264', rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, 'yuv420p'
        audio = output.add_stream('libopus' if webm else 'aac', rate=sample_rate)
        audio.layout = 'mono'
        for i in range(60):
            picture = np.full((180, 320, 3), 245, dtype=np.uint8)
            if i < 30:
                picture[25:40, 20:230] = (20, 60, 170)
                picture[70:85, 30:150] = (10, 20, 30)
            else:
                picture[95:130, 130:290] = (180, 30, 20)
                picture[35:90, 200:220] = (10, 20, 30)
            frame = av.VideoFrame.from_ndarray(picture, format='rgb24')
            frame.pts, frame.time_base = i, Fraction(1, 10)
            for packet in video.encode(frame): output.mux(packet)
            sound = av.AudioFrame.from_ndarray(np.zeros((1, samples_per_frame), dtype=np.float32), format='flt' if webm else 'fltp', layout='mono')
            sound.sample_rate, sound.pts, sound.time_base = sample_rate, i*samples_per_frame, Fraction(1,sample_rate)
            for packet in audio.encode(sound): output.mux(packet)
        for packet in video.encode(): output.mux(packet)
        for packet in audio.encode(): output.mux(packet)
    return path.read_bytes()


@pytest.fixture
def local(tmp_path, monkeypatch):
    value = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp',
                    mock_mode=True, api_key='video-test', min_free_disk_bytes=0,
                    video_upload_chunk_bytes=8192)
    for module in (api, db, purge, worker, pipeline): monkeypatch.setattr(module, 'settings', value)
    return value


@pytest.fixture
def client(local):
    with TestClient(api.create_app(), headers={'Authorization':'Bearer video-test'}) as value:
        yield value


def upload(client, data, name='lecture.mp4', key='upload-key-0001'):
    response = client.post('/api/v1/asr/video-uploads', json={'name':name,'size_bytes':len(data)},headers={'Idempotency-Key':key})
    assert response.status_code == 201, response.text
    item=response.json()
    url='/api/v1/asr/video-uploads/'+item['id']
    for start in range(0,len(data),item['chunk_bytes']):
        block=data[start:start+item['chunk_bytes']]
        response=client.patch(url,content=block,headers={'Upload-Offset':str(start),'Upload-Checksum':hashlib.sha256(block).hexdigest(),'Content-Type':'application/octet-stream'})
        assert response.status_code==200,response.text
    return response.json()


def test_pdf_lossless_merge_and_chinese(tmp_path):
    phrases=['觉得都特别在意，我我我，反正觉得文采。','知识工程的，知识工程里面呢，知识里面。','然后代码里面也有知识工程。']
    segments=[{'id':i,'speaker':'s0','speaker_label':'张老师','start':i*2,'end':i*2+2,'text':text,'words':[]} for i,text in enumerate(phrases)]
    merged=paragraphs(segments)
    assert len(merged)==1 and merged[0]['text']==''.join(phrases)
    assert (merged[0]['start'],merged[0]['end'])==(0,6)
    segments.append({**segments[0],'id':3,'speaker':'s1','start':6,'end':7,'text':'另一位同名人员。'})
    assert len(paragraphs(segments))==2
    payload=download('中文会议',{'segments':segments},tmp_path)
    text=''.join(p.extract_text() for p in PdfReader(io.BytesIO(payload)).pages)
    assert '张老师' in text and '我我我' in text and '知识工程' in text
    assert not list(tmp_path.iterdir())


def test_scene_partition_preserves_each_word_and_fallback():
    result={'video':{'scenes':[{'id':'a','start':0,'end':2},{'id':'b','start':2,'end':6}]},'segments':[
        {'id':0,'speaker':'s0','speaker_label':'人','start':0,'end':4,'text':'你好。再见。','words':[{'text':'你好','start':0,'end':1},{'text':'再见','start':3,'end':4}]},
        {'id':1,'speaker':'s0','speaker_label':'人','start':4,'end':6,'text':'没有逐字。','words':[]}]}
    scenes=scene_transcripts(result)
    assert [s['paragraphs'][0]['text'] for s in scenes]==['你好。','再见。没有逐字。']


def test_upload_resume_checksum_replay_restart_and_expiry(client,local):
    data=b'x'*8192+b'y'*15
    created=client.post('/api/v1/asr/video-uploads',json={'name':'meeting.mp4','size_bytes':len(data)},headers={'Idempotency-Key':'resume-key-0001'}).json()
    url='/api/v1/asr/video-uploads/'+created['id']
    headers={'Upload-Offset':'0','Upload-Checksum':hashlib.sha256(data[:8192]).hexdigest()}
    assert client.patch(url,content=b'bad',headers=headers).status_code==413
    assert client.patch(url,content=b'z'*8192,headers=headers).status_code==422
    assert client.get(url).json()['offset']==0
    assert client.patch(url,content=data[:8192],headers=headers).json()['offset']==8192
    partial,_=video_uploads.paths(local,video_uploads.get(created['id']))
    before=partial.stat().st_mtime_ns
    assert client.patch(url,content=data[:8192],headers=headers).json()['offset']==8192
    assert partial.stat().st_mtime_ns==before
    with partial.open('ab') as f:f.write(b'unconfirmed crash tail')
    with TestClient(api.create_app(),headers={'Authorization':'Bearer video-test'}) as other:
        assert other.head(url).headers['upload-offset']=='8192'
        assert other.patch(url,content=data[8192:],headers={'Upload-Offset':'8192','Upload-Checksum':hashlib.sha256(data[8192:]).hexdigest()}).status_code==200
    assert partial.read_bytes()==data
    with db.connect() as c:c.execute('UPDATE video_uploads SET expires_at=? WHERE id=?',(time.time()-1,created['id']))
    assert client.get(url).status_code==410
    assert video_uploads.discard(local,created['id'],expired_only=True)
    assert not partial.parent.parent.exists()


def test_video_end_to_end_edit_pdf_and_idempotency(client,local,tmp_path):
    data=video_file(tmp_path/'input.mp4')
    assert probe(tmp_path/'input.mp4')['width']==320
    item=upload(client,data)
    body={'upload_id':item['id'],'compute_device':'cpu','use_voiceprint_library':False}
    headers={'Idempotency-Key':'video-job-0001'}
    response=client.post('/api/v1/asr/video-jobs',json=body,headers=headers)
    assert response.status_code==202,response.text
    job=response.json();assert job['purpose']=='video_transcription'
    assert job['request']['audio_enhancement']=='auto'
    assert client.post('/api/v1/asr/video-jobs',json=body,headers=headers).status_code==200
    assert client.post('/api/v1/asr/video-jobs',json={**body,'align':False},headers=headers).status_code==409
    assert client.post('/api/v1/asr/video-jobs',json={**body,'audio_enhancement':'off'},headers=headers).status_code==409
    assert not (local.jobs_dir/job['id']/'input/video.partial').exists()
    claimed=db.claim_job('asr','video-test-worker')
    assert claimed['id']==job['id']
    outcome=worker._run_one_job('asr',job['id'],'video-test-worker',1,pipeline.process_job)
    result=db.get_job(job['id'])
    assert outcome['state']=='succeeded',result.get('error_message')
    video=result['result']['video'];assert len(video['scenes'])==2
    root='/api/v1/jobs/'+job['id']
    pdf = client.get(root+'/transcript.pdf')
    assert pdf.content.startswith(b'%PDF')
    assert pdf.headers['content-type'] == 'application/pdf'
    head = client.head(root+'/transcript.pdf')
    assert head.content == b''
    for header in ('content-type', 'content-length', 'content-disposition'):
        assert head.headers[header] == pdf.headers[header]
    assert client.get(root+'/video-scenes').json()['revision']==1
    image=video['scenes'][0]['image']
    frame = client.get(root+'/video-frames/'+image)
    assert frame.status_code == 200 and frame.headers['content-type'] == 'image/jpeg'
    preview = client.get(root+'/video-preview?time=1')
    assert preview.status_code == 200 and preview.headers['content-type'] == 'image/jpeg'
    response=client.patch(root+'/video-scenes',json={'revision':1,'action':'merge_next','scene_id':video['scenes'][0]['id']})
    assert response.status_code==200,response.text
    assert len(response.json()['video']['scenes'])==1
    assert client.patch(root+'/video-scenes',json={'revision':1,'action':'restore'}).status_code==409
    response=client.patch(root+'/video-scenes',json={'revision':2,'action':'restore'})
    assert len(response.json()['video']['scenes'])==2
    assert 'result' not in client.get('/api/v1/jobs').json()['items'][0]
    assert client.delete('/api/v1/asr/video-uploads/'+item['id']).status_code==409
    assert not list((local.jobs_dir/job['id']).rglob('*.partial'))


def test_limits_auth_schema_and_cancel(client,local):
    assert client.post('/api/v1/asr/video-uploads',json={'name':'x.mp4','size_bytes':25*1024**3+1},headers={'Idempotency-Key':'oversized-0001'}).status_code==413
    assert client.post('/api/v1/asr/video-uploads',json={'name':'x.mp4','size_bytes':4,'unknown':1},headers={'Idempotency-Key':'strict-body-0001'}).status_code==422
    item=upload(client,b'data',key='cancel-upload-0001')
    url='/api/v1/asr/video-uploads/'+item['id']
    assert client.get(url,headers={'Authorization':'Bearer wrong'}).status_code==401
    assert client.delete(url).status_code==204
    assert not (local.jobs_dir/item['id']).exists()
    with db.connect() as c:assert c.execute('SELECT MIN(version) FROM schema_meta').fetchone()[0]==16


def test_slide_builds_roi_and_repeated_page(tmp_path):
    from PIL import Image
    from audio_intel.video_frames import crop_picture
    first=np.full((180,320,3),245,dtype=np.uint8)
    first[20:35,20:190]=(20,40,160)
    built=first.copy();built[70:85,20:220]=(25,25,25)
    second=np.full_like(first,245);second[40:140,180:220]=(170,30,40)
    detector=PresentationDetector(tmp_path/'frames')
    frames=[first]*4+[built]*4+[second]*4+[built]*4
    for index,frame in enumerate(frames):detector.feed(Image.fromarray(frame),index*.5)
    detector.flush(8)
    assert len(detector.scenes)==3
    assert [s['start'] for s in detector.scenes]==[0,4,6]
    assert detector.scenes[0]['image']==detector.scenes[2]['image']
    assert detector.scenes[0]['frame_time']==3.5
    # A moving speaker inset outside a user-selected PPT region cannot create cuts.
    detector=PresentationDetector(tmp_path/'roi')
    for index in range(12):
        frame=built.copy();frame[:,250:]=(index*21,30,50)
        picture=crop_picture(av.VideoFrame.from_ndarray(frame,format='rgb24'),{'x':0,'y':0,'width':.75,'height':1})
        detector.feed(picture,index*.5)
    detector.flush(6)
    assert len(detector.scenes)==1
    assert len(list((tmp_path/'roi').iterdir()))==1


def test_reservations_boundary_and_seven_day_lifetime(client,local,monkeypatch):
    from collections import namedtuple
    usage=namedtuple('usage','total used free')
    monkeypatch.setattr(video_uploads.shutil,'disk_usage',lambda _:usage(60*1024**3,0,30*1024**3))
    response=client.post('/api/v1/asr/video-uploads',json={'name':'max.mp4','size_bytes':25*1024**3},headers={'Idempotency-Key':'boundary-video-0001'})
    assert response.status_code==201
    maximum=response.json()
    assert video_uploads.remaining_bytes()==25*1024**3
    assert not (local.jobs_dir/maximum['id']).exists()  # no preallocation or temporary full-size copy
    response=client.post('/api/v1/asr/video-uploads',json={'name':'too-many.mp4','size_bytes':6*1024**3},headers={'Idempotency-Key':'reservation-video-0001'})
    assert response.status_code==429 and response.headers['Retry-After']=='300'
    assert client.delete('/api/v1/asr/video-uploads/'+maximum['id']).status_code==204
    now=time.time()
    monkeypatch.setattr(video_uploads.time,'time',lambda:now)
    created=client.post('/api/v1/asr/video-uploads',json={'name':'week.mp4','size_bytes':10},headers={'Idempotency-Key':'lifetime-video-0001'}).json()
    with db.connect() as c:c.execute('UPDATE video_uploads SET created_at=? WHERE id=?',(now-7*86400+60,created['id']))
    block=b'x'*10
    response=client.patch('/api/v1/asr/video-uploads/'+created['id'],content=block,headers={'Upload-Offset':'0','Upload-Checksum':hashlib.sha256(block).hexdigest()})
    assert response.status_code==200
    assert response.json()['expires_at']==pytest.approx(now+60)


def test_failed_decode_cleans_task_temporary_files(client,local):
    # Exercise the worker cleanup contract for a damaged persisted video request.
    job=db.create_job('asr','broken.mp4',{'purpose':'video_transcription','input_path':str(local.data_dir/'broken.mp4'),'model':'qwen3-asr-0.6b','compute_device':'cpu'})
    (local.data_dir/'broken.mp4').write_bytes(b'not a video')
    db.claim_job('asr','broken-worker')
    work=local.temp_dir/job['id'];work.mkdir(parents=True);(work/'frames').mkdir();(work/'frames'/'interrupted.jpg.partial').write_bytes(b'x')
    outcome=worker._run_one_job('asr',job['id'],'broken-worker',1,pipeline.process_job)
    assert outcome['state']=='failed'
    assert not work.exists()


def test_video_openapi_contract(client):
    schema=client.get('/openapi.json').json()
    body=schema['components']['schemas']['VideoJobRequest']
    assert body['additionalProperties'] is False
    assert body['properties']['accelerate_single_task']['default'] is True
    enrollment=schema['paths']['/api/v1/voiceprints/people/{person_id}/samples/from-asr']['post']
    assert 'playback.wav' in enrollment['description']
    example=enrollment['requestBody']['content']['application/json']['examples']['video_sentences']['value']
    assert example=={'job_id':'video_example','segment_ids':[0,2]}
    upload=schema['paths']['/api/v1/asr/video-uploads']['post']
    assert upload['security']==[{'BearerAuth':[]},{'SessionCookie':[]}]
    assert upload['responses']['201']['content']['application/json']['schema']['$ref'].endswith('VideoUploadResponse')
    assert client.get('/api/v1/capabilities').json()['asr']['video']['max_upload_bytes']==25*1024**3


def test_variable_frame_pts_and_delayed_audio_share_origin(tmp_path, client, local):
    from audio_intel.video_frames import decode, picture_at
    source=tmp_path/'offsets.mkv'
    with av.open(str(source),'w') as output:
        video=output.add_stream('ffv1',rate=10)
        video.width,video.height,video.pix_fmt=320,180,'yuv420p'
        sound=output.add_stream('pcm_s16le',rate=16000);sound.layout='mono'
        for milliseconds in (2000,2400,3100,3800):
            plane=np.full((180,320,3),240,dtype=np.uint8)
            if milliseconds<3100:plane[20:40,10:180]=30
            else:plane[90:150,180:230]=30
            frame=av.VideoFrame.from_ndarray(plane,format='rgb24');frame.pts=milliseconds;frame.time_base=Fraction(1,1000)
            for packet in video.encode(frame):output.mux(packet)
        audio=av.AudioFrame.from_ndarray(np.full((1,16000),10000,dtype=np.int16),format='s16',layout='mono')
        audio.sample_rate,audio.pts,audio.time_base=16000,48000,Fraction(1,16000)
        for packet in sound.encode(audio):output.mux(packet)
        for packet in video.encode():output.mux(packet)
        for packet in sound.encode():output.mux(packet)
    class Context:
        def progress(self,*args,**kwargs):pass
    values,rate,result=decode(source,tmp_path/'normalized.wav',tmp_path/'frames',None,Context())
    assert result['time_origin']==pytest.approx(2)
    assert np.max(np.abs(values[:15990]))==0
    assert float(np.mean(values[16100:17000]))>.25
    assert result['scenes'][1]['start']==pytest.approx(1.1)
    assert result['duration']==pytest.approx(2)
    assert picture_at(source,1.1,None,result['time_origin']).size==(320,180)

    # Enrollment must use the same normalized timeline, not the original PTS.
    job=db.create_job('asr','offsets.mkv',{'purpose':'video_transcription','input_path':str(source)})
    output=local.jobs_dir/job['id']/'output'
    output.mkdir(parents=True)
    (tmp_path/'normalized.wav').replace(output/'playback.wav')
    segments=[{'id':i,'start':1.1,'end':1.6,'speaker':'s0','speaker_label':'讲者','text':'测试',
               'words':[{'text':'测试','start':1.2,'end':1.5}]} for i in range(2)]
    db.update_job(job['id'],state='succeeded',result_json={'video':result,'segments':segments,'language':'Chinese'})
    person=client.post('/api/v1/voiceprints/people',json={'name':'视频讲者'}).json()
    url=f"/api/v1/voiceprints/people/{person['id']}/samples/from-asr"
    response=client.post(url,json={'job_id':job['id'],'segment_ids':[0,0]})
    assert response.status_code==201,response.text
    sample=response.json()['items'][0]
    assert len(response.json()['items'])==1
    assert sample['duration']==pytest.approx(.5)
    assert sample['words']==[{'text':'测试','start':.1,'end':.4}]
    assert sample['transcript']=='测试'
    import soundfile as sf
    sound,rate=sf.read(io.BytesIO(client.get(sample['audio_url']).content))
    assert rate==16000 and len(sound)==8000 and float(np.mean(sound))>.25
    assert client.post(url,json={'job_id':job['id'],'segment_ids':[0]}).status_code==409
    (output/'playback.wav').unlink()
    missing=client.post(url,json={'job_id':job['id'],'segment_ids':[1]})
    assert missing.status_code==404 and 'playback' in missing.json()['detail'].lower()


def test_pdf_resource_budget(tmp_path,monkeypatch):
    from audio_intel import transcript_pdf
    monkeypatch.setattr(transcript_pdf,'_max_text_chars',2)
    with pytest.raises(ValueError,match='budget'):
        transcript_pdf.render('large',{'segments':[{'text':'too long'}]},tmp_path)


@pytest.mark.parametrize("gain,noise", [(False, False), (True, False), (False, True), (True, True)])
def test_video_independent_controls(client, local, tmp_path, gain, noise):
    item = upload(client, video_file(tmp_path / 'independent.mp4'))
    body = {'upload_id': item['id'], 'compute_device': 'cpu', 'use_voiceprint_library': False,
            'speech_enhancement': gain, 'background_denoise': noise}
    headers = {'Idempotency-Key': 'independent-video-controls-01'}
    first = client.post('/api/v1/asr/video-jobs', json=body, headers=headers)
    assert first.status_code == 202
    saved = first.json()['request']
    assert saved['speech_enhancement'] is gain and saved['background_denoise'] is noise
    assert client.post('/api/v1/asr/video-jobs', json=body, headers=headers).status_code == 200
    assert client.post('/api/v1/asr/video-jobs', json={**body, 'background_denoise': not noise}, headers=headers).status_code == 409
    assert client.post('/api/v1/asr/video-jobs', json={**body, 'audio_enhancement': 'auto'}, headers=headers).status_code == 422


def test_video_enhancement_historical_replay_and_invalid_mode(client, local, tmp_path):
    item = upload(client, video_file(tmp_path / 'historical.mp4'))
    body = {'upload_id': item['id'], 'compute_device': 'cpu', 'use_voiceprint_library': False}
    headers = {'Idempotency-Key': 'historical-video-enhancement-01'}
    first = client.post('/api/v1/asr/video-jobs', json=body, headers=headers)
    assert first.status_code == 202
    job = db.get_job(first.json()['id'])
    request = job['request']
    request.pop('audio_enhancement')
    request['video_caller'].pop('audio_enhancement')
    for field in ('speech_enhancement', 'background_denoise', 'enhancement_policy_version', 'denoise_backend', 'speech_enhancement_strength', 'background_denoise_strength'):
        request.pop(field, None)
        request['video_caller'].pop(field, None)
    with db.connect() as connection:
        connection.execute('UPDATE jobs SET request_json=? WHERE id=?', (json.dumps(request), job['id']))
        connection.execute('UPDATE job_idempotency SET request_hash=? WHERE job_id=?',
                           (api.request_fingerprint(request['video_caller']), job['id']))
    before = db.get_job(job['id'])
    replay = client.post('/api/v1/asr/video-jobs', json=body, headers=headers)
    assert replay.status_code == 200
    assert db.get_job(job['id']) == before
    assert 'audio_enhancement' not in replay.json()['request']
    assert client.post('/api/v1/asr/video-jobs', json={**body, 'audio_enhancement': 'auto'}, headers=headers).status_code == 409
    assert client.post('/api/v1/asr/video-jobs', json={**body, 'audio_enhancement': 'invalid'}, headers=headers).status_code == 422


def test_video_strength_snapshot_validation_and_replay(client, local, tmp_path):
    item = upload(client, video_file(tmp_path / 'strength.mp4'))
    body = {'upload_id': item['id'], 'compute_device': 'cpu', 'use_voiceprint_library': False,
            'audio_enhancement': 'auto', 'speech_enhancement_strength': 1.5, 'background_denoise_strength': .75}
    headers = {'Idempotency-Key': 'video-strength-001'}
    for field, value in [('speech_enhancement_strength', -1), ('speech_enhancement_strength', 2.01),
                         ('background_denoise_strength', 1.01), ('background_denoise_strength', -1)]:
        assert client.post('/api/v1/asr/video-jobs', json={**body, field: value}, headers=headers).status_code == 422
    response = client.post('/api/v1/asr/video-jobs', json=body, headers=headers)
    assert response.status_code == 202, response.text
    request = response.json()['request']
    for saved in (request, request['video_caller']):
        assert saved['enhancement_policy_version'] == 6
        assert saved['speech_enhancement_strength'] == 1.5 and saved['background_denoise_strength'] == .75
    replay_body = {k: v for k, v in body.items() if not k.endswith('_strength')}
    assert client.post('/api/v1/asr/video-jobs', json=replay_body, headers=headers).status_code == 200
    assert client.post('/api/v1/asr/video-jobs', json={**body, 'background_denoise_strength': 0}, headers=headers).status_code == 409
