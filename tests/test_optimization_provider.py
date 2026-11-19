"""Wire protocol, credential lifetime and upgrade regressions."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from audio_intel import api, db, optimization_engine as engine, optimization_store as store, optimization_provider as provider
from test_voiceprint_groups_exports import local

CONFIG = {'protocol': provider.PROTOCOL, 'base_url': 'https://model.invalid/gateway/v1', 'model': 'test', 'use_proxy': False, 'context_length': 32768, 'extra_body': {}}
ROOT = '/api/v1/settings/transcript-optimization'


def test_usage_scope_and_call_finish_survive_request_body_errors(monkeypatch):
    from audio_intel import optimization_budget as budget
    finished = []
    monkeypatch.setattr(budget, 'reserve', lambda: 7)
    monkeypatch.setattr(budget, 'finish', lambda identifier, error, usage: finished.append((identifier, type(error), usage)))

    def invalid(*args):
        raise ValueError('Invalid request body')

    monkeypatch.setattr(provider, 'request_body', invalid)
    monkeypatch.setattr(engine, 'request_fits', lambda *args: True)

    async def check():
        outer = {}
        token = provider.usage_capture.set(outer)
        try:
            with pytest.raises(ValueError, match='Invalid request body'):
                await engine.streamed_text(CONFIG, {})
            assert provider.usage_capture.get() is outer
        finally:
            provider.usage_capture.reset(token)

    asyncio.run(check())
    assert finished == [(7, ValueError, {})]


def stream(text='正文', finish='stop'):
    return 'data: '+json.dumps({'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': finish}]})+'\n\ndata: [DONE]\n\n'


def test_path_normalization_and_extra_limits():
    assert provider.normalize_url(CONFIG['base_url']+'/chat/completions/') == CONFIG['base_url']
    assert provider.normalize_url('https://model.invalid/custom') == 'https://model.invalid/custom'
    for value in [{'model':'x'}, {'messages':[]}, {'n':2}, {'headers':{}}, {'max_tokens':True}, {'max_tokens':0}, {'max_tokens':5,'max_completion_tokens':8}]:
        with pytest.raises(ValueError):
            provider.validate_extra(value)
    payload = {'messages':[{'role':'user','content':'原文'}], 'options':{'num_predict':1024}, 'format':{'type':'object'}}
    body=provider.request_body({**CONFIG,'extra_body':{'max_completion_tokens':4096,'temperature':None}},payload)
    assert body['max_completion_tokens']==4096 and 'max_tokens' not in body and 'temperature' not in body
    assert body['response_format']=={'type':'json_object'}
    assert 'schema' in body['messages'][0]['content'] and payload['messages'][0]['content']=='原文'
    assert not provider.fits({**CONFIG,'extra_body':{'max_tokens':32768}}, payload)


@pytest.mark.parametrize('wire', ['sse','json'])
def test_completion_protocol_and_prefix(wire):
    def handle(request):
        assert request.url.path=='/gateway/v1/chat/completions'
        assert not any(k in json.loads(request.content) for k in ('options','think','truncate','format'))
        if wire=='json':
            return httpx.Response(200,json={'choices':[{'message':{'content':'正文'},'finish_reason':'stop'}]})
        return httpx.Response(200,text=': ping\n\ndata: {"choices":[]}\n\n'+stream(),headers={'content-type':'text/event-stream'})
    async def run():
        async with httpx.AsyncClient(base_url=CONFIG['base_url']+'/',transport=httpx.MockTransport(handle)) as client:
            return await provider.completion(client,provider.request_body(CONFIG,{'messages':[]}))
    assert asyncio.run(run())=='正文'


@pytest.mark.parametrize('body,error', [(stream(finish='length'),provider.ContextWindowError),('data: [DONE]\n\n',provider.InvalidOutput),(stream(''),provider.InvalidOutput),(stream(finish='tool_calls'),provider.InvalidOutput),('data: broken\n\n',provider.InvalidOutput)])
def test_incomplete_streams_are_rejected(body,error):
    async def run():
        async with httpx.AsyncClient(base_url='http://model.invalid/',transport=httpx.MockTransport(lambda r:httpx.Response(200,text=body))) as client:
            return await provider.completion(client,{'messages':[]})
    with pytest.raises(error):asyncio.run(run())


@pytest.mark.parametrize('model', ['test', 'qwen3.5:4b', 'qwen3.5:9b'])
def test_verify_negotiates_only_explicit_format_failures(monkeypatch, model):
    calls=[]
    def handle(request):
        calls.append(request.url.path)
        body=json.loads(request.content)
        if model == 'qwen3.5:9b':
            assert body['presence_penalty'] == 0
        else:
            assert 'presence_penalty' not in body
        if body.get('response_format',{}).get('type')=='json_schema':
            return httpx.Response(400,json={'error':'unsupported response_format json_schema'})
        return httpx.Response(200,text=stream('{"ok":true}' if body.get('response_format') else '预算15万元，不是50万元。这个暂时还没有确认。'))
    monkeypatch.setattr(engine,'client',lambda c:httpx.AsyncClient(base_url=CONFIG['base_url']+'/',transport=httpx.MockTransport(handle)))
    # Switching away from an eligible model must also discard its internal marker.
    value=asyncio.run(engine.verify({**CONFIG, 'model': model, 'packet_presence_penalty': 0}))
    assert value['structured_output_mode']=='json_object'
    assert value['observed_context_length'] is None and value['model_digest'] is None
    assert value['token_usage_mode'] == 'unavailable'
    assert calls==['/gateway/v1/chat/completions']*4


def test_key_binding_rotation_and_snapshots(local,monkeypatch):
    async def verify(config):return {**config,'verified_at':'today','structured_output_mode':'json_object','prompt_version':engine.PROMPT_VERSION}
    monkeypatch.setattr(engine,'verify',verify)
    identity={k:v for k,v in CONFIG.items() if k!='protocol'}
    with TestClient(api.create_app()) as client:
        for key in ('test-secret-one','test-secret-two'):
            verified=client.post(ROOT+'/verify',json={**identity,'api_key':key})
            assert verified.status_code==200,verified.text
            assert key not in verified.text and verified.json()['has_api_key']
            mismatch=client.put(ROOT,json={**identity,'api_key':'different','enabled':True,'verification_token':verified.json()['verification_token']})
            assert mismatch.status_code==409
            saved=client.put(ROOT,json={**identity,'enabled':True,'verification_token':verified.json()['verification_token']})
            assert saved.status_code==200,saved.text
            assert key not in saved.text and key not in client.get(ROOT).text
            config=store.settings();assert store.credential(config)==key
            if key.endswith('one'):
                reference=config['credential_ref']
            else:assert config['credential_ref']==reference
        assert client.post(ROOT+'/discover',json={**identity,'api_key':'secret'}).status_code==422  # Undeclared fields.
        response=client.put(ROOT,json={**identity,'api_key':'','enabled':False})
        assert response.status_code==200 and not response.json()['has_api_key']
        assert store.credential(config)==''  # Same-connection rotation/clear applies to references.
        other=client.put(ROOT,json={**identity,'base_url':'https://other.invalid/v1','enabled':False})
        assert other.status_code==200 and not other.json()['has_api_key']
        invalid = client.post(ROOT+'/verify', json={**identity, 'api_key': 'private-marker' * 1000})
        assert invalid.status_code == 422 and 'private-marker' not in invalid.text
        schema=client.get('/openapi.json').json()['components']['schemas']
        assert schema['Connection']['properties']['api_key']['anyOf'][0]['writeOnly']
    with db.connect() as c:
        assert 'test-secret' not in c.execute('SELECT value_json FROM optimization_settings').fetchone()[0]


def test_v21_upgrade_retains_historical_snapshots(local):
    job=db.create_job('asr','test',{})
    db.update_job(job['id'],state='succeeded',result_json={'segments':[]})
    legacy={'base_url':'http://localhost:11434','model':'old','context_length':32768,'use_proxy':False,'enabled':True,'verified_at':'old'}
    store.save_settings(legacy)
    from test_optimization import source
    result = source(1)
    db.update_job(job['id'], result_json=result)
    snapshot = {**legacy, 'prompt_version': engine.PROMPT_VERSION}
    run, _ = store.create(job['id'], 'legacy-snapshot-upgrade', False, snapshot, engine.source_hash(result), [], engine.prepare_units(result))
    before = store.parts(run, 0)
    with db.connect() as c:
        c.execute("UPDATE schema_meta SET version=21")
    db.init_db();db.init_db()
    config=store.settings()
    assert config['base_url']=='http://localhost:11434/v1' and not config['enabled'] and config['verified_at'] is None
    assert provider.effective(legacy)['base_url']==config['base_url'] and legacy['base_url']=='http://localhost:11434'
    assert db.get_job(job['id'])['state']=='succeeded'
    assert store.get(run)['config'] == snapshot and store.parts(run, 0) == before
    assert store.get(run)['state'] == 'paused'
    with db.connect() as c:assert c.execute('SELECT version FROM schema_meta').fetchone()[0]== 28


def test_stream_cancellation_closes_response():
    async def run():
        arrived = asyncio.Event()
        closed = asyncio.Event()
        class Pending(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b': pending\n\n'
                arrived.set()
                await asyncio.Event().wait()
            async def aclose(self):
                closed.set()
        async with httpx.AsyncClient(base_url=CONFIG['base_url']+'/', transport=httpx.MockTransport(lambda r:httpx.Response(200,stream=Pending()))) as client:
            task = asyncio.create_task(provider.completion(client,{'messages':[]}))
            await asyncio.wait_for(arrived.wait(),1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert closed.is_set()
    asyncio.run(run())


def test_outgoing_key_is_bound_to_endpoint(local):
    config={**CONFIG,'_api_key':'outgoing-model-key'}
    store.save_settings(config)
    saved=store.settings()
    async def run():
        async with engine.client(saved) as connection:
            request=connection.build_request('GET','models')
            assert request.headers['authorization']=='Bearer outgoing-model-key'
            assert str(request.url)==CONFIG['base_url']+'/models'
    asyncio.run(run())
    with pytest.raises(ValueError,match='endpoint mismatch'):
        store.credential({**saved,'base_url':'https://other.invalid/v1'})


@pytest.mark.parametrize('behavior,mode,probes', [
    ('native','automatic',0), ('on_request','include_usage',1), ('reject','unavailable',1),
    ('missing','unavailable',1), ('opt_out','unavailable',0),
])
def test_verification_negotiates_usage_only_on_synthetic_requests(monkeypatch, behavior, mode, probes):
    seen = []
    def handle(request):
        body = json.loads(request.content)
        flag = body.get('stream_options', {}).get('include_usage')
        seen.append(flag)
        if flag and behavior == 'reject':
            return httpx.Response(400, json={'error':'unsupported stream_options'})
        content = '{"ok":true}' if body.get('response_format') else '预算15万元，不是50万元。这个暂时还没有确认。'
        events = stream(content)
        if behavior == 'native' or (flag and behavior == 'on_request'):
            events = events.replace('data: [DONE]', 'data: {"choices":[],"usage":{"total_tokens":17}}\n\ndata: [DONE]')
        return httpx.Response(200, text=events, headers={'content-type':'text/event-stream'})
    monkeypatch.setattr(engine, 'client', lambda c:httpx.AsyncClient(base_url=CONFIG['base_url']+'/',transport=httpx.MockTransport(handle)))
    extra = {'stream_options': {'include_usage': False}} if behavior == 'opt_out' else {}
    config = {**CONFIG, 'extra_body':extra}
    verified = asyncio.run(engine.verify(config))
    assert verified['token_usage_mode'] == mode and seen.count(True) == probes
    assert verified['extra_body'] == extra
    new_request = provider.request_body(verified, {'messages':[]})
    assert bool(new_request.get('stream_options', {}).get('include_usage')) == (mode == 'include_usage')
    assert not provider.request_body(config, {'messages':[]}).get('stream_options', {}).get('include_usage')
