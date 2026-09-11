from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from audio_intel.document_text import extract, segment, length_sections, _member
from audio_intel.document_download import zip_stream, mp3_stream, lease, deletion_guard


def test_lossless_fallback_and_boundary_priority():
    text = '甲' * 850 + '\n\n' + '乙' * 147 + '。\n' + '丙' * 2500
    parts = length_sections(text, 1000)
    assert parts[0]['end'] == 852 and parts[0]['basis'] == 'paragraph'
    assert ''.join(text[p['start']:p['end']] for p in parts) == text
    for text in ['无标点' * 3000, ('Sentence.\n' * 1000), ('longword ' * 1500), '🙂e\u0301' * 3000]:
        parts = length_sections(text, 1000)
        assert ''.join(text[p['start']:p['end']] for p in parts) == text
        assert all(0 < p['end'] - p['start'] <= 2000 for p in parts)
        assert all(text[p['end']:p['end'] + 1] != '\u0301' for p in parts)
    assert length_sections('a'*1200, 1000) == [{'start':0,'end':1200,'basis':'remainder'}]
    assert length_sections('a'*2100, 1000)[0]['basis'] == 'forced'


def test_markdown_heading_level_prefix_and_long_chapter(tmp_path):
    source = tmp_path/'test.md'
    source.write_text('# Title\n\n## First\n\n'+ '内容。'*20000 +'\n\n### Subheading\n\n正文\n\n## Second\n\n尾声\n```py\nsecret code\n```\n![image](a.png)')
    doc=extract(source,5000000,1000000)
    auto=segment(doc)
    assert len(auto['sections']) == 2
    assert auto['sections'][0]['title']=='First'
    assert auto['sections'][0]['char_count']>50000
    assert 'Subheading' in doc['text'] and 'secret code' not in doc['text']
    assert doc['warnings']
    length=segment(doc,'length',1000)
    assert len(length['sections'])>20
    assert ''.join(doc['text'][p['start']:p['end']] for p in auto['sections'])==doc['text']
    assert auto['preview_revision'] != length['preview_revision']


def test_epub_same_file_anchors_and_reading_order(tmp_path):
    assert _member('OPS/a.xhtml','#chapter') == ('OPS/a.xhtml','chapter')
    source=tmp_path/'test.epub'
    with zipfile.ZipFile(source,'w') as z:
        z.writestr('META-INF/container.xml','<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>')
        z.writestr('OPS/content.opf','<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c" href="a.xhtml"/><item id="n" href="toc.ncx" media-type="application/x-dtbncx+xml"/></manifest><spine><itemref idref="c"/><itemref idref="c"/></spine></package>')
        z.writestr('OPS/a.xhtml','<html><body><p>Preface</p><div id="a">First body</div><div id="b">Second body</div></body></html>')
        z.writestr('OPS/toc.ncx','<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap><navPoint><navLabel><text>Second</text></navLabel><content src="a.xhtml#b"/></navPoint><navPoint><navLabel><text>First</text></navLabel><content src="a.xhtml#a"/></navPoint></navMap></ncx>')
    doc=extract(source,5000000,1000000)
    parts=segment(doc)['sections']
    assert [p['title'] for p in parts][-2:] == ['First','Second']
    assert doc['text'].count('First body') == 1
    assert ''.join(doc['text'][p['start']:p['end']] for p in parts)==doc['text']
    with pytest.raises(ValueError):
        extract(source,5000000,10)
    with pytest.raises(ValueError):
        _member('OPS/a.xhtml','../../etc/passwd')


@pytest.fixture
def document_client(tmp_path,monkeypatch):
    import audio_intel.api as api
    import audio_intel.db as db
    import audio_intel.worker as worker
    import audio_intel.purge as purge
    import tts.pipeline as pipeline
    from audio_intel.config import settings
    local=replace(settings,data_dir=tmp_path/'data',temp_dir=tmp_path/'tmp',mock_mode=True,min_free_disk_bytes=0,enabled_services=frozenset({'tts','asr'}),max_queued_tts=10)
    for module in [api,db,worker,purge,pipeline]:
        monkeypatch.setattr(module,'settings',local)
    with TestClient(api.create_app()) as client:
        yield client,local


