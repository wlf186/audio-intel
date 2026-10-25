from __future__ import annotations

import asyncio
from copy import deepcopy
import io
import json
import os
import subprocess
import sys
import time

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
import pytest

from audio_intel import api, db, optimization_api as routes, optimization_engine as engine, optimization_store as store, purge
from test_voiceprint_groups_exports import local


PROFILE = {'base_url': 'http://ollama.invalid:11434', 'use_proxy': False, 'model': 'test-model', 'enabled': True,
           'context_length': 32768, 'model_context_length': 131072, 'model_digest': 'test-digest', 'prompt_version': 1, 'verified_at': '2026-10-23T00:00:00+00:00'}


def source(count=8):
    return {'language': 'Chinese', 'duration': count * 3, 'speakers': [
        {'id': 's1', 'name': 'Mike Smith', 'label': 'Mike Smith'}, {'id': 's2', 'name': 'Lucy Lee', 'label': 'Lucy Lee'}],
        'segments': [{'id': i, 'speaker': 's1' if i % 2 == 0 else 's2', 'speaker_label': 'Mike Smith' if i % 2 == 0 else 'Lucy Lee',
                      'start': i * 3, 'end': (i + 1) * 3, 'text': f'那个第{i}项，阿特拉斯预算15万元，不是50万元。暂时没有确认，周五先测试。'} for i in range(count)]}


def job(result=None):
    value = db.create_job('asr', '会议.wav', {'hotword_lists': [{'id': 'old', 'name': '历史热词', 'terms': ['Atlas']}]})
    db.update_job(value['id'], state='succeeded', result_json=result or source())
    return value['id']


def url(identifier):
    return f'/api/v1/jobs/{identifier}/transcript-optimization'


