from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from audio_intel import api, db, optimization_engine as engine, optimization_store as store
from audio_intel import optimization_topics as topics, optimization_budget as calls
from test_optimization import configured, job, source, url, wait
from test_voiceprint_groups_exports import local


@pytest.fixture
def model(configured, monkeypatch):
    requests = []

    async def structured(config, payload, activity=None):
        identifier = calls.reserve()
        try:
            items = json.loads(next(m['content'] for m in reversed(payload['messages']) if m['role']=='user' and m['content'].startswith('[')))
            requests.append(items)
            return {'topics': [{'title': '预算与测试', 'summary': '讨论预算和测试安排。',
                                'source_ids': [i['id'] for i in items]}], 'unclassified': []}
        finally:
            calls.finish(identifier, None)

    monkeypatch.setattr(engine, 'structured', structured)
    return requests


def test_topics_publish_together_and_etag_is_metadata_only(model, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = engine.structured

    async def blocked(*args, **kwargs):
        entered.set()
        while not release.is_set(): await asyncio.sleep(.01)
        return await original(*args, **kwargs)

    monkeypatch.setattr(engine, 'structured', blocked)
    identifier = job(source(3))
    with TestClient(api.create_app()) as client:
        accepted = client.post(url(identifier), json={'analyze_topics': True}, headers={'Idempotency-Key': 'topic-workflow'})
        assert accepted.status_code == 202, accepted.text
        run = accepted.json()['candidate']['id']
        assert entered.wait(4)
        status = client.get(url(identifier)).json()
        assert status['published'] is None and status['candidate']['phase'] == 'topics'
        assert client.get(url(identifier) + '/topics', params={'run_id': run}).json()['result'] is None
        assert client.get(url(identifier) + '/result', params={'run_id': run}).status_code == 409
        release.set()
        wait(client, identifier, lambda s: bool(s['published']))
        response = client.get(url(identifier) + '/topics', params={'run_id': run})
        catalogue = response.json()['result']
        assert catalogue['topics'][0]['ranges'][0]['start'] == 0
        assert len(model) == 1
        assert store.get(run)['config']['reading_plan_ready']
        v2 = client.get(url(identifier)+'/topics',params={'run_id':run,'include_outline':True,'outline_version':2})
        assert v2.json()['outline']['version']==2
        assert client.get(url(identifier)+'/topics',params={'run_id':run,'include_outline':True}).json()['outline']['version']==1
        for suffix in ('', '/estimate', '/pause', '/resume'):
            assert client.post(url(identifier)+'/topics'+suffix,json={'run_id':run},headers={'Idempotency-Key':'bound-topics'+suffix.replace('/','-')}).status_code==409
        from audio_intel.reading_plan import freeze
        saved = store.units(run)
        freeze(run, db.get_job(identifier)['result'])
        assert store.units(run)==saved
        monkeypatch.setattr(topics, 'read', lambda *a: pytest.fail('304 read full catalogue'))
        assert client.get(url(identifier) + '/topics', params={'run_id': run}, headers={'If-None-Match': response.headers['etag']}).status_code == 304
        replay = client.post(url(identifier), json={'analyze_topics': True}, headers={'Idempotency-Key': 'topic-workflow'})
        assert replay.status_code == 200
        assert client.post(url(identifier), json={'analyze_topics': False}, headers={'Idempotency-Key': 'topic-workflow'}).status_code == 409


def test_disabled_path_and_auxiliary_replay_regeneration(model):
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        assert client.post(url(identifier), json={}, headers={'Idempotency-Key': 'without-topics'}).status_code == 202
        run = wait(client, identifier, lambda s: bool(s['published']))['published']['id']
        assert model == []
        endpoint = url(identifier) + '/topics'
        preflight = client.post(endpoint + '/estimate', json={'run_id': run})
        assert preflight.status_code == 200
        body = {'run_id': run, 'estimate_fingerprint': preflight.json()['fingerprint']}
        response = client.post(endpoint, json=body, headers={'Idempotency-Key': 'add-topics'})
        assert response.status_code == 202, response.text
        wait(client, identifier, lambda s: s['published']['topic_state'] == 'completed')
        assert len(model) == 1
        assert client.post(endpoint, json=body, headers={'Idempotency-Key': 'add-topics'}).status_code == 200
        assert client.post(endpoint, json={'run_id': run}, headers={'Idempotency-Key': 'reuse-topics'}).status_code == 200
        assert len(model) == 1
        before = store.units(run)
        assert client.post(endpoint, json={'run_id': run, 'regenerate': True}, headers={'Idempotency-Key': 'regenerate-topics'}).status_code == 202
        wait(client, identifier, lambda s: s['published']['topic_state'] == 'completed')
        assert len(model) == 2 and before == store.units(run)
        assert client.delete(url(identifier) + '?purge=true').status_code == 204
        with db.connect() as c:
            assert not c.execute('SELECT 1 FROM optimization_topic_nodes').fetchall()


def test_recurrence_ranges_speakers_scenes_and_word_precision():
    result = {'segments': [
        {'id': 1, 'text': 'Budget is undecided.', 'start': 1, 'end': 3, 'speaker': 'a', 'words': [{'text': 'Budget', 'start': 1, 'end': 1.5}, {'text': 'is', 'start': 1.5, 'end': 2}, {'text': 'undecided.', 'start': 2, 'end': 3}]},
        {'id': 2, 'text': 'Now discuss testing.', 'start': 3, 'end': 6, 'speaker': 'b'},
        {'id': 3, 'text': 'Return to the budget.', 'start': 8, 'end': 11, 'speaker': 'b'}]}
    atoms = topics.sources(result)
    indexed = {a['id']: a for a in atoms}
    intervals = topics.ranges(['0', '2'], indexed)
    assert len(intervals) == 2
    assert intervals[0]['timing_precision'] == 'word'
    assert intervals[1]['timing_precision'] == 'segment'
    assert intervals[1]['source_refs'][0]['segment_id'] == 3
    result['segments'][0]['words'][0]['text'] = 'wrong'
    assert topics.sources(result)[0]['timing_precision'] == 'segment'


def test_missing_foreign_or_duplicate_catalogue_members_are_rejected():
    indexed = {'0': {'source': 'We discuss budgets.'}, '1': {'source': 'We discuss testing.'}}
    value = {'topics': [{'title': 'Budget', 'summary': 'Discuss budgets.', 'source_ids': ['0']}], 'unclassified': []}
    with pytest.raises(engine.InvalidOutput, match='coverage'): topics.checked(value, indexed)
    value['topics'][0]['source_ids'] = ['0', 'foreign']
    with pytest.raises(engine.InvalidOutput, match='references'): topics.checked(value, indexed)
    value['topics'][0]['source_ids'] = ['0', '1']
    value['topics'].append(value['topics'][0])
    with pytest.raises(engine.InvalidOutput, match='Duplicate'): topics.checked(value, indexed, merge=True)


def test_invalid_topics_pause_without_rewriting_body_and_resume(model, monkeypatch):
    original = engine.structured
    async def invalid(*args, **kwargs):
        await original(*args, **kwargs)
        return {'topics': [], 'unclassified': []}
    monkeypatch.setattr(engine, 'structured', invalid)
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        assert client.post(url(identifier), json={'analyze_topics': True}, headers={'Idempotency-Key': 'bad-topics'}).status_code == 202
        state = wait(client, identifier, lambda s: bool(s['candidate'] and s['candidate']['state'] == 'paused'))
        run = state['candidate']['id']
        assert state['published'] is None and len(model) == 3
        before = store.units(run)
        monkeypatch.setattr(engine, 'structured', original)
        assert client.post(url(identifier) + '/resume', json={'run_id': run}).status_code == 200
        wait(client, identifier, lambda s: bool(s['published']))
        assert [u['original_text'] for u in store.units(run)] == [u['original_text'] for u in before]
        assert store.get(run)['config']['reading_plan_ready'] and len(model) == 4


def test_v25_migration_is_additive(configured):
    identifier = job(source(1))
    before = db.get_job(identifier)
    with db.connect() as c: c.execute('UPDATE schema_meta SET version=25')
    db.init_db(); db.init_db()
    assert before == db.get_job(identifier)
    with db.connect() as c:
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0] == 28
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_topic_exports_use_same_replacements_and_timing(tmp_path):
    from audio_intel import transcript_export, transcript_docx, transcript_html
    from docx import Document
    import io
    result = {'segments': [], 'topics': [{'id': 't1', 'title': 'Private budget', 'summary': 'Discuss Private budget.',
                                        'ranges': [{'start': 1, 'end': 4, 'timing_precision': 'segment', 'source_refs': []}]}]}
    policy = transcript_export.TranscriptExportRequest(replacements=[{'source': 'Private', 'target': 'Public'}])
    safe = transcript_export.export_result(result, policy)
    assert safe['topics'][0]['title'] == 'Public budget'
    docx = Document(io.BytesIO(transcript_docx.render('Topics', safe, tmp_path)))
    assert 'Public budget' in '\n'.join(p.text for p in docx.paragraphs)
    pdf = transcript_html.render('Topics', safe, tmp_path).decode()
    assert 'Public budget' in pdf