def import_document(client,text='# One\n\nHello.\n\n# Two\n\nWorld.'):
    headers={'Idempotency-Key':uuid.uuid4().hex}
    payload={'files':{'file':('test.md',text.encode(),'text/markdown')},'headers':headers}
    response=client.post('/api/v1/tts/document-imports',**payload)
    assert response.status_code==202,response.text
    identifier=response.json()['id']
    replay=client.post('/api/v1/tts/document-imports',**payload)
    assert replay.status_code==200 and replay.json()['id']==identifier
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        record=client.get('/api/v1/tts/document-imports/'+identifier).json()
        if record['state']=='ready':break
        assert record['state']!='failed',record
        time.sleep(.05)
    assert record['state']=='ready',record
    preview=client.post(f'/api/v1/tts/document-imports/{identifier}/preview',json={}).json()
    return identifier,preview


def submit_document(client,identifier,preview,**kwargs):
    data={'document_import_id':identifier,'preview_revision':preview['preview_revision'],'section_ids':[s['id'] for s in preview['sections']],'compute_device':'cpu','speaker':'Vivian',**kwargs}
    headers={'Idempotency-Key':uuid.uuid4().hex}
    response=client.post('/api/v1/tts/document-jobs',data=data,headers=headers)
    assert response.status_code==202,response.text
    replay=client.post('/api/v1/tts/document-jobs',data=data,headers=headers)
    assert replay.status_code==200,replay.text
    assert replay.json()['id']==response.json()['id']
    return response.json(),data


def test_document_contract_checkpoint_resume_streams_and_purge(document_client,monkeypatch):
    client,local=document_client
    from audio_intel import db,document_store
    from audio_intel.worker import JobContext
    import tts.pipeline as pipeline
    from tts.document import retry_delay
    identifier,preview=import_document(client)
    job,data=submit_document(client,identifier,preview)
    assert 'text' not in job['request'] and job['request']['document']['section_count']==2
    assert client.post('/api/v1/tts/document-jobs',data={**data,'unexpected':'x'},headers={'Idempotency-Key':uuid.uuid4().hex}).status_code==422
    assert client.post('/api/v1/tts/document-jobs',data={**data,'preview_revision':'stale'},headers={'Idempotency-Key':uuid.uuid4().hex}).status_code==409
    assert client.post('/api/v1/tts/jobs',data={'text':'x'*50001},headers={'Idempotency-Key':uuid.uuid4().hex}).status_code==422
    context=JobContext(db.get_job(job['id']),'test')
    original=pipeline.mock_speech
    calls=0
    def fail_second(text):
        nonlocal calls
        calls+=1
        if calls==2:raise MemoryError('test oom')
        return original(text)
    monkeypatch.setattr(pipeline,'mock_speech',fail_second)
    with pytest.raises(MemoryError):pipeline.process_job(context)
    rows=document_store.sections(job['id'])
    assert [p['state'] for p in rows]==['complete','generating']
    first=Path(rows[0]['artifact']['path']);before=(first.stat().st_mtime_ns,first.read_bytes())
    assert not list(context.output_dir.glob('*.partial'))
    assert retry_delay(job,MemoryError())==30
    assert retry_delay(job,MemoryError())==120
    assert retry_delay(job,MemoryError()) is None
    assert retry_delay(job,ValueError()) is None
    monkeypatch.setattr(pipeline,'mock_speech',original)
    result=pipeline.process_job(context)
    assert before==(first.stat().st_mtime_ns,first.read_bytes())
    assert len(result['artifacts'])==2 and all(p.suffix=='.mp3' for p in context.output_dir.iterdir())
    db.finish_job(job['id'],'succeeded',result_json=result)
    files=[(a['name'],Path(a['path'])) for a in result['artifacts']]
    streamed=client.get(f"/api/v1/jobs/{job['id']}/document/download?mode=sections")
    assert streamed.status_code==200 and streamed.headers['accept-ranges']=='none'
    with zipfile.ZipFile(io.BytesIO(streamed.content)) as archive:
        assert archive.namelist()==[a[0] for a in files]
        for name,path in files:assert archive.read(name)==path.read_bytes()
    complete=client.get(f"/api/v1/jobs/{job['id']}/document/download?mode=complete")
    assert complete.status_code==200 and complete.headers['x-accel-buffering']=='no'
    import av
    with av.open(io.BytesIO(complete.content)) as audio:
        samples=sum(frame.samples for frame in audio.decode(audio=0))
    assert samples>0
    individual=client.get(f"/api/v1/jobs/{job['id']}/artifacts/{files[0][0]}",headers={'Range':'bytes=0-99'})
    assert individual.status_code==206,individual.text[:200]
    summaries=client.get('/api/v1/jobs').json()['items']
    assert 'result' not in summaries[0] and 'request' not in summaries[0]
    assert client.delete('/api/v1/tts/document-imports/'+identifier).status_code==204
    with lease(job['id'],2):
        assert client.delete('/api/v1/jobs/'+job['id']+'?purge=true').status_code==409
    assert client.delete('/api/v1/jobs/'+job['id']+'?purge=true').status_code==204