def wait(client, identifier, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(url(identifier))
        assert response.status_code == 200, response.text
        value = response.json()
        if predicate(value):
            return value
        time.sleep(.025)
    raise AssertionError(value)


@pytest.fixture
def configured(local, monkeypatch):
    store.save_settings(PROFILE)
    monkeypatch.setattr(purge, 'settings', local)
    async def check(self, profile):
        assert profile['model_digest'] == PROFILE['model_digest']
    async def generate(profile, batch, hotwords, before, after, activity):
        assert hotwords == ['Atlas']
        return [(p['ordinal'], p['original_text'].replace('那个', '').replace('阿特拉斯', 'Atlas')) for p in batch]
    monkeypatch.setattr(routes.Manager, 'check_model', check)
    monkeypatch.setattr(engine, 'generate', generate)
    return local


def start(client, identifier, regenerate=False, key='optimization-test-key-0001'):
    return client.post(url(identifier), json={'regenerate': regenerate}, headers={'Idempotency-Key': key})


def test_configuration_verification_binding_and_auth(local, monkeypatch):
    async def verify(config):
        return {**PROFILE, **config}
    async def discover(config):
        return {'version': 'test', 'models': [{'name': 'test-model', 'digest': 'test-digest'}]}
    monkeypatch.setattr(engine, 'verify', verify)
    monkeypatch.setattr(engine, 'discover', discover)
    root = '/api/v1/settings/transcript-optimization'
    with TestClient(api.create_app()) as client:
        assert client.get(root).json()['enabled'] is False
        config = {key: PROFILE[key] for key in ('base_url', 'model', 'use_proxy', 'enabled')}
        assert client.put(root, json=config).status_code == 409
        assert client.post(root+'/discover', json={'base_url': 'file:///tmp/x'}).status_code == 422
        assert client.post(root+'/discover', json={'base_url': 'http://user:secret@localhost'}).status_code == 422
        assert client.post(root+'/discover', json={'base_url': config['base_url']}).json()['models'][0]['name'] == 'test-model'
        verified = client.post(root+'/verify', json={k: v for k, v in config.items() if k != 'enabled'}).json()
        config['verification_token'] = verified['verification_token']
        assert client.put(root, json={**config, 'use_proxy': True}).status_code == 409
        assert client.put(root, json=config).json()['enabled'] is True
        assert client.put(root, json={**config, 'enabled': False}).json()['enabled'] is False
        from dataclasses import replace
        with monkeypatch.context() as protected:
            protected.setattr(api, 'settings', replace(local, api_key='test-secret'))
            assert client.post(root+'/verify', json={'base_url': config['base_url'], 'model': 'x'}, headers={'Authorization':'Bearer invalid'}).status_code == 401
        schema = client.get('/openapi.json').json()
        assert root in schema['paths']
        assert 'text_version' in schema['components']['schemas']['TranscriptExportRequest']['properties']


def test_units_source_coverage_scene_splits_and_long_speech():
    result = source(2)
    result['segments'] = [{'id': 0, 'speaker':'s1', 'speaker_label':'Mike Smith', 'start':0, 'end':6, 'text':'第一段。第二段。',
                           'words':[{'text':'第一段','start':0,'end':2},{'text':'第二段','start':3,'end':5}]}]
    result['video'] = {'scenes':[{'id':'a','start':0,'end':3},{'id':'b','start':3,'end':6}]}
    before = deepcopy(result)
    units = engine.prepare_units(result)
    assert [p['original_text'] for p in units] == ['第一段。','第二段。']
    assert [(p['sources'][0]['start_offset'],p['sources'][0]['end_offset']) for p in units] == [(0,4),(4,8)]
    assert result == before
    result.pop('video')
    result['segments'][0].update(text='长句内容。'*900, words=[])
    units = engine.prepare_units(result)
    assert ''.join(p['original_text'] for p in units) == result['segments'][0]['text']
    assert all(len(p['original_text']) <= 800 for p in units)
    assert ''.join(s['text'] for p in units for s in p['sources']) == result['segments'][0]['text']
    with pytest.raises(ValueError, match='窗口'):
        engine.validate_budget({**PROFILE,'context_length':16384}, units, ['热词'*10000])


def test_v14_migration_preserves_history_queue_and_idempotency(local):
    history = job()
    queued, replayed = db.create_job_idempotent('tts', 'historical queue', {'text': '保存快照'}, None, 'tts', 'existing-key', 'existing-request')
    assert not replayed
    before = {identifier: deepcopy(db.get_job(identifier)) for identifier in [history, queued['id']]}
    with db.connect() as connection:
        receipt = tuple(connection.execute('SELECT * FROM job_idempotency').fetchone())
        sequence = tuple(connection.execute('SELECT * FROM queue_sequence').fetchone())
        for table in ['optimization_units', 'transcript_optimizations', 'optimization_settings']:
            connection.execute('DROP TABLE ' + table)
        connection.execute('UPDATE schema_meta SET version=14')
    db.init_db()
    db.init_db()
    assert {identifier: db.get_job(identifier) for identifier in before} == before
    assert not store.settings()['enabled']
    with db.connect() as connection:
        assert connection.execute('SELECT min(version) FROM schema_meta').fetchone()[0] == 15
        assert tuple(connection.execute('SELECT * FROM job_idempotency').fetchone()) == receipt
        assert tuple(connection.execute('SELECT * FROM queue_sequence').fetchone()) == sequence
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    repeated, replayed = db.create_job_idempotent('tts', 'historical queue', {'text': '保存快照'}, None, 'tts', 'existing-key', 'existing-request')
    assert replayed and repeated['id'] == queued['id']


def test_accepted_key_replays_after_disabling_or_changing_configuration(configured):
    identifier = job()
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda state: bool(state['published']))['published']['id']
        store.save_settings({'base_url': 'http://other.invalid', 'model': 'other', 'use_proxy': False, 'enabled': False})
        replayed = start(client, identifier)
        assert replayed.status_code == 200
        assert replayed.json()['published']['id'] == run
        conflict = start(client, identifier, True)
        assert conflict.status_code == 409 and 'Idempotency' in conflict.json()['detail']
        assert start(client, identifier, True, 'new-key-after-disable').status_code == 409


