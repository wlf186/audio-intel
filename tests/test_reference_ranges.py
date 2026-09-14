from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid
import wave

import pytest

from audio_intel import db, reference_ranges as ranges
from audio_intel.worker import JobContext
from audio_intel.document_download import reader_lease
from audio_intel.waveforms import cache_path
from test_documents import document_client, import_document
import tts.pipeline as pipeline


def sample_fixture(local):
    person = db.create_voiceprint_person('Range test')
    path = local.voiceprints_dir / person['id'] / 'sample.wav'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each second carries a different PCM value so the crop start is observable.
    with wave.open(str(path), 'wb') as f:
        f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        for second in range(45):
            f.writeframes((second+1).to_bytes(2, 'little', signed=True) * 16000)
    words = [{'text':str(i), 'start':float(i), 'end':float(i+1)} for i in range(45)]
    sample = db.create_voiceprint_sample(person['id'], name='Reference', state='ready',
        audio_path=str(path), duration=45, language='Chinese',
        transcript=' '.join(str(i) for i in range(45)), words=words)
    return sample, path


def submit(client, kind, sample, fields, key=None, document=None):
    body = {'voice_mode':'voiceprint', 'compute_device':'cpu', 'voiceprint_sample_id':sample['id'], **fields}
    if kind == 'sequence':
        return client.post('/api/v1/tts/sequence-jobs', headers={'Idempotency-Key':key or uuid.uuid4().hex},
            json={'voice_mode':'voiceprint', 'compute_device':'cpu', 'items':[{'id':'one','text':'你好', 'voiceprint_sample_id':sample['id'], **fields}]})
    if kind == 'document':
        identifier, preview = document
        body.update(document_import_id=identifier, preview_revision=preview['preview_revision'], section_ids=[p['id'] for p in preview['sections']])
    else:
        body['text'] = '你好，区间测试。'
    return client.post('/api/v1/tts/'+('document-jobs' if kind=='document' else 'jobs'),
        data=body, headers={'Idempotency-Key':key or uuid.uuid4().hex})


@pytest.mark.parametrize('kind', ['single','document','sequence'])
def test_submission_snapshot_result_replay_and_retry(document_client, kind):
    client, local = document_client
    sample, source = sample_fixture(local)
    original = source.read_bytes()
    document = import_document(client) if kind=='document' else None
    fields = {'reference_start_seconds':10, 'reference_end_seconds':40}
    key = uuid.uuid4().hex
    response = submit(client,kind,sample,fields,key,document)
    assert response.status_code == 202, response.text
    job = response.json()
    persisted = copy.deepcopy(job['request'])
    result = pipeline.process_job(JobContext(db.get_job(job['id']), 'test'))
    usage = result['sequence']['items'][0] if kind=='sequence' else result
    assert usage['reference_start_seconds_used'] == 10
    assert usage['reference_end_seconds_used'] == 40
    assert usage['reference_duration_used'] == 30
    assert usage['reference_text_used'] == ' '.join(str(i) for i in range(10,40))
    assert source.read_bytes() == original
    assert db.get_job(job['id'])['request'] == persisted
    db.finish_job(job['id'],'succeeded',result_json=result)
    public = client.get(f"/api/v1/jobs/{job['id']}/result").json()
    assert (public['sequence']['items'][0] if kind=='sequence' else public)['reference_start_seconds_used'] == 10
    db.rename_voiceprint_sample(sample['person_id'],sample['id'],'Renamed')
    # Replay and retry remain independent of the current library, including deletion.
    source.unlink()
    assert submit(client,kind,sample,fields,key,document).status_code == 200
    assert submit(client,kind,sample,{**fields,'reference_end_seconds':39},key,document).status_code == 409
    db.update_job(job['id'],state='failed')
    assert client.post(f"/api/v1/jobs/{job['id']}/retry").status_code == 200
    assert db.get_job(job['id'])['request'] == persisted
    retry = pipeline.process_job(JobContext(db.get_job(job['id']), 'test'))
    assert (retry['sequence']['items'][0] if kind=='sequence' else retry)['reference_text_used'] == usage['reference_text_used']