def test_download_guards_and_zip_bounded_chunks(tmp_path):
    path=tmp_path/'audio.mp3';path.write_bytes(b'a'*2000000)
    chunks=list(zip_stream([('001.mp3',path)]))
    assert max(map(len,chunks))<270000
    with zipfile.ZipFile(io.BytesIO(b''.join(chunks))) as z:assert z.read('001.mp3')==path.read_bytes()
    with deletion_guard('x') as allowed:
        assert allowed
        with pytest.raises(RuntimeError):
            with lease('x',2):pass
    with lease('x',1):
        with deletion_guard('x') as allowed:assert not allowed
        with pytest.raises(RuntimeError):
            with lease('y',1):pass


def test_zip64_over_four_gib_without_export_file(tmp_path):
    import struct
    path=tmp_path/'sparse.mp3'
    with path.open('wb') as f:f.truncate(2**32+1024)
    total=0;tail=b'';peak=0
    for chunk in zip_stream([('large.mp3',path)]):
        peak=max(peak,len(chunk));total+=len(chunk);tail=(tail+chunk)[-1024:]
    assert total>2**32 and peak<270000
    assert b'PK\x06\x06' in tail and b'PK\x06\x07' in tail
    assert b'large.mp3' in tail


def test_million_characters_bounded_audio_and_resume(document_client,monkeypatch):
    import tracemalloc
    import numpy as np
    from audio_intel import db,document_store
    from audio_intel.worker import JobContext
    import tts.pipeline as pipeline
    from audio_intel.utils import atomic_json
    client,local=document_client
    text='正文测试。'*200000
    parsed={'text':text,'text_hash':hashlib.sha256(text.encode()).hexdigest(),'headings':[]}
    preview=segment(parsed,'length',10000)
    request={'purpose':'tts_document','model':'qwen3-tts-0.6b','voice_mode':'preset','speaker':'Vivian','compute_device':'cpu','language':'Chinese','document':{'text_hash':parsed['text_hash'],'total_chars':len(text),'title':'million'},'accelerate_single_task':False}
    job=db.create_job('tts','million',request)
    root=local.jobs_dir/job['id']/'input';root.mkdir(parents=True)
    (root/'document.txt').write_text(text)
    atomic_json(root/'document.json',{'contract_version':1,'sections':preview['sections']})
    context=JobContext(job,'test')
    monkeypatch.setattr(context,'progress',lambda *a,**k:None)
    calls=0
    def short_mock(text):
        nonlocal calls
        assert len(text)<=300
        calls+=1
        return np.zeros(240,dtype=np.float32),24000
    monkeypatch.setattr(pipeline,'mock_speech',short_mock)
    tracemalloc.start()
    try:
        result=pipeline.process_job(context)
        peak=tracemalloc.get_traced_memory()[1]
    finally:tracemalloc.stop()
    assert peak<80*1024**2
    assert calls>3000 and len(result['artifacts'])==len(preview['sections'])
    assert all(p['state']=='complete' for p in document_store.sections(job['id']))
    assert not list(context.output_dir.glob('*.wav'))