def test_complete_idempotency_summary_exports_latest_and_cleanup(configured):
    identifier = job()
    before = deepcopy(db.get_job(identifier))
    with TestClient(api.create_app()) as client:
        response = start(client, identifier)
        assert response.status_code == 202, response.text
        assert start(client, identifier).status_code == 200
        assert start(client, identifier, True).status_code == 409
        state = wait(client, identifier, lambda s: bool(s['published']))
        first = state['published']['id']
        assert state['published']['progress'] == 1
        result = client.get(url(identifier)+'/result', params={'run_id':first}).json()
        assert result['complete'] and len(result['paragraphs']) == 8
        assert 'Atlas' in result['paragraphs'][0]['text']
        assert '阿特拉斯' in result['paragraphs'][0]['original_text']
        response = client.get(url(identifier))
        assert client.get(url(identifier),headers={'If-None-Match':response.headers['etag']}).status_code == 304
        summaries = client.get('/api/v1/jobs').json()
        assert 'optimization' in json.dumps(summaries)
        assert 'original_text' not in json.dumps(summaries) and 'hotwords' not in json.dumps(summaries)
        export = f'/api/v1/jobs/{identifier}/transcript-export'
        body = {'format':'docx','text_version':'comparison','optimization_id':first,'redact':True,'replacements':[{'source':'Atlas','target':'平台'},{'source':'阿特拉斯','target':'平台'}]}
        response = client.post(export,json=body)
        assert response.status_code == 200, response.text
        document = Document(io.BytesIO(response.content))
        text = '\n'.join(p.text for p in document.paragraphs)
        assert '原文 / Original' in text and '平台' in text and 'Mike Smith' not in text
        originals = [p for p in document.paragraphs if p.text.startswith('原文 / Original')]
        assert len(originals) == 8 and originals[0].runs[0].font.size.pt == 9
        assert str(originals[0].runs[0].font.color.rgb) == '667085'
        response = client.post(export,json={**body,'format':'pdf'})
        assert response.status_code == 200
        pdf = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(response.content)).pages)
        assert '原文' in pdf and '平台' in pdf and 'Mike Smith' not in pdf
        assert client.post(export,json={**body,'format':'srt'}).status_code == 422
        assert client.post(export,json={'format':'docx','text_version':'comparison'}).status_code == 422
        assert start(client,identifier,True,'optimization-second-key').status_code == 202
        state = wait(client,identifier,lambda s: bool(s['published'] and s['published']['id']!=first))
        assert client.get(url(identifier)+'/result',params={'run_id':first}).status_code == 404
        assert client.delete(url(identifier)).status_code == 409
        assert client.delete(url(identifier)+'?purge=true').status_code == 204
        assert client.get(url(identifier)).json()['published'] is None
        with db.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM optimization_units').fetchone()[0] == 0
        assert db.get_job(identifier) == before