@pytest.mark.parametrize('kind', ['single','sequence','document'])
@pytest.mark.parametrize('fields', [
    {'reference_start_seconds':1}, {'reference_end_seconds':10},
    {'reference_start_seconds':-1,'reference_end_seconds':9},
    {'reference_start_seconds':10,'reference_end_seconds':12.99},
    {'reference_start_seconds':10,'reference_end_seconds':40.01},
    {'reference_start_seconds':40,'reference_end_seconds':46},
    {'reference_start_seconds':8,'reference_end_seconds':3},
])
def test_invalid_ranges_rejected_without_job_or_files(document_client,kind,fields):
    client,local=document_client
    sample,_=sample_fixture(local)
    document=import_document(client) if kind=='document' else None
    before=set(local.jobs_dir.rglob('*'))
    response=submit(client,kind,sample,fields,document=document)
    assert response.status_code==422,response.text
    assert set(local.jobs_dir.rglob('*'))==before


def test_default_compatibility_boundaries_and_cross_mode(document_client):
    client,local=document_client
    sample,_=sample_fixture(local)
    default=submit(client,'single',sample,{}).json()
    assert not any(k in default['request'] for k in ranges.RANGE_FIELDS)
    output=pipeline.process_job(JobContext(db.get_job(default['id']),'test'))
    assert output['reference_duration_used']==15
    for fields in ({'reference_start_seconds':2,'reference_end_seconds':5}, {'reference_start_seconds':15,'reference_end_seconds':45}):
        assert submit(client,'single',sample,fields).status_code==202
    for mode in ('preset','profile','inline_clone','voice_design'):
        response=client.post('/api/v1/tts/jobs',headers={'Idempotency-Key':uuid.uuid4().hex},data={'text':'Test','voice_mode':mode,'speaker':'Vivian','compute_device':'cpu','reference_start_seconds':1,'reference_end_seconds':5})
        assert response.status_code==422,response.text
    for value in (True,'10',float('inf'),float('nan')):
        if isinstance(value,float):
            response=client.post('/api/v1/tts/jobs',headers={'Idempotency-Key':uuid.uuid4().hex},data={'text':'x','voice_mode':'voiceprint','voiceprint_sample_id':sample['id'],'compute_device':'cpu','reference_start_seconds':str(value),'reference_end_seconds':10})
        else:
            response=submit(client,'sequence',sample,{'reference_start_seconds':value,'reference_end_seconds':10})
        assert response.status_code==422,response.text


def test_middle_crop_pcm_and_transcript_and_alignment_fallback(document_client, monkeypatch):
    _,local=document_client
    sample,path=sample_fixture(local)
    request={'voice_mode':'voiceprint','reference_audio_path':str(path),'reference_text':sample['transcript'],
             'reference_words':sample['words'],'reference_start_seconds':10.2,'reference_end_seconds':20.8,'language':'Chinese'}
    work=local.temp_dir/'crop';work.mkdir(parents=True)
    pipeline._prepare_clone_reference(SimpleNamespace(work_dir=work),request,'cpu')
    assert request['reference_start_seconds_used']==11
    assert request['reference_end_seconds_used']==20
    with wave.open(request['reference_audio_path']) as f:
        assert f.getnframes()/f.getframerate()==9
        assert int.from_bytes(f.readframes(1),'little',signed=True)==12
    calls=[]
    def align(*args):
        calls.append(args)
        return sample['words']
    monkeypatch.setattr(pipeline,'_align_reference',align)
    request.update(reference_audio_path=str(path),reference_text=sample['transcript'],reference_words=[])
    pipeline._prepare_clone_reference(SimpleNamespace(work_dir=work),request,'cpu')
    assert len(calls)==1
    assert request['reference_text']==' '.join(str(i) for i in range(11,20))