def test_import_failure_conflict_and_auth(document_client):
    client,_=document_client
    key={'Idempotency-Key':uuid.uuid4().hex}
    route='/api/v1/tts/document-imports'
    assert client.post(route,files={'file':('a.txt',b'hello')}).status_code==400
    response=client.post(route,files={'file':('a.txt',b'hello')},headers=key)
    assert response.status_code==202
    assert client.post(route,files={'file':('a.txt',b'changed')},headers=key).status_code==409
    assert client.post(route,files={'file':('a.exe',b'hello')},headers={'Idempotency-Key':uuid.uuid4().hex}).status_code==422
    response=client.post(route,files={'file':('a.pdf',b'not PDF')},headers={'Idempotency-Key':uuid.uuid4().hex})
    identifier=response.json()['id']
    end=time.monotonic()+15
    while time.monotonic()<end:
        state=client.get(route+'/'+identifier).json()
        if state['state']=='failed':break
        time.sleep(.05)
    assert state['state']=='failed' and state['error']
    assert client.post(route+'/'+identifier+'/preview',json={}).status_code==409


def test_stale_document_requires_manual_resume(document_client):
    from audio_intel import db,document_store
    client,local=document_client
    identifier,preview=import_document(client)
    job,_=submit_document(client,identifier,preview)
    claimed=db.claim_job('tts','stale')
    assert claimed['id']==job['id']
    document_store.ensure_sections(job['id'],preview['sections'])
    document_store.checkpoint(job['id'],preview['sections'][0]['id'],'complete',{'name':'one.mp3'})
    partial=local.jobs_dir/job['id']/'output/document-test.partial';partial.parent.mkdir(exist_ok=True);partial.write_bytes(b'incomplete')
    assert db.recover_stale('tts')==1
    assert db.get_job(job['id'])['state']=='failed'
    assert not partial.exists()
    assert document_store.sections(job['id'])[0]['state']=='complete'
    assert client.post('/api/v1/jobs/'+job['id']+'/retry').status_code==200
    assert document_store.sections(job['id'])[0]['state']=='complete'


def test_authenticated_document_routes(tmp_path,monkeypatch):
    import audio_intel.api as api
    import audio_intel.db as db
    from audio_intel.config import settings
    local=replace(settings,data_dir=tmp_path/'data',temp_dir=tmp_path/'tmp',api_key='test-document-key',mock_mode=True,min_free_disk_bytes=0)
    monkeypatch.setattr(api,'settings',local);monkeypatch.setattr(db,'settings',local)
    with TestClient(api.create_app()) as client:
        assert client.get('/api/v1/tts/document-imports').status_code==401
        assert client.get('/api/v1/jobs/unknown/document/download?mode=complete').status_code==401
        headers={'Authorization':'Bearer test-document-key','Idempotency-Key':uuid.uuid4().hex}
        response=client.post('/api/v1/tts/document-imports',headers=headers,files={'file':('auth.txt',b'hello')})
        assert response.status_code==202,response.text
        assert client.get('/api/v1/tts/document-imports',headers={'Authorization':'Bearer test-document-key'}).status_code==200