def test_pause_resume_disable_and_no_late_writes(configured, monkeypatch):
    calls = []
    async def generate(profile,batch,hotwords,before,after,activity):
        calls.append([p['ordinal'] for p in batch])
        if len(calls) == 2:
            await asyncio.sleep(60)
        return [(p['ordinal'],p['original_text'].replace('那个','')) for p in batch]
    monkeypatch.setattr(engine,'generate',generate)
    identifier = job(source(100))
    with TestClient(api.create_app()) as client:
        start(client,identifier)
        state = wait(client,identifier,lambda s: bool(s['candidate'] and s['candidate']['completed_units']>0))
        run = state['candidate']['id']
        assert client.post(url(identifier)+'/pause',json={'run_id':run}).status_code == 200
        paused = client.get(url(identifier)).json()['candidate']
        assert paused['state'] == 'paused' and 0 < paused['progress'] < 1
        assert not store.checkpoint(run,[(99,'late output')])
        assert client.post(f'/api/v1/jobs/{identifier}/transcript-export',json={'format':'pdf','text_version':'optimized','optimization_id':run}).status_code == 409
        assert client.post(url(identifier)+'/resume',json={'run_id':run}).status_code == 200
        state = wait(client,identifier,lambda s: bool(s['published']))
        assert state['published']['completed_units'] == 100
        assert not any(set(calls[0])&set(batch) for batch in calls[1:])
        start(client,identifier,True,'optimization-regenerate-key')
        disabled = {k: PROFILE[k] for k in ('base_url','model','use_proxy')}
        assert client.put('/api/v1/settings/transcript-optimization',json={**disabled,'enabled':False}).status_code == 200
        state = client.get(url(identifier)).json()
        if state['candidate']:
            assert state['candidate']['state']=='paused'
            assert client.post(url(identifier)+'/resume',json={'run_id':state['candidate']['id']}).status_code == 409
        assert state['published']
        assert client.delete(url(identifier)+'?purge=true').status_code == 204
        assert not store.checkpoint(run,[(0,'resurrected')])


def test_failed_regeneration_retains_published_and_parent_purge(configured,monkeypatch):
    identifier = job()
    with TestClient(api.create_app()) as client:
        start(client,identifier)
        first = wait(client,identifier,lambda s:bool(s['published']))['published']['id']
        async def fail(*args):
            raise ValueError('Model failed')
        monkeypatch.setattr(engine,'generate',fail)
        start(client,identifier,True,'optimization-failed-generation')
        state=wait(client,identifier,lambda s:bool(s['candidate'] and s['candidate']['state']=='failed'))
        assert state['published']['id']==first
        response=client.delete(f'/api/v1/jobs/{identifier}?purge=true')
        assert response.status_code==204,response.text
        with db.connect() as connection:
            assert connection.execute('SELECT COUNT(*) FROM transcript_optimizations').fetchone()[0]==0
            assert connection.execute('SELECT COUNT(*) FROM optimization_units').fetchone()[0]==0


def test_adaptive_split_and_restart_recovery(configured,monkeypatch):
    calls=[]
    async def small(profile,batch,*args):
        calls.append(len(batch))
        if len(batch)>2:
            raise engine.InvalidOutput('length')
        return [(p['ordinal'],p['original_text']) for p in batch]
    monkeypatch.setattr(engine,'generate',small)
    identifier=job()
    with TestClient(api.create_app()) as client:
        start(client,identifier)
        state=wait(client,identifier,lambda s:bool(s['published']))
        assert state['published']['completed_units']==8 and max(calls)>2
        run=state['published']['id']
    with db.connect() as connection:
        connection.execute("UPDATE transcript_optimizations SET published=0,state='running' WHERE id=?",(run,))
    with TestClient(api.create_app()) as client:
        state=wait(client,identifier,lambda s:bool(s['candidate'] and s['candidate']['state']=='paused'))
        assert state['candidate']['completed_units']==8


