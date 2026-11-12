import asyncio
from concurrent.futures import ThreadPoolExecutor
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from audio_intel import api, db, optimization_budget as budget, optimization_batch as batch
from audio_intel import optimization_engine as engine, optimization_store as store, optimization_provider as provider
from test_optimization import configured, local, job, source, url, wait, PROFILE


def running(limit=3):
    data = source(1)
    units = engine.prepare_units(data)
    config = {**PROFILE, 'budget_policy_version': 1, 'max_llm_calls': limit, 'call_estimate': {}}
    run, _ = store.create(job(data), 'budget-test-call-key', False, config, engine.source_hash(data), [], units)
    store.transition(run, 'running', ('queued',))
    return run


def test_reservations_are_atomic_and_survive_restart(local):
    run = running(3)
    def attempt(_):
        try:
            with budget.scope(run, 'generation'):
                return budget.reserve()
        except budget.BudgetExhausted:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(x is not None for x in pool.map(attempt, range(12))) == 3
    db.init_db()
    assert budget.usage(run)['calls_used'] == 3
    assert budget.usage(run)['failed_or_interrupted'] == 3
    with budget.scope(run, 'review'), pytest.raises(budget.BudgetExhausted):
        budget.reserve()


def test_physical_failed_calls_count_and_exhaustion_is_not_fallback(local, monkeypatch):
    run = running(2)
    requests = []
    async def offline(connection, body, activity=None):
        requests.append(body)
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(provider, 'completion', offline)
    async def execute():
        with budget.scope(run, 'group'):
            for _ in range(2):
                with pytest.raises(httpx.ConnectError):
                    await engine.streamed_text(PROFILE, {'messages': [{'role': 'user', 'content': 'hello'}]})
            with pytest.raises(budget.BudgetExhausted):
                await engine.streamed_text(PROFILE, {'messages': [{'role': 'user', 'content': 'hello'}]})
    asyncio.run(execute())
    assert len(requests) == 2
    assert budget.usage(run)['stages'] == {'group': 2}