def test_failed_topic_repairs_only_affected_sources(model, monkeypatch):
    requests = []
    replies = iter([
        {'topics': [{'title': '预算', 'summary': '讨论预算。', 'source_ids': ['0']},
                    {'title': '新增99次测试', 'summary': '讨论测试。', 'source_ids': ['1']}], 'unclassified': []},
        {'topics': [{'title': '测试', 'summary': '讨论测试。', 'source_ids': ['1']}], 'unclassified': []}])
    async def structured(config, payload, activity=None):
        items = json.loads(next(m['content'] for m in reversed(payload['messages']) if m['role']=='user' and m['content'].startswith('[')))
        requests.append([i['id'] for i in items])
        if len(requests) == 2:
            assert 'Topic 0 title' in payload['messages'][-1]['content']
            assert 'Topic 1 title' not in payload['messages'][-1]['content']
            assert 'omit the unsupported number' in payload['messages'][-1]['content']
            assert not any('99' in m['content'] for m in payload['messages'] if m['role'] == 'assistant')
        return next(replies)
    monkeypatch.setattr(engine, 'structured', structured)
    result = {'segments': [{'id': 0, 'text': '讨论预算。', 'start': 0, 'end': 3}, {'id': 1, 'text': '讨论测试。', 'start': 3, 'end': 6}]}
    identifier = job(result)
    with TestClient(api.create_app()) as client:
        assert client.post(url(identifier), json={'analyze_topics': True}, headers={'Idempotency-Key': 'repair-only-bad'}).status_code == 202
        wait(client, identifier, lambda s: bool(s['published']))
    assert requests == [['0', '1'], ['1']]