def test_document_voiceprint_replay_keeps_submitted_snapshot(document_client):
    from audio_intel import db
    client,local=document_client
    identifier,preview=import_document(client)
    person=db.create_voiceprint_person('Document voice')
    source=local.voiceprints_dir/'source.wav';source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(b'original sample')
    sample=db.create_voiceprint_sample(person['id'],state='ready',audio_path=str(source),transcript='original text',language='Chinese',duration=2)
    data={'document_import_id':identifier,'preview_revision':preview['preview_revision'],'section_ids':[p['id'] for p in preview['sections']],'voice_mode':'voiceprint','voiceprint_sample_id':sample['id'],'compute_device':'cpu'}
    headers={'Idempotency-Key':uuid.uuid4().hex}
    first=client.post('/api/v1/tts/document-jobs',data=data,headers=headers)
    assert first.status_code==202,first.text
    request=first.json()['request'];reference=Path(request['reference_audio_path'])
    assert reference.read_bytes()==b'original sample'
    db.update_voiceprint_sample(sample['id'],transcript='changed text')
    source.write_bytes(b'changed sample')
    replay=client.post('/api/v1/tts/document-jobs',data=data,headers=headers)
    assert replay.status_code==200,replay.text
    assert replay.json()['request']['reference_text']=='original text'
    assert reference.read_bytes()==b'original sample'


def test_document_idempotent_replay_after_import_deletion(document_client):
    client,_=document_client
    identifier,preview=import_document(client)
    data={'document_import_id':identifier,'preview_revision':preview['preview_revision'],'section_ids':[p['id'] for p in preview['sections']],'compute_device':'cpu','speaker':'Vivian'}
    headers={'Idempotency-Key':uuid.uuid4().hex}
    first=client.post('/api/v1/tts/document-jobs',data=data,headers=headers)
    assert first.status_code==202
    assert client.delete('/api/v1/tts/document-imports/'+identifier).status_code==204
    replay=client.post('/api/v1/tts/document-jobs',data=data,headers=headers)
    assert replay.status_code==200 and replay.json()['id']==first.json()['id']
    assert client.post('/api/v1/tts/document-jobs',data={**data,'speaker':'Ryan'},headers=headers).status_code==409