def test_video_comparison_images_redaction_and_scene_reassignment(configured):
    result=source(4)
    result['video']={'revision':1,'duration':12,'time_origin':0,'scenes':[
        {'id':'a','start':0,'end':6,'frame_time':1,'image':'a.jpg'},
        {'id':'b','start':6,'end':12,'frame_time':8,'image':'b.jpg'}],'export_hidden_scene_ids':['b']}
    identifier=job(result)
    frames=configured.jobs_dir/identifier/'output'/'frames';frames.mkdir(parents=True)
    for name in ('a.jpg','b.jpg'):
        Image.new('RGB',(80,60),'blue').save(frames/name)
    with TestClient(api.create_app()) as client:
        start(client,identifier)
        run=wait(client,identifier,lambda s:bool(s['published']))['published']['id']
        response=client.post(f'/api/v1/jobs/{identifier}/transcript-export',json={'format':'docx','text_version':'comparison','optimization_id':run})
        assert response.status_code==200,response.status_code
        document=Document(io.BytesIO(response.content))
        assert len(document.inline_shapes)==1
        assert sum(p.text.startswith('原文 / Original') for p in document.paragraphs)==4
        result['video']['scenes'][0]['end']=9;result['video']['scenes'][1]['start']=9
        db.update_job(identifier,result_json=result)
        draft=client.get(url(identifier)+'/result',params={'run_id':run}).json()
        assert [len(s['paragraphs']) for s in draft['scenes']]==[3,1]
        assert len({p['id'] for s in draft['scenes'] for p in s['paragraphs']})==4


def test_stream_contract_truncation_and_coverage(monkeypatch):
    import httpx
    profile = {**PROFILE, 'context_length': 16384}
    batch = [{'id':'p1','ordinal':0,'speaker':'s1','original_text':'那个预算15万元，不是50万元。'}]
    payload = engine.payload_for(profile,batch,['Atlas'],'','')
    assert payload['think'] is False and payload['options']['num_predict']==4096
    assert payload['options']['num_ctx']==16384
    async def run(reason, paragraphs):
        event={'message':{'content':json.dumps({'paragraphs':paragraphs})},'done':True,'done_reason':reason}
        transport=httpx.MockTransport(lambda request:httpx.Response(200,content=(json.dumps(event)+'\n').encode()))
        monkeypatch.setattr(engine,'client',lambda config:httpx.AsyncClient(base_url='http://test/',transport=transport))
        return await engine.generate(profile,batch,['Atlas'],'','',None)
    assert asyncio.run(run('stop',[{'id':'p1','text':'预算15万元，不是50万元。'}]))==[(0,'预算15万元，不是50万元。')]
    with pytest.raises(engine.InvalidOutput):
        asyncio.run(run('length',[{'id':'p1','text':'预算15万元'}]))
    with pytest.raises(engine.InvalidOutput):
        asyncio.run(run('stop',[{'id':'p2','text':'错误段落'}]))


def test_queue_limit_and_settings_are_independent(local):
    identifiers=[job() for _ in range(6)]
    for identifier in identifiers[:5]:
        store.create(identifier,'key-'+identifier,False,PROFILE,'hash',[],engine.prepare_units(source()))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        store.create(identifiers[5],'key-six',False,PROFILE,'hash',[],engine.prepare_units(source()))
    assert error.value.status_code==429
    assert error.value.headers.get('Retry-After')


def test_model_output_validation_survives_optimized_python(local):
    code = '''import asyncio,json,httpx
from audio_intel import optimization_engine as engine
event={'message':{'content':json.dumps({'paragraphs':[{'id':'wrong','text':'预算15万元。'}]})},'done':True,'done_reason':'stop'}
transport=httpx.MockTransport(lambda request:httpx.Response(200,content=(json.dumps(event)+'\\n').encode()))
engine.client=lambda config:httpx.AsyncClient(base_url='http://test/',transport=transport)
async def run():
    try:
        await engine.generate({'model':'test','context_length':16384},[{'id':'p1','ordinal':0,'speaker':'s1','original_text':'预算15万元。'}],[],'','',None)
    except engine.InvalidOutput:
        return
    raise SystemExit('Malformed model output was accepted')
asyncio.run(run())
'''
    environment = dict(os.environ, AUDIO_INTEL_LOAD_ENV='0', AUDIO_INTEL_DATA_DIR=str(local.data_dir),
                       AUDIO_INTEL_TEMP_DIR=str(local.temp_dir), TMPDIR=str(local.temp_dir))
    subprocess.run([sys.executable, '-O', '-c', code], env=environment, check=True, timeout=15)