def test_membership_wire_requires_every_source_and_declared_topic_ids():
    items = [{'id': 'a', 'source': 'Discuss budget.'}, {'id': 'b', 'source': 'Budget again.'}]
    value = {'topics': [{'id': 't1', 'title': 'Budget', 'summary': 'Discuss budget.'}], 'assignments': {'a': ['t1'], 'b': ['t1']}}
    normalized = topics.internal(value, items)
    assert normalized['topics'][0]['source_ids'] == ['a', 'b']
    del value['assignments']['b']
    with pytest.raises(engine.InvalidOutput, match='membership keys'): topics.internal(value, items)
    value['assignments']['b'] = ['not-a-topic']
    with pytest.raises(engine.InvalidOutput, match='Unknown topic ID'): topics.internal(value, items)


def test_summary_fallback_is_explicit_and_from_owned_original_source():
    original = 'We will consider the deployment proposal.'
    value = {'topics': [{'title': 'Deployment', 'summary': 'Deployed 999 instances.', 'source_ids': ['a']}], 'unclassified': []}
    checked = topics.checked(value, {'a': {'source': original}})
    assert checked['topics'][0]['summary_method'] == 'source_excerpt'
    assert checked['topics'][0]['summary'] in original
    assert '999' not in checked['topics'][0]['summary']


def test_budget_resume_reuses_committed_packets(model):
    result = {'segments': [{'id': 0, 'text': '讨论预算和测试。' * 400, 'start': 0, 'end': 100, 'speaker': 'a'}]}
    identifier = job(result)
    config = {**store.settings(), 'prompt_version': engine.PROMPT_VERSION, 'budget_policy_version': 1,
              'max_llm_calls': 1, 'call_estimate': {}, 'analyze_topics': True}
    run, _ = store.create(identifier, 'topic-budget-resume', False, config, engine.source_hash(result), [], engine.prepare_units(result), topic_result=result)
    store.transition(run, 'running', ('queued',))
    with pytest.raises(calls.BudgetExhausted): asyncio.run(topics.process(run, result))
    calls.pause(run)
    with db.connect() as c:
        done = c.execute('SELECT node_key FROM optimization_topic_nodes WHERE run_id=? AND value_json IS NOT NULL', (run,)).fetchall()
    assert len(done) == 1
    first_key = done[0][0]
    store.resume(run, 100)
    store.transition(run, 'running', ('queued',))
    asyncio.run(topics.process(run, result))
    assert store.get(run)['topic_state'] == 'completed'
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM optimization_calls WHERE run_id=? AND node_key=?', (run, first_key)).fetchone()[0] == 1