def test_mp3_remux_preserves_order_and_duration(tmp_path):
    import av
    import numpy as np
    files=[]
    rate=24000
    for number,frequency in enumerate((330,880),1):
        path=tmp_path/f'{number}.mp3'
        values=(.2*np.sin(2*np.pi*frequency*np.arange(rate*2)/rate)).astype(np.float32).reshape(1,-1)
        with av.open(str(path),'w',format='mp3') as output:
            stream=output.add_stream('libmp3lame',rate=rate);stream.layout='mono';stream.bit_rate=96000
            for start in range(0,values.shape[1],8192):
                frame=av.AudioFrame.from_ndarray(values[:,start:start+8192],format='fltp',layout='mono');frame.sample_rate=rate
                for packet in stream.encode(frame):output.mux(packet)
            for packet in stream.encode():output.mux(packet)
        files.append((path.name,path))
    with av.open(io.BytesIO(b''.join(mp3_stream(files)))) as source:
        decoded=np.concatenate([frame.to_ndarray().reshape(-1) for frame in source.decode(audio=0)])
    # Nonseekable MP3 retains per-section encoder delay/tail padding. At
    # 24 kHz each MPEG-2 frame has 576 samples; budget three per section.
    assert 0 <= len(decoded)-rate*4 <= len(files)*3*576
    for offset,expected in ((rate//2,330),(rate*5//2,880)):
        spectrum=np.abs(np.fft.rfft(decoded[offset:offset+rate//2]))
        assert abs(np.argmax(spectrum)*2-expected)<10


def test_single_markdown_wrapper_uses_length_fallback(tmp_path):
    source=tmp_path/'single.md'
    source.write_text('# Document title\n\n'+('Plain paragraph.\n\n'*5000))
    doc=extract(source,5000000,1000000)
    preview=segment(doc)
    assert len(preview['sections'])>5
    assert all(p['char_count']<=20000 for p in preview['sections'])
    assert ''.join(doc['text'][p['start']:p['end']] for p in preview['sections'])==doc['text']


@pytest.mark.parametrize('multipart', [False, True])
def test_document_large_selection_and_stable_sections(document_client, multipart):
    client, local = document_client
    from audio_intel import document_store
    identifier, preview = import_document(client, '\n'.join(f'# Chapter {i}\n\nBody {i}.\n' for i in range(2000)))
    assert len(preview['sections']) == 2000
    for count in (1000, 1001, 2000):
        data = {'document_import_id': identifier, 'preview_revision': preview['preview_revision'],
                'section_ids': [s['id'] for s in preview['sections'][:count]], 'compute_device': 'cpu', 'speaker': 'Vivian'}
        kwargs = {'files': [(key, (None, value)) for key, values in data.items() for value in (values if isinstance(values, list) else [values])]} if multipart else {'data': data}
        response = client.post('/api/v1/tts/document-jobs', headers={'Idempotency-Key': uuid.uuid4().hex}, **kwargs)
        assert response.status_code == 202, response.text
        job = response.json()
    endpoint = f"/api/v1/jobs/{job['id']}/document/sections?offset=10&limit=2"
    before = client.get(endpoint).json()
    document_store.ensure_sections(job['id'], preview['sections'])
    after = client.get(endpoint).json()
    assert before['total'] == after['total'] == 2000
    for a, b in zip(before['items'], after['items']):
        assert set(a) == set(b)
        assert {k: v for k, v in a.items() if k != 'updated_at'} == {k: v for k, v in b.items() if k != 'updated_at'}
        assert a['start'] == a['start_offset'] and a['end'] == a['end_offset']
        assert a['char_count'] == a['end'] - a['start']
    data['section_ids'].append(data['section_ids'][0])
    assert client.post('/api/v1/tts/document-jobs', headers={'Idempotency-Key': uuid.uuid4().hex}, data=data).status_code == 422


def test_blank_sections_merge_without_losing_text_or_legacy_ids():
    from audio_intel.document_text import digest
    text = '\n\nFirst\n\n  \nSecond\n  '
    doc = {'version': 2, 'text': text, 'text_hash': 'test', 'headings': [
        {'offset': n, 'level': 1, 'title': title, 'basis': 'bookmark'}
        for n, title in [(0, 'Blank cover'), (2, 'First'), (9, 'Blank page'), (12, 'Second'), (19, 'Blank tail')]
    ], 'page_offsets': [0, 2, 9, 12, 19]}
    result = segment(doc)
    assert len(result['sections']) == 2
    assert all(text[s['start']:s['end']].strip() for s in result['sections'])
    assert ''.join(text[s['start']:s['end']] for s in result['sections']) == text
    assert result['preview_revision'] != digest([2, 'test', 'auto', 10000])
    assert segment({'text': 'hello', 'text_hash': 'old'})['preview_revision'] == digest([1, 'old', 'auto', 10000])
    with pytest.raises(ValueError, match='no speakable'):
        segment({'text': ' \n\n', 'text_hash': 'blank'})


def test_document_upload_stream_limits_disconnect_and_no_spooling(tmp_path):
    import asyncio
    from fastapi import HTTPException
    from starlette.requests import Request, ClientDisconnect
    from audio_intel.document_upload import receive_document
    body = b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\n' + b'x' * 4096 + b'\r\n--boundary--\r\n'

    async def check(limit, disconnect=False):
        position = 0
        async def receive():
            nonlocal position
            if disconnect and position >= 300:
                return {'type': 'http.disconnect'}
            chunk = body[position:position + 100]
            position += len(chunk)
            return {'type': 'http.request', 'body': chunk, 'more_body': position < len(body)}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': [(b'content-type', b'multipart/form-data; boundary=boundary')]}, receive)
        path = tmp_path / uuid.uuid4().hex
        try:
            return await receive_document(request, path, limit)
        finally:
            if path.exists():
                assert path.stat().st_size <= limit
                path.unlink()  # A closed handle is required for this on Windows.
    assert asyncio.run(check(4096))[2] == 4096
    with pytest.raises(HTTPException) as error:
        asyncio.run(check(1000))
    assert error.value.status_code == 413
    with pytest.raises(ClientDisconnect):
        asyncio.run(check(4096, True))


def test_document_admission_before_body_and_reserved_bytes(document_client, monkeypatch):
    client, local = document_client
    from audio_intel import document_upload
    admission = client.app.state.admission
    monkeypatch.setattr(admission, 'disk_free', lambda: local.max_document_bytes + 1)
    decision = client.portal.call(admission.reserve_upload, local.max_document_bytes)
    assert decision.accepted
    calls = []
    original = document_upload.receive_document
    async def spy(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(document_upload, 'receive_document', spy)
    try:
        response = client.post('/api/v1/tts/document-imports', headers={'Idempotency-Key': uuid.uuid4().hex}, files={'file': ('test.txt', b'hello')})
        assert response.status_code == 429 and 'Retry-After' in response.headers
        assert not calls
    finally:
        client.portal.call(admission.release_upload, decision.reserved_bytes)
    assert admission.active == 0 and admission._bytes == 0
    assert not list((local.data_dir / 'documents').glob('*'))


def test_import_retry_pagination_storage_and_snapshot_delete_guard(document_client):
    import concurrent.futures
    from audio_intel import document_store, db
    client, local = document_client
    identifier, preview = import_document(client)
    endpoint = '/api/v1/tts/document-imports/' + identifier
    assert client.post(endpoint + '/retry').status_code == 409
    document_store.update_import(identifier, state='failed', error='transient parser failure')
    response = client.post(endpoint + '/retry')
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + 10
    while client.get(endpoint).json()['state'] != 'ready' and time.monotonic() < deadline:
        time.sleep(.05)
    page = client.get('/api/v1/tts/document-imports?offset=0&limit=1').json()
    assert len(page) == 1 and page[0]['storage_bytes'] > page[0]['size_bytes']
    assert client.get('/api/v1/tts/document-imports?offset=1&limit=1').json() == []
    with document_store.import_files(), concurrent.futures.ThreadPoolExecutor() as pool:
        assert pool.submit(client.delete, endpoint).result().status_code == 409
    job, _ = submit_document(client, identifier, preview)
    assert client.delete(endpoint).status_code == 204
    assert client.get(endpoint).status_code == 404
    assert (local.jobs_dir / job['id'] / 'input/document.txt').is_file()
    assert client.get(f"/api/v1/jobs/{job['id']}/document/sections").status_code == 200
    assert client.post(endpoint + '/retry').status_code == 404


def test_document_download_problem_contains_retry_delay(document_client):
    client, local = document_client
    from audio_intel import db
    identifier, preview = import_document(client)
    job, _ = submit_document(client, identifier, preview)
    db.update_job(job['id'], state='succeeded', result={'artifacts': []})
    with lease('other', 1), lease('another', 2):
        response = client.get(f"/api/v1/jobs/{job['id']}/document/download?mode=sections")
    assert response.status_code == 429
    assert response.json()['retry_after_seconds'] == int(response.headers['Retry-After'])


def test_pdf_blank_bookmark_does_not_create_an_audio_item(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DecodedStreamObject, DictionaryObject
    writer = PdfWriter()
    for i in range(3):
        page = writer.add_blank_page(612, 792)
        if i:
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 50 700 Td (Body page {i}.) Tj ET'.encode())
            page[NameObject('/Contents')] = writer._add_object(stream)
        writer.add_outline_item(('Blank cover', 'First', 'Second')[i], i)
    path = tmp_path / 'bookmarks.pdf'
    writer.write(path)
    parsed = extract(path, 100000, 100000)
    preview = segment(parsed)
    assert len(preview['sections']) == 2
    assert all(parsed['text'][p['start']:p['end']].strip() for p in preview['sections'])
    assert ''.join(parsed['text'][p['start']:p['end']] for p in preview['sections']) == parsed['text']
    assert parsed['warnings']


def test_document_auth_before_upload_and_cookie_origin(document_client, monkeypatch):
    import audio_intel.api as api
    from audio_intel import document_upload
    client, local = document_client
    monkeypatch.setattr(api, 'settings', replace(local, api_key='document-secret'))
    calls = []
    original = document_upload.receive_document
    async def spy(*args, **kwargs):
        calls.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(document_upload, 'receive_document', spy)
    endpoint = '/api/v1/tts/document-imports'
    kwargs = {'files': {'file': ('a.md', b'# Title\n\nText')}, 'headers': {'Idempotency-Key': uuid.uuid4().hex}}
    assert client.post(endpoint, **kwargs).status_code == 401 and not calls
    assert client.post('/api/v1/auth/session', headers={'Authorization': 'Bearer document-secret'}).status_code == 204
    kwargs['headers']['Origin'] = 'https://untrusted.example'
    assert client.post(endpoint, **kwargs).status_code == 403 and not calls
    assert client.post('/api/v1/tts/document-jobs', data={'section_ids': ['a'] * 1001}, headers=kwargs['headers']).status_code == 403
    kwargs['headers']['Origin'] = 'http://testserver'
    assert client.post(endpoint, **kwargs).status_code == 202 and calls
    client.cookies.clear()
    kwargs['headers']['Authorization'] = 'Bearer document-secret'
    assert client.post(endpoint, **kwargs).status_code == 200


def test_large_zip_stream_memory_is_bounded(tmp_path):
    import tracemalloc
    source = tmp_path / 'section.mp3'
    source.write_bytes(b'0' * (256 * 1024))
    files = [(f'{i}.mp3', source) for i in range(2000)]
    tracemalloc.start()
    try:
        count = sum(len(chunk) for chunk in zip_stream(files))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count > 500 * 1024**2
    assert peak < 16 * 1024**2
    assert list(tmp_path.iterdir()) == [source]


def test_document_openapi_models_and_required_import_key(document_client):
    client, _ = document_client
    schema = client.get('/openapi.json').json()
    upload = schema['paths']['/api/v1/tts/document-imports']['post']
    assert next(p for p in upload['parameters'] if p['name'] == 'Idempotency-Key')['required']
    assert upload['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/DocumentImportResponse')
    for path, method, model in [('/api/v1/tts/document-imports/{identifier}/preview', 'post', 'DocumentPreview'),
                                ('/api/v1/jobs/{job_id}/document/sections', 'get', 'JobDocumentSections')]:
        assert schema['paths'][path][method]['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/'+model)
    assert {'start', 'end', 'char_count', 'start_offset', 'position', 'artifact', 'retries'} <= set(schema['components']['schemas']['JobDocumentSection']['required'])


def test_v10_migration_preserves_historical_jobs_and_queue(tmp_path, monkeypatch):
    from audio_intel import db, api
    local = replace(api.settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", mock_mode=True, min_free_disk_bytes=0)
    monkeypatch.setattr(db, "settings", local)
    monkeypatch.setattr(api, "settings", local)
    client = TestClient(api.create_app())  # No parser lifespan during the v10 fixture construction.
    headers = {'Idempotency-Key': uuid.uuid4().hex}
    data = {'text': 'Historical text', 'speaker': 'Vivian', 'compute_device': 'cpu'}
    job = client.post('/api/v1/tts/jobs', data=data, headers=headers).json()
    with db.connect() as connection:
        before = tuple(connection.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone())
        sequence = tuple(connection.execute('SELECT * FROM queue_sequence').fetchone())
        connection.execute('DROP TABLE document_sections')
        connection.execute('DROP TABLE document_imports')
        connection.execute('UPDATE schema_meta SET version=10')
    db.init_db()
    with db.connect() as connection:
        assert connection.execute('SELECT version FROM schema_meta').fetchone()[0] == 11
        assert tuple(connection.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()) == before
        assert tuple(connection.execute('SELECT * FROM queue_sequence').fetchone()) == sequence
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    replay = client.post('/api/v1/tts/jobs', data=data, headers=headers)
    assert replay.status_code == 200 and replay.json()['id'] == job['id']
