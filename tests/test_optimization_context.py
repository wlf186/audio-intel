"""Requested windows are immutable budgets, not model metadata or server defaults."""
import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from audio_intel import api, optimization_engine as engine, optimization_store as store
from test_optimization import PROFILE, configured, job, source, start, url, wait
from test_voiceprint_groups_exports import local


@pytest.mark.parametrize('requested', [16384, 32768, 65536])
def test_client_budget_does_not_claim_server_allocation(monkeypatch, requested):
    from test_optimization_provider import CONFIG, stream
    calls = []
    def handle(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        return httpx.Response(200, text=stream('{"ok":true}' if body.get('response_format') else '预算15万元，不是50万元。这个暂时还没有确认。'))
    monkeypatch.setattr(engine, 'client', lambda config: httpx.AsyncClient(base_url='http://model.invalid/v1/', transport=httpx.MockTransport(handle)))
    result = asyncio.run(engine.verify({**CONFIG, 'context_length': requested}))
    assert result['context_length'] == requested
    assert result['observed_context_length'] is result['model_context_length'] is None
    assert all(path == '/v1/chat/completions' for path, body in calls)
    assert all('options' not in body and 'truncate' not in body for path, body in calls)


@pytest.mark.parametrize('status,message', [(500, 'CUDA out of memory'), (413, 'input exceeds the context length')])
def test_capacity_errors_escape_generation_fallback(monkeypatch, status, message):
    from test_optimization_provider import CONFIG
    monkeypatch.setattr(engine, 'client', lambda config: httpx.AsyncClient(base_url='http://model.invalid/v1/', transport=httpx.MockTransport(lambda r: httpx.Response(status, json={'error': message}))))
    with pytest.raises(engine.ContextWindowError):
        asyncio.run(engine.generate(CONFIG, [{'ordinal': 0, 'original_text': '预算15万元。'}], [], '', '', None))


def test_context_configuration_binding_defaults_and_schema(local, monkeypatch):
    root = '/api/v1/settings/transcript-optimization'
    async def verify(config):
        return {**PROFILE, **config, 'observed_context_length': config['context_length']}
    monkeypatch.setattr(engine, 'verify', verify)
    identity = {k: PROFILE[k] for k in ('base_url', 'model', 'use_proxy')}
    with TestClient(api.create_app()) as client:
        assert client.get(root).json()['context_length'] == 16384
        for invalid in [8192, 0, -1, 16384.5, '65536', True]:
            assert client.post(root+'/verify', json={**identity, 'context_length': invalid}).status_code == 422
            assert client.put(root, json={**identity, 'context_length': invalid}).status_code == 422
        verified = client.post(root+'/verify', json={**identity, 'context_length': 65536}).json()
        payload = {**identity, 'enabled': True, 'verification_token': verified['verification_token']}
        assert client.put(root, json={**payload, 'context_length': 32768}).status_code == 409
        saved = client.put(root, json=payload).json()  # Legacy omission inherits the probe.
        assert saved['context_length'] == saved['observed_context_length'] == 65536
        assert client.post(root+'/verify', json=identity).json()['context_length'] == 65536
        assert client.post(root+'/verify', json={**identity, 'model': 'new-model'}).json()['context_length'] == 16384
        assert client.put(root, json={**identity, 'enabled': True, 'context_length': 32768}).status_code == 409
        draft = client.put(root, json={**identity, 'enabled': False, 'context_length': 32768}).json()
        assert draft['context_length'] == 32768 and not draft['verified_at'] and draft['observed_context_length'] is None
        store.save_settings(PROFILE)
        legacy = client.get(root).json()
        assert legacy['context_length'] == 32768 and legacy['observed_context_length'] is None
        assert client.put(root, json={**identity, 'enabled': True}).json()['context_length'] == 32768
        schemas = client.get('/openapi.json').json()['components']['schemas']
        assert 'context_length' in schemas['ModelConnection']['properties']
        assert 'observed_context_length' in schemas['ConfigurationResponse']['properties']


def test_documented_context_configuration_example(monkeypatch):
    import re
    from audio_intel.api_docs import API_DESCRIPTION
    following = API_DESCRIPTION.split('## 上下文配置示例 / Context configuration example', 1)[1]
    section = re.split(r'^##[ \t]+', following, maxsplit=1, flags=re.M)[0]
    blocks = re.findall(r'```python\n(.*?)```', section, re.S)
    assert len(blocks) == 1
    calls = []
    def handle(request):
        body = json.loads(request.content)
        assert body['context_length'] == 16384
        assert request.headers['Authorization'] == 'Bearer synthetic-key'
        calls.append(request.url.path)
        if request.url.path.endswith('/verify'):
            return httpx.Response(200, json={'verification_token': 'window-bound'})
        assert body['verification_token'] == 'window-bound' and body['enabled']
        return httpx.Response(200, json=body)
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    for k, v in {'AUDIO_INTEL_BASE_URL': 'http://test.invalid', 'AUDIO_INTEL_API_KEY': 'synthetic-key', 'OPTIMIZATION_BASE_URL': 'http://model.invalid/v1', 'OPTIMIZATION_MODEL': 'test-model'}.items():
        monkeypatch.setenv(k, v)
    exec(compile(blocks[0], '<context-example>', 'exec'), {})
    assert calls == ['/api/v1/settings/transcript-optimization/verify', '/api/v1/settings/transcript-optimization']


@pytest.mark.parametrize('stage', ['generate', 'repair_seam', 'review'])
def test_capacity_failure_pauses_and_resume_keeps_snapshot_and_checkpoints(configured, monkeypatch, stage):
    result = source(1)
    result['segments'][0]['text'] = '那个阿特拉斯预算还没有确认' * 60
    identifier = job(result)
    original = getattr(engine, stage)
    calls = 0
    async def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if stage == 'generate' and calls == 1:
            return await original(*args, **kwargs)
        raise engine.ContextWindowError('请求窗口 32768，观测窗口 16384')
    monkeypatch.setattr(engine, stage, fail)
    # The normal test fixture only replaces generation/review, not seam repair.
    async def seam(config, left, right, hotwords):
        return left['text'], right['text']
    if stage != 'repair_seam':
        monkeypatch.setattr(engine, 'repair_seam', seam)
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        paused = wait(client, identifier, lambda s: bool(s['candidate'] and s['candidate']['state'] == 'paused'))
        assert paused['published'] is None
        candidate = paused['candidate']
        assert '32768' in candidate['error']
        run = candidate['id']
        before = store.parts(run, 0)
        assert any(p['text'] is not None for p in before)
        assert not any(p['review_outcome'] for p in before)
        store.save_settings({**PROFILE, 'context_length': 16384})
        monkeypatch.setattr(engine, stage, seam if stage == 'repair_seam' else original)
        assert client.post(url(identifier)+'/resume', json={'run_id': run}).status_code == 200
        wait(client, identifier, lambda s: bool(s['published']))
        assert store.get(run)['config']['context_length'] == 32768
        after = store.parts(run, 0)
        assert all(p['text'] == after[i]['text'] for i, p in enumerate(before) if p['text'] is not None)