def test_deleted_analysis_is_not_an_internal_server_error(configured):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as raised: topics.read('f' * 32)
    assert raised.value.status_code == 404


def test_english_examples_ignore_brief_chinese_background_speech():
    items = [{'id': '0', 'source': 'Discuss the video generation cost and rendering performance. 好的。'}]
    for config in ({}, {'english_source': True}):
        payload = topics.request(config, items)
        example = json.loads(payload['messages'][2]['content'])
        assert example['topics'][0]['title'] == 'Deployment timing'
    declared = topics.request({'english_source': True}, [{'id': '0', 'source': '好的。'}])
    assert 'Write topic titles and summaries in English' in declared['messages'][0]['content']


def test_wrong_language_rebuilds_packet_without_repeating_body(model, monkeypatch):
    requests = []
    result = {'language': 'en', 'segments': [
        {'id': 0, 'text': 'Discuss deployment timing.', 'start': 0, 'end': 3},
        {'id': 1, 'text': 'Discuss testing scope.', 'start': 3, 'end': 6}]}

    async def structured(config, payload, activity=None):
        assert config['english_source']
        items = json.loads(payload['messages'][-1]['content'])
        requests.append([i['id'] for i in items])
        title = '部署与测试' if len(requests) == 1 else 'Deployment and testing'
        return {'topics': [{'title': title, 'summary': 'Discuss deployment and testing.',
                            'source_ids': [i['id'] for i in items]}], 'unclassified': []}

    monkeypatch.setattr(engine, 'structured', structured)
    identifier = job(result)
    config = {**store.settings(), 'prompt_version': engine.PROMPT_VERSION,
              'budget_policy_version': 1, 'max_llm_calls': 10, 'call_estimate': {},
              'analyze_topics': True}
    run, _ = store.create(identifier, 'english-topic-repair', False, config, engine.source_hash(result),
                          [], engine.prepare_units(result), topic_result=result)
    store.transition(run, 'running', ('queued',))
    before = store.units(run)
    asyncio.run(topics.process(run, result))
    assert store.get(run)['topic_state'] == 'completed'
    assert requests == [['0', '1'], ['0', '1']]
    assert store.units(run) == before


def test_ambiguous_packet_can_remain_entirely_unclassified():
    value = {'topics': [], 'unclassified': ['0']}
    for merge in (False, True):
        assert topics.checked(value, {'0': {'source': 'That one over there.'}}, merge=merge) == value
    schema = topics.request({}, [{'id': '0', 'source': 'That one over there.'}], merge=True)['format']
    assert schema['properties']['assignments']['properties']['0'].get('minItems', 0) == 0


def test_merge_unclassified_members_keep_original_ranges(model, monkeypatch):
    result = {'segments': [{'id': i, 'text': 'Discuss deployment timing. ' * 60,
                            'start': i * 100, 'end': i * 100 + 99} for i in range(4)]}

    async def structured(config, payload, activity=None):
        items = json.loads(payload['messages'][-1]['content'])
        merge = 'HIGH-LEVEL table of contents' in payload['messages'][0]['content']
        return {'topics': [] if merge else [{'id': 't1', 'title': 'Deployment timing', 'summary': 'Discuss deployment timing.'}],
                'assignments': {i['id']: [] if merge else ['t1'] for i in items}}

    monkeypatch.setattr(engine, 'structured', structured)
    identifier = job(result)
    config = {**store.settings(), 'prompt_version': engine.PROMPT_VERSION, 'analyze_topics': True,
              'budget_policy_version': 1, 'max_llm_calls': 100, 'call_estimate': {}}
    run, _ = store.create(identifier, 'merge-unclassified', False, config, engine.source_hash(result),
                          [], engine.prepare_units(result), topic_result=result)
    store.transition(run, 'running', ('queued',))
    asyncio.run(topics.process(run, result))
    catalogue = topics.read(run)['result']
    assert catalogue['topics'] == [] and catalogue['unclassified']
    refs = [ref for interval in catalogue['unclassified'] for ref in interval['source_refs']]
    assert {r['segment_id'] for r in refs} == {0, 1, 2, 3}
    assert not catalogue['no_substantive_content']