def test_estimate_and_temporary_limit_have_no_remote_side_effects(configured, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError('preflight must not call a model')
    monkeypatch.setattr(engine, 'streamed_text', forbidden)
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        root = url(identifier)
        estimate = client.post(root+'/estimate', json={}).json()
        assert estimate['estimated_calls'] == 6 and estimate['recommended_limit'] == 20
        assert store.status(identifier)['candidate'] is None
        headers = {'Idempotency-Key': 'budget-temporary-test-key'}
        rejected = client.post(root, json={'max_llm_calls': 1}, headers=headers)
        assert rejected.status_code == 409
        assert rejected.json()['code'] == 'optimization_budget_insufficient'
        assert store.status(identifier)['candidate'] is None
        accepted = client.post(root, json={'max_llm_calls': 25}, headers=headers)
        assert accepted.status_code == 202
        assert accepted.json()['candidate']['calls_limit'] == 25
        assert store.settings()['max_llm_calls'] == 300
        assert client.post(root, json={'max_llm_calls': 25}, headers=headers).status_code == 200
        assert client.post(root, json={'max_llm_calls': 26}, headers=headers).status_code == 409


def test_packet_recommendation_covers_repair_overrun_without_raising_limit(local, monkeypatch):
    from audio_intel import optimization_packet_store as packets
    async def forbidden(*args, **kwargs):
        raise AssertionError('budget planning must not call a model')
    monkeypatch.setattr(engine, 'streamed_text', forbidden)
    # A complete 20-request plan needed 31 actual attempts during validation.
    plan = [('generation', ['0'])] * 10 + [('finalization', ['0'])] * 9 + [('group', ['0'])]
    monkeypatch.setattr(packets, 'compile_plan', lambda *args: plan)
    config = {**PROFILE, 'packet_version': 1, 'max_llm_calls': 30}
    units = engine.prepare_units(source(1))
    value = budget.estimate(config, units, [])
    assert value['estimated_calls'] == value['minimum_limit'] == 20
    assert value['recommended_limit'] >= 31
    assert value['can_start'] and not value['sufficient']
    assert value['max_llm_calls'] == config['max_llm_calls'] == 30
    temporary = budget.estimate(config, units, [], limit=value['recommended_limit'])
    assert temporary['sufficient'] and config['max_llm_calls'] == 30
    assert temporary['fingerprint'] == value['fingerprint']
    resumed = budget.estimate(config, units, [], used=7)
    assert resumed['minimum_limit'] == 27 and resumed['recommended_limit'] >= 38
    monkeypatch.setattr(budget, '_legacy_breakdown', lambda *args: {'generation': 10, 'review': 10})
    assert budget.estimate({**config, 'packet_version': None}, units, [])['recommended_limit'] == 30


def test_manager_pauses_on_budget_and_resume_does_not_reset_usage(configured, monkeypatch):
    from audio_intel import optimization_api as routes
    async def generate(config, parts, *args):
        call = budget.reserve()
        budget.finish(call, None)
        return [(parts[0]['ordinal'], '已整理。')]
    monkeypatch.setattr(engine, 'generate', generate)
    identifier = job(source(2))
    # A recovery storm can exceed the estimate. Keep the original checkpoints.
    with TestClient(api.create_app()) as client:
        response = client.post(url(identifier), json={'max_llm_calls': 10}, headers={'Idempotency-Key': 'pause-budget-test-key'})
        run = response.json()['candidate']['id']
        done = wait(client, identifier, lambda s: bool(s['published']))
        assert done['published']['calls_used'] == 2
        with db.connect() as c:
            c.execute("UPDATE transcript_optimizations SET state='running',published=0,calls_limit=calls_used WHERE id=?", (run,))
        async def resume_work():
            with budget.scope(run, 'generation'):
                with pytest.raises(budget.BudgetExhausted):
                    budget.reserve()
            budget.pause(run)
        asyncio.run(resume_work())
        assert store.get(run)['state'] == 'paused'
        with db.connect() as c:
            budget.raise_limit(c, run, 20)
        assert store.get(run)['calls_used'] == 2
        assert len([p for p in store.units(run) if p['text']]) == 2


def test_output_limit_and_batch_packing_share_capacity_rules():
    config = {**PROFILE, 'max_output_tokens': 1024, 'batch_max_items': 4}
    items = [{'id': str(i), 'original_text': '预算尚未确认。'*12} for i in range(4)]
    packets = batch.pack(config, 'generation', items, [])
    assert len(packets) > 1
    for packet in packets:
        payload = batch.payload(config, 'generation', packet, [])
        assert provider.request_body(config, payload)['max_tokens'] == 1024
        assert engine.request_fits(config, payload)
    body = provider.request_body({**config, 'extra_body': {'max_completion_tokens': 1024}}, {'messages': [], 'options': {'num_predict': 512}})
    assert body['max_completion_tokens'] == 512 and 'max_tokens' not in body


def test_partial_batches_accept_only_unique_valid_items(monkeypatch):
    async def response(*args):
        return {'items': [{'id': 'a', 'text': '预算三千元。'}, {'id': 'b', 'text': '文本一'}, {'id': 'b', 'text': '文本二'}]}
    monkeypatch.setattr(engine, 'structured', response)
    items = [{'id': 'a', 'original_text': '预算三千元。'}, {'id': 'b', 'original_text': '只能先测试。'}]
    assert asyncio.run(batch.request(PROFILE, 'generation', items, [])) == {'a': '预算三千元。'}


def test_legacy_usage_is_unknown_and_settings_limit_needs_no_probe(local):
    identifier = job(source(1))
    run, _ = store.create(identifier, 'legacy-budget-test-key', False, PROFILE, 'hash', [], engine.prepare_units(source(1)))
    assert budget.usage(run)['calls_used'] is None
    store.save_settings(PROFILE)
    with TestClient(api.create_app()) as client:
        config = {k: PROFILE[k] for k in ('base_url', 'model', 'use_proxy', 'context_length')}
        result = client.put('/api/v1/settings/transcript-optimization', json={**config, 'enabled': True, 'max_llm_calls': 123})
        assert result.status_code == 200, result.text
        assert result.json()['max_llm_calls'] == 123


def test_batched_workflow_accounts_physical_calls_and_checkpoints(configured, monkeypatch):
    from audio_intel import optimization_summaries as insights
    monkeypatch.setattr(insights, 'VERSION', 2)
    store.save_settings({**PROFILE, 'batch_max_items': 2})
    async def completion(connection, body, activity=None):
        data = json.loads(body['messages'][1]['content'])
        rows = []
        for item in data['items']:
            if 'candidate' in item:
                rows.append({'id': item['id'], 'verdict': 'pass', 'source_quote': '', 'candidate_quote': '', 'reason': ''})
            elif 'source' in item:
                rows.append({'id': item['id'], 'texts': ['周五先测试。']})
            else:
                rows.append({'id': item['id'], 'text': item['target'].replace('那个', '')})
        return json.dumps({'items': rows}, ensure_ascii=False)
    monkeypatch.setattr(provider, 'completion', completion)
    identifier = job(source(4))
    with TestClient(api.create_app()) as client:
        response = client.post(url(identifier), json={}, headers={'Idempotency-Key': 'batch-full-workflow-test'})
        assert response.status_code == 202
        completed = wait(client, identifier, lambda s: bool(s['published']))['published']
        usage = client.get(url(identifier)+'/usage', params={'run_id': completed['id']}).json()
        assert usage['stages'] == {'group': 2}
        assert usage['calls_used'] == 2  # Four insights, two physical requests.
        assert completed['summary_status']['counts']['group']['ready'] == 4


def test_new_budget_endpoints_are_documented(local):
    schema = api.create_app().openapi()
    root = '/api/v1/jobs/{job_id}/transcript-optimization'
    assert schema['paths'][root+'/estimate']['post']['operationId'] == 'estimateOptimizationCalls'
    assert schema['paths'][root+'/usage']['get']['operationId'] == 'getOptimizationCallUsage'
    assert 'max_llm_calls' in schema['components']['schemas']['CreateRequest']['properties']
    assert 'max_output_tokens' in schema['components']['schemas']['SaveConfiguration']['properties']


def test_summary_retry_uses_same_allowance_and_conflicts_on_changed_override(configured):
    from audio_intel import optimization_summaries as insights
    identifier = job(source(1))
    with TestClient(api.create_app()) as client:
        client.post(url(identifier), json={}, headers={'Idempotency-Key': 'budget-retry-source-key'})
        published = wait(client, identifier, lambda s: bool(s['published']))['published']
        run = published['id']
        with db.connect() as c:
            c.execute("UPDATE optimization_summaries SET state='unavailable',value_json='[]' WHERE run_id=?", (run,))
            c.execute("UPDATE transcript_optimizations SET summary_state='paused',calls_used=300 WHERE id=?", (run,))
        estimate = client.post(url(identifier)+'/estimate', json={'run_id': run}).json()
        assert estimate['breakdown']['generation'] == 0 and estimate['breakdown']['review'] == 0
        headers = {'Idempotency-Key': 'budget-summary-retry-key'}
        body = {'run_id': run, 'max_llm_calls': 320}
        accepted = client.post(url(identifier)+'/summaries/retry', json=body, headers=headers)
        assert accepted.status_code == 202, accepted.text
        assert store.get(run)['calls_used'] == 300 and store.get(run)['calls_limit'] == 320
        assert client.post(url(identifier)+'/summaries/retry', json=body, headers=headers).status_code == 200
        assert client.post(url(identifier)+'/summaries/retry', json={**body, 'max_llm_calls': 321}, headers=headers).status_code == 409


def test_preflight_binding_rechecks_configuration_without_consuming_key(configured):
    identifier = job(source(1))
    with TestClient(api.create_app()) as client:
        root = url(identifier)
        plan = client.post(root+'/estimate', json={}).json()
        store.save_settings({**store.settings(), 'context_length': 65536})
        headers = {'Idempotency-Key': 'bound-estimate-create-key'}
        body = {'max_llm_calls': 30, 'estimate_fingerprint': plan['fingerprint']}
        rejected = client.post(root, json=body, headers=headers)
        assert rejected.status_code == 409 and rejected.json()['code'] == 'optimization_estimate_stale'
        assert store.status(identifier)['candidate'] is None
        body['estimate_fingerprint'] = rejected.json()['estimate']['fingerprint']
        accepted = client.post(root, json=body, headers=headers)
        assert accepted.status_code == 202, accepted.text
        store.save_settings({**store.settings(), 'context_length': 16384})
        assert client.post(root, json=body, headers=headers).status_code == 200