def test_repeated_words_punctuation_and_invalid_alignment():
    words=[{'text':'Hello','start':0,'end':2},{'text':'Hello','start':3,'end':5},{'text':'世界','start':6,'end':8}]
    assert ranges.resolve('Hello! Hello, 世界。',words,3,8,8)==(3,8,'Hello, 世界')
    with pytest.raises(ValueError):ranges.resolve('wrong',words,3,8,8)
    with pytest.raises(ValueError):ranges.resolve('Hello! Hello, 世界。',words,2.5,5.5,8)


def test_sequence_distinct_ranges_share_only_original_snapshot(document_client,monkeypatch):
    client,local=document_client
    sample,_=sample_fixture(local)
    items=[{'id':str(i),'text':'区间测试。','voiceprint_sample_id':sample['id'],'reference_start_seconds':start,'reference_end_seconds':end} for i,(start,end) in enumerate([(0,15),(10,40),(0,15)])]
    response=client.post('/api/v1/tts/sequence-jobs',headers={'Idempotency-Key':uuid.uuid4().hex},json={'voice_mode':'voiceprint','compute_device':'cpu','items':items})
    assert response.status_code==202,response.text
    job=response.json()
    assert len(job['request']['voiceprint_references'])==1
    prepared=[]
    original=pipeline._prepare_clone_reference
    def prepare(*args,**kwargs):
        original(*args,**kwargs)
        prepared.append(args[1].copy())
    monkeypatch.setattr(pipeline,'_prepare_clone_reference',prepare)
    result=pipeline.process_job(JobContext(db.get_job(job['id']),'test'))
    assert len(prepared)==2
    assert prepared[0]['reference_audio_path']!=prepared[1]['reference_audio_path']
    assert [i['reference_start_seconds_used'] for i in result['sequence']['items']]==[0,10,0]
    assert [i['reference_duration_used'] for i in result['sequence']['items']]==[15,30,15]


def test_sample_waveform_cache_and_purge_guard(document_client):
    client,local=document_client
    sample,path=sample_fixture(local)
    url=f"/api/v1/voiceprints/samples/{sample['id']}/audio/waveform"
    value=client.get(url)
    assert value.status_code==200,value.text
    assert value.json()['duration']==45
    assert len(value.json()['waveform'])==240
    assert cache_path(path,path.parent/"waveforms").exists()
    delete=f"/api/v1/voiceprints/people/{sample['person_id']}/samples/{sample['id']}?purge=true"
    with reader_lease('voiceprint:'+sample['id']):
        assert client.delete(delete).status_code==409
        assert path.exists() and db.get_voiceprint_sample(sample['id'])
    assert client.delete(delete).status_code==204
    assert not path.exists() and not cache_path(path,path.parent/"waveforms").exists()
    assert client.get(url).status_code==404


def test_waveform_auth_and_public_range_contract(document_client,monkeypatch):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    import audio_intel.api as api
    _,local=document_client
    sample,_=sample_fixture(local)
    monkeypatch.setattr(api,'settings',replace(local,api_key='range-test-key'))
    with TestClient(api.create_app()) as client:
        url=f"/api/v1/voiceprints/samples/{sample['id']}/audio/waveform"
        assert client.get(url).status_code==401
        headers={'Authorization':'Bearer range-test-key'}
        assert client.get(url,headers=headers).status_code==200
        assert client.post('/api/v1/auth/session',headers=headers).status_code==204
        assert client.get(url).status_code==200
        assert client.get(url.removesuffix('/waveform'),headers={'Range':'bytes=0-3'}).status_code==206
        schema=client.get('/openapi.json').json()
        for endpoint in ('jobs','document-jobs'):
            ref=schema['paths']['/api/v1/tts/'+endpoint]['post']['requestBody']['content']['multipart/form-data']['schema']['$ref'].split('/')[-1]
            assert set(ranges.RANGE_FIELDS)<=set(schema['components']['schemas'][ref]['properties'])
        assert set(ranges.RANGE_FIELDS)<=set(schema['components']['schemas']['TtsSequenceItem']['properties'])
        caps=client.get('/api/v1/capabilities').json()
        assert caps['limits']['max_clone_reference_seconds']==15
        for model in caps['tts']['model_capabilities']:
            assert model['controls']['reference_range']==ranges.capability()
        assert client.delete('/api/v1/auth/session').status_code==204
        assert client.get(url).status_code==401