def test_nonreducing_partition_stops_without_replaying_merge_levels(model, monkeypatch):
    original_pack = topics.pack

    def small_window(config, items, *, merge=False):
        return [[item] for item in items] if merge else original_pack(config, items)

    monkeypatch.setattr(topics, 'pack', small_window)
    result = {'segments': [{'id': 0, 'text': '讨论预算和测试。' * 500, 'start': 0, 'end': 100}]}
    identifier = job(result)
    config = {**store.settings(), 'prompt_version': engine.PROMPT_VERSION, 'analyze_topics': True,
              'budget_policy_version': 1, 'max_llm_calls': 100, 'call_estimate': {}}
    run, _ = store.create(identifier, 'no-merge-progress', False, config, engine.source_hash(result),
                          [], engine.prepare_units(result), topic_result=result)
    store.transition(run, 'running', ('queued',))
    with pytest.raises(engine.ContextWindowError, match='no progress'):
        asyncio.run(topics.process(run, result))
    with db.connect() as c:
        maps = c.execute("SELECT count(*) FROM optimization_topic_nodes WHERE run_id=? AND node_key LIKE 'map:%'", (run,)).fetchone()[0]
        assert not c.execute("SELECT 1 FROM optimization_topic_nodes WHERE run_id=? AND node_key LIKE 'merge:1:%'", (run,)).fetchall()
    assert maps > 1 and len(model) == maps * 2
    assert topics.read(run)['result'] is None


def test_v2_hints_do_not_change_map_requests_or_membership_namespace():
    base = {'context_length': 32768, 'max_output_tokens': 4096}
    items = [{'id': '0', 'source': 'Discuss deployment.'}]
    config = {**base, '_topic_scene_context': {'titles': {'0': 'Deployment planning'}, 'sources': {'0': ['0']}}}
    assert topics.request(config, items) == topics._request_v1(base, items)
    merged = topics.request(config, items, merge=True)
    assert 'Deployment' in merged['messages'][0]['content']
    assert json.loads(merged['messages'][-1]['content']) == items
    assert set(merged['format']['properties']['assignments']['properties']) == {'0'}


def test_existing_v1_checkpoints_and_original_topic_export(model):
    identifier = job(source(3))
    with TestClient(api.create_app()) as client:
        accepted = client.post(url(identifier), json={'analyze_topics': True}, headers={'Idempotency-Key':'outline-export'})
        run = accepted.json()['candidate']['id']
        wait(client, identifier, lambda s: bool(s['published']))
        outlined = client.get(url(identifier)+'/topics', params={'run_id':run,'include_outline':True})
        assert outlined.status_code == 200
        assert outlined.json()['outline']['groups'][0]['primary_item_ids']
        assert client.get(url(identifier)+'/topics',params={'run_id':run,'include_outline':True},headers={'If-None-Match':outlined.headers['etag']}).status_code==304
        for format in ('html','docx'):
            exported = client.post(f'/api/v1/jobs/{identifier}/transcript-export',json={'format':format,'text_version':'original','organization':'topic','optimization_id':run})
            assert exported.status_code == 200, exported.text[:200]
        # New source-bound runs reject narrow regeneration; the old contract remains supported.
        assert client.post(url(identifier)+'/topics/estimate',json={'run_id':run}).status_code==409
        legacy_config = {**store.get(run)['config'], 'reading_plan_version': 1}
        with db.connect() as c:
            c.execute('UPDATE transcript_optimizations SET config_json=? WHERE id=?',(store.encode(legacy_config),run))
        # Reconstruct a pre-v2 checkpoint without rewriting source or prose.
        result = db.get_job(identifier)['result']
        with db.connect() as c:
            c.execute('DELETE FROM optimization_topic_nodes WHERE run_id=?',(run,))
            c.execute('UPDATE optimization_topics SET version=1,refs_json=? WHERE run_id=?',(store.encode([{k:v for k,v in a.items() if k!='text'} for a in topics.sources(result)]),run))
            c.execute("INSERT INTO optimization_topic_nodes(run_id,node_key,ids_json) VALUES(?,'map:0:0',?)",(run,store.encode([a['id'] for a in topics.sources(result)])))
            c.execute("UPDATE transcript_optimizations SET topic_state='paused',topic_completed_nodes=0,topic_total_nodes=1 WHERE id=?",(run,))
        assert client.post(url(identifier)+'/topics/resume',json={'run_id':run},headers={'Idempotency-Key':'legacy-outline-resume'}).status_code==202
        wait(client,identifier,lambda s:s['published']['topic_state']=='completed')
        with db.connect() as c:assert c.execute('SELECT version FROM optimization_topics WHERE run_id=?',(run,)).fetchone()[0]==1