def test_decimal_boundaries_and_duplicate_form_fields(document_client):
    from urllib.parse import urlencode
    ranges.validate(0.2,3.2,45)
    ranges.validate(0.2,30.2,45)
    client,local=document_client
    sample,_=sample_fixture(local)
    response=client.post('/api/v1/tts/jobs',headers={'Idempotency-Key':uuid.uuid4().hex,'Content-Type':'application/x-www-form-urlencoded'},content=urlencode([('text','test'),('voice_mode','voiceprint'),('voiceprint_sample_id',sample['id']),('compute_device','cpu'),('reference_start_seconds',0),('reference_start_seconds',1),('reference_end_seconds',15)]))
    assert response.status_code==422,response.text


def test_sequence_missing_alignment_computed_once_without_library_write(document_client,monkeypatch):
    client,local=document_client
    sample,_=sample_fixture(local)
    db.update_voiceprint_sample(sample['id'],words_json=[])
    items=[{'id':str(i),'text':'样本对齐验证。','voiceprint_sample_id':sample['id'],'reference_start_seconds':start,'reference_end_seconds':end} for i,(start,end) in enumerate([(0,15),(10,40)])]
    response=client.post('/api/v1/tts/sequence-jobs',headers={'Idempotency-Key':uuid.uuid4().hex},json={'voice_mode':'voiceprint','compute_device':'cpu','items':items})
    assert response.status_code==202,response.text
    calls=[]
    def align(*args):
        calls.append(args)
        return sample['words']
    monkeypatch.setattr(pipeline,'_align_reference',align)
    job=db.get_job(response.json()['id'])
    result=pipeline.process_job(JobContext(job,'test'))
    assert len(calls)==1
    assert [i['reference_duration_used'] for i in result['sequence']['items']]==[15,30]
    assert db.get_voiceprint_sample(sample['id'])['words']==[]


@pytest.mark.parametrize('invalid', ['backwards','nonfinite','text'])
def test_default_crop_validates_existing_alignment_once(document_client,monkeypatch,invalid):
    _,local=document_client
    sample,path=sample_fixture(local)
    words=copy.deepcopy(sample['words'])
    if invalid=='backwards':words[10]['start']=0
    elif invalid=='nonfinite':words[10]['end']=float('nan')
    else:words[10]['text']='not in transcript'
    request={'voice_mode':'voiceprint','voiceprint_sample_id':sample['id'],
             'reference_audio_path':str(path),'reference_text':sample['transcript'],
             'reference_words':words,'language':'Chinese'}
    work=local.temp_dir/'default-validation';work.mkdir(parents=True)
    calls=[]
    def align(*args):
        calls.append(args)
        return copy.deepcopy(sample['words'])
    monkeypatch.setattr(pipeline,'_align_reference',align)
    pipeline._prepare_clone_reference(SimpleNamespace(work_dir=work),request,'cpu')
    assert len(calls)==1 and request['reference_duration_used']==15
    assert request['reference_text']==' '.join(str(i) for i in range(15))
    assert db.get_voiceprint_sample(sample['id'])['words']==sample['words']


def test_default_crop_refuses_crossing_overlapping_word(document_client):
    _,local=document_client
    sample,path=sample_fixture(local)
    words=copy.deepcopy(sample['words'])
    words[15]['start']=14.5
    request={'voice_mode':'voiceprint','reference_audio_path':str(path),'reference_text':sample['transcript'],
             'reference_words':words,'language':'Chinese'}
    work=local.temp_dir/'overlap';work.mkdir(parents=True)
    with pytest.raises(ValueError,match='overlapping'):
        pipeline._prepare_clone_reference(SimpleNamespace(work_dir=work),request,'cpu')
    assert not (work/'clone-reference.wav').exists()
