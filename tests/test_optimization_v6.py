"""Unified workflow lifecycle and immutable many-to-one source regression tests."""
import asyncio
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from audio_intel import api, db, optimization_engine as engine, optimization_store as store
from test_voiceprint_groups_exports import local
from test_optimization import PROFILE, configured, job, source, start, url, wait


def test_new_content_cannot_export_until_unified_check_finishes(configured, monkeypatch):
    async def slow_review(*args):
        await asyncio.sleep(60)
    monkeypatch.setattr(engine, 'review', slow_review)
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        status = wait(client, identifier, lambda s: s['candidate'] and s['candidate']['phase'] == 'review')
        run = status['candidate']
        assert status['published'] is None and run['state'] == 'running'
        assert client.get(url(identifier)+'/result', params={'run_id':run['id']}).status_code == 409
        response = client.post(f'/api/v1/jobs/{identifier}/transcript-export', json={'format':'txt','text_version':'optimized','optimization_id':run['id']})
        assert response.status_code == 409
        assert client.post(url(identifier)+'/pause', json={'run_id':run['id'],'phase':'review'}).status_code == 200
        assert store.get(run['id'])['state'] == 'paused'


def test_fact_uncertain_and_unavailable_never_rewrite_published_prose(configured, monkeypatch):
    outcomes = iter(['fact', 'uncertain', 'unavailable'])
    async def review(config, original, candidate, *args):
        outcome = next(outcomes)
        return {'outcome':outcome, 'details':{'stage':'review','issues': [{'source_quote':'15万元','candidate_quote':'15万元','reason':'请核对金额'}] if outcome=='fact' else []}}
    monkeypatch.setattr(engine, 'review', review)
    identifier = job(source(3))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: bool(s['published'] and s['published']['review_state']=='completed'))['published']
        assert (run['needs_review_units'],run['uncertain_units'],run['unavailable_units'],run['reviewed_units'])==(1,1,1,3)
        paragraphs = client.get(url(identifier)+'/result', params={'run_id':run['id']}).json()['paragraphs']
        assert all(p['text']!=p['original_text'] and p['candidate_text'] is None for p in paragraphs)
        assert [p['review_outcome'] for p in paragraphs]==['fact','uncertain','unavailable']
        assert not store.checkpoint_part(run['id'],0,0,'late mutation','model')
        assert not store.checkpoint_review(run['id'],0,0,{'outcome':'pass','details':{'stage':'review','issues':[]}})


def test_review_outage_publishes_without_separate_retry_action(configured, monkeypatch):
    async def offline(*args):
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(engine, 'review', offline)
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: bool(s['published']))['published']
        assert run['review_state'] == 'completed' and run['unavailable_units'] == 2
        before = store.units(run['id'])
        assert client.post(url(identifier)+'/resume', json={'run_id':run['id'],'phase':'review'}).status_code == 409
        assert store.units(run['id']) == before


def test_regeneration_keeps_old_draft_during_check_and_delete_rejects_late_results(configured, monkeypatch):
    identifier = job(source(1))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        old = wait(client, identifier, lambda s: bool(s['published']))['published']['id']
        async def slow(*args):
            await asyncio.sleep(60)
        monkeypatch.setattr(engine, 'review', slow)
        assert start(client, identifier, True, 'unified-regenerate-key').status_code == 202
        state = wait(client, identifier, lambda s: s['candidate'] and s['candidate']['phase'] == 'review')
        new = state['candidate']['id']
        assert state['published']['id'] == old
        assert client.get(url(identifier)+'/result', params={'run_id':old}).status_code == 200
        assert client.get(url(identifier)+'/result', params={'run_id':new}).status_code == 409
        assert client.delete(f'/api/v1/jobs/{identifier}?purge=true').status_code == 204
        assert not store.checkpoint_review(new,0,0,{'outcome':'pass','details':{'stage':'review','issues':[]}})
        with db.connect() as c:
            assert not c.execute('SELECT 1 FROM optimization_parts WHERE run_id IN (?,?)',(old,new)).fetchall()


def test_generation_bad_output_uses_basic_cleanup_and_continues(configured,monkeypatch):
    async def broken(*args):
        raise engine.InvalidOutput('unusable')
    monkeypatch.setattr(engine,'generate',broken)
    result=source(2)
    result['segments'][0]['text']='那个那个我我说明一下，不是不是真的通过了。'
    identifier=job(result)
    with TestClient(api.create_app()) as client:
        start(client,identifier)
        run=wait(client,identifier,lambda s:bool(s['published']))['published']
        paragraphs=store.units(run['id'])
        assert run['basic_cleanup_units']==2
        assert all(p['generation_method']=='basic_cleanup' for p in paragraphs)
        assert paragraphs[0]['text']!=paragraphs[0]['original_text']
        assert '不是真的通过了' in paragraphs[0]['text']
        assert db.get_job(identifier)['result']==result


def test_plain_generation_strengthens_stutters_once_and_keeps_good_prose(monkeypatch):
    calls=[]
    async def stream(config,payload,activity=None):
        calls.append(payload)
        return '那个那个我我说明。' if len(calls)==1 else '我说明一下。'
    monkeypatch.setattr(engine,'streamed_text',stream)
    result=asyncio.run(engine.generate(PROFILE,[{'ordinal':0,'speaker':'Mike','original_text':'那个那个我我说明。'}],[],'','',None))
    assert result==[(0,'我说明一下。')] and result.method=='model' and len(calls)==2
    assert all('format' not in payload for payload in calls)


def test_plain_generation_exhaustion_is_basic_cleanup_not_original(monkeypatch):
    calls=[]
    async def stream(*args):
        calls.append(1)
        raise engine.InvalidOutput('truncated')
    monkeypatch.setattr(engine,'streamed_text',stream)
    result=asyncio.run(engine.generate(PROFILE,[{'ordinal':0,'speaker':'Mike','original_text':'那个那个我我说明。'}],[],'','',None))
    assert result==[(0,'我说明。')] and result.method=='basic_cleanup' and len(calls)==2


def test_pass_explanation_and_quote_whitespace_are_not_failures():
    assert engine.validate_review({'verdict':'pass','reason':'信息核心一致','source_quote':'任意解释','candidate_quote':'任意解释'},'原文','改写')['outcome']=='pass'
    value=engine.validate_review({'verdict':'fact','reason':'改变金额','source_quote':'预算 １５ 万元','candidate_quote':'预算 50 万元'},'预算15万元。','预算50万元。')
    assert value['details']['issues'][0]['source_quote']=='预算15万元'
    with pytest.raises(engine.InvalidOutput):
        engine.validate_review({'verdict':'fact','reason':'改变金额','source_quote':'预算20万元','candidate_quote':'预算50万元'},'预算15万元。','预算50万元。')


def test_review_fact_requires_confirmation_and_never_receives_editing_goal(monkeypatch):
    calls=[]
    async def structured(config,payload,*args):
        calls.append(payload)
        return {'verdict':'fact','source_quote':'15万元','candidate_quote':'50万元','reason':'改变金额'} if len(calls)==1 else {'verdict':'pass','reason':'没有错误'}
    monkeypatch.setattr(engine,'structured',structured)
    result=asyncio.run(engine.review(PROFILE,'预算15万元。','预算50万元。',[]))
    assert result['outcome']=='uncertain' and len(calls)==2
    assert all('editing_goal' not in json.loads(p['messages'][1]['content']) for p in calls)


def test_parts_and_forced_seams_resume_without_regenerating_or_duplication(configured,monkeypatch):
    data=source(1);data['segments'][0]['text']='甲'*360
    generated,seams=[],[]
    async def generate(config,batch,*args):
        start=batch[0]['start_offset'];generated.append(start)
        return [(0,f'片段{start}')]
    async def seam(config,left,right,*args):
        seams.append(left['part_ordinal'])
        if len(seams)==2:
            await asyncio.sleep(60)
        return left['text']+'|'+right['text'],''
    monkeypatch.setattr(engine,'generate',generate);monkeypatch.setattr(engine,'repair_seam',seam)
    identifier=job(data)
    with TestClient(api.create_app()) as client:
        start(client,identifier)
        run=wait(client,identifier,lambda s:bool(s['candidate'] and len(seams)==2))['candidate']['id']
        assert client.post(url(identifier)+'/pause',json={'run_id':run}).status_code==200
    with TestClient(api.create_app()) as client:
        assert client.post(url(identifier)+'/resume',json={'run_id':run}).status_code==200
        wait(client,identifier,lambda s:bool(s['published']))
        units=store.units(run);chunks=store.parts(run,0)
        assert len(generated)==len(chunks)==3
        assert seams==[0,1,1]
        assert all(units[0]['text'].count(f'片段{part["source"]["start_offset"]}')==1 for part in chunks)
        assert ''.join(part['source']['original_text'] for part in chunks)==data['segments'][0]['text']
        assert store.get(run)['completed_chars']==360


def test_budget_adapts_parts_and_preserves_every_source_offset():
    data=source(1);data['segments'][0]['text']='第一句描述。'*500
    group=engine.prepare_units(data)[0]
    parts=engine.prepare_parts({**PROFILE,'context_length':16384},group,['Atlas'])
    assert len(parts)>1 and all(len(part['original_text'])<=1200 for part in parts)
    assert ''.join(part['original_text'] for part in parts)==group['original_text']
    assert [(p['start_offset'],p['end_offset']) for p in parts]==[(0,parts[0]['end_offset'])]+[(a['end_offset'],b['end_offset']) for a,b in zip(parts,parts[1:])]
    for part in parts:
        for span in part['sources']:
            assert data['segments'][0]['text'][span['start_offset']:span['end_offset']]==span['text']


def test_v17_migration_clears_legacy_runs_and_keeps_parent(configured):
    identifier=job(source(1))
    config={**PROFILE,'prompt_version':5}
    run,_=store.create(identifier,'legacy-v17-migration',False,config,engine.source_hash(source(1)),[],engine.prepare_units(source(1)))
    store.transition(run,'running',('queued',));store.checkpoint(run,[(0,'旧版成稿')]);store.publish(run)
    with db.connect() as c:
        old=tuple(c.execute('SELECT source_json,optimized_text FROM optimization_units WHERE run_id=?',(run,)).fetchone())
        c.execute('DROP TABLE optimization_parts')
        for field in ('generation_method','review_outcome'):
            c.execute('ALTER TABLE optimization_units DROP COLUMN '+field)
        for field in ('review_state','reviewed_units','uncertain_units','unavailable_units','basic_cleanup_units','review_error'):
            c.execute('ALTER TABLE transcript_optimizations DROP COLUMN '+field)
        c.execute('UPDATE schema_meta SET version=17')
    db.init_db();db.init_db()
    assert store.status(identifier) == {'published': None, 'candidate': None}
    assert store.units(run) == []
    assert db.get_job(identifier)['result'] == source(1)
    with db.connect() as c:
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0] == 19
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_adjacent_seams_use_disjoint_short_middle_ranges(monkeypatch):
    junctions = []
    async def stream(config, payload, activity=None):
        junction = json.loads(payload['messages'][1]['content'])['junction_draft']
        junctions.append(junction)
        return junction.replace('\n', '')
    monkeypatch.setattr(engine, 'streamed_text', stream)
    parts = [{'text': text, 'source': {'original_text': text, 'forced_after': index < 2}}
             for index, text in enumerate(('甲' * 200, '中间', '乙' * 200))]
    async def run():
        parts[0]['text'], parts[1]['text'] = await engine.repair_seam(PROFILE, parts[0], parts[1], [])
        parts[1]['text'], parts[2]['text'] = await engine.repair_seam(PROFILE, parts[1], parts[2], [])
    asyncio.run(run())
    assert junctions[0].endswith('中') and junctions[1].startswith('间\n')
    assert ''.join(part['text'] for part in parts) == '甲' * 200 + '中间' + '乙' * 200


def test_forced_review_windows_adapt_before_persisting_parts():
    data = source(1)
    data['segments'][0]['text'] = '甲' * 5000
    unit = engine.prepare_units(data)[0]
    for context in (16384, 32768):
        config = {**PROFILE, 'context_length': context}
        parts = engine.prepare_parts(config, unit, ['Atlas'])
        assert all(engine.seam_review_fits(config, len(part['original_text']), ['Atlas']) for part in parts)
        assert ''.join(part['original_text'] for part in parts) == unit['original_text']


def test_group_overlap_uses_maximum_turn_end_and_scene_links():
    data = source(3)
    data['segments'] = [dict(data['segments'][0], id=1, start=0, end=100),
                        dict(data['segments'][0], id=2, start=10, end=11),
                        dict(data['segments'][0], id=3, start=30, end=32)]
    data['video'] = {'scenes': [{'id': str(i), 'start': i, 'end': i + 1} for i in range(1000)]}
    units = engine.prepare_units(data)
    assert [u['scene_ids'] for u in units] == [['0'], ['10'], ['30']]
    assert [u['sources'][0]['segment_id'] for u in units] == [1, 2, 3]
    data['segments'][0]['end'] = float('nan')
    with pytest.raises(ValueError):
        engine.prepare_units(data)


def test_retry_unavailable_review_preserves_other_verdicts_and_text(configured, monkeypatch):
    outcomes = iter(['pass', 'unavailable', 'uncertain', 'fact'])
    async def review(*args):
        return {'outcome': next(outcomes), 'details': {'stage': 'review', 'issues': []}}
    monkeypatch.setattr(engine, 'review', review)
    identifier = job(source(4))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: bool(s['published'] and s['published']['review_state'] == 'completed'))['published']['id']
        before = store.units(run)
        calls = []
        async def retry(config, original, candidate, *args):
            calls.append(original)
            return {'outcome': 'pass', 'details': {'stage': 'review', 'issues': []}}
        monkeypatch.setattr(engine, 'review', retry)
        response = client.post(url(identifier) + '/resume', json={'run_id': run, 'phase': 'review'})
        assert response.status_code == 409
        assert calls == [] and store.units(run) == before


def test_restart_restores_review_and_disable_pauses_it(configured, monkeypatch):
    import time
    async def slow(*args):
        await asyncio.sleep(60)
    monkeypatch.setattr(engine, 'review', slow)
    identifier = job(source(1))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: bool(s['candidate'] and s['candidate']['phase'] == 'review'))['candidate']['id']
        before = store.units(run)[0]['text']
        settings = {key: PROFILE[key] for key in ('base_url', 'model', 'use_proxy')}
        assert client.put('/api/v1/settings/transcript-optimization', json={**settings, 'enabled': False}).status_code == 200
        assert store.get(run)['state'] == 'paused'
        assert store.units(run)[0]['text'] == before
    # Simulate interrupted running review, not a user pause.
    store.save_settings(PROFILE)
    store.transition(run, 'running', ('paused',))
    async def completed(*args):
        return {'outcome': 'pass', 'details': {'stage': 'review', 'issues': []}}
    monkeypatch.setattr(engine, 'review', completed)
    with TestClient(api.create_app()) as client:
        assert store.get(run)['state'] == 'paused'
        assert client.post(url(identifier)+'/resume', json={'run_id':run}).status_code == 200
        wait(client, identifier, lambda s: bool(s['published'] and s['published']['review_state'] == 'completed'))
        assert store.units(run)[0]['text'] == before


def test_legacy_scene_ownership_retains_maximum_overlap(configured):
    data = source(1)
    data['segments'][0]['end'] = 20
    data['video'] = {'scenes': [{'id': 'first', 'start': 0, 'end': 1}, {'id': 'longer', 'start': 1, 'end': 20}]}
    identifier = job(data)
    run, _ = store.create(identifier, 'legacy-scene-ownership', False, {**PROFILE, 'prompt_version': 5}, engine.source_hash(data), [], engine.prepare_units(data))
    store.transition(run, 'running', ('queued',))
    store.checkpoint(run, [(0, '历史正文')])
    with db.connect() as c:
        c.execute("UPDATE transcript_optimizations SET review_state='completed' WHERE id=?", (run,))
    assert store.publish(run)
    result = engine.reading_result(data, run)
    assert result['scenes'][0]['paragraphs'] == []
    assert result['scenes'][1]['paragraphs'][0]['text'] == '历史正文'


def test_mixed_fact_and_unavailable_are_immutable_after_publication(configured):
    data = source(1)
    data['segments'][0]['text'] = '甲' * 360
    identifier = job(data)
    unit = engine.prepare_units(data)[0]
    run, _ = store.create(identifier, 'mixed-review-retry', False, PROFILE, engine.source_hash(data), [], [unit])
    store.transition(run, 'running', ('queued',))
    chunks = store.parts(run, 0)
    for i in range(len(chunks)):
        store.checkpoint_part(run, 0, i, f'正文{i}', 'model')
    for i in range(len(chunks) - 1):
        if chunks[i]['source']['forced_after']:
            store.checkpoint_seam(run, 0, i, (f'正文{i}', f'正文{i+1}'))
    store.finish_unit(run, 0)
    assert not store.publish(run)
    assert store.begin_review(run)
    fact = {'outcome': 'fact', 'details': {'stage': 'review', 'issues': [{'source_quote': '甲', 'candidate_quote': '正文', 'reason': '证据'}]}}
    store.checkpoint_review(run, 0, 0, fact)
    store.checkpoint_review(run, 0, 1, {'outcome': 'unavailable', 'details': {'stage': 'review', 'issues': []}})
    for i in range(2, len(chunks)):
        store.checkpoint_review(run, 0, i, {'outcome': 'pass', 'details': {'stage': 'review', 'issues': []}})
    assert store.publish(run)
    summary = store.status(identifier)['published']
    before = store.units(run)[0]['text']
    assert summary['needs_review_units'] == summary['unavailable_units'] == 1
    assert store.units(run)[0]['review_outcome'] == 'fact'
    assert not store.checkpoint_review(run, 0, 1, {'outcome': 'pass', 'details': {'stage': 'review', 'issues': []}})
    assert store.units(run)[0]['text'] == before
    assert store.units(run)[0]['review_details'] == fact['details']
    assert store.status(identifier)['published']['unavailable_units'] == 1


def test_qualifier_guard_accepts_synonyms_and_self_correction():
    assert engine.missing_qualifiers('不止蓝沙，未必成功，并非终稿，只能内测。', '不仅蓝沙，不一定成功，不是终稿，仅限内测。') == []
    assert engine.missing_qualifiers('在东楼，不不，是西楼。周二三点。', '周二三点在西楼。') == []
    assert engine.missing_qualifiers('不是模型慢，而是网络抖动。', '问题在网络抖动。') == []
    assert engine.missing_qualifiers('不止一个平台。', '只有一个平台。') == ['不止']


def test_lost_qualifier_reuses_one_strengthening_then_cleans_without_blocking(monkeypatch):
    calls = []
    async def stream(config, payload, activity=None):
        calls.append(deepcopy(payload))
        return '只有蓝沙平台。'
    monkeypatch.setattr(engine, 'streamed_text', stream)
    batch = [{'ordinal': 0, 'speaker': 'Mike', 'original_text': '那个那个不止不止蓝沙平台。'}]
    output = asyncio.run(engine.generate(PROFILE, batch, [], '', '', None))
    assert output.method == 'basic_cleanup' and output == [(0, '不止蓝沙平台。')]
    assert len(calls) == 2 and '不止' in calls[1]['messages'][-1]['content']
    calls.clear()
    async def repaired(config, payload, activity=None):
        calls.append(deepcopy(payload))
        return '只有蓝沙平台。' if len(calls) == 1 else '不仅是蓝沙平台。'
    monkeypatch.setattr(engine, 'streamed_text', repaired)
    output = asyncio.run(engine.generate(PROFILE, batch, [], '', '', None))
    assert output.method == 'model' and output == [(0, '不仅是蓝沙平台。')] and len(calls) == 2


def test_meaningless_truncated_tail_is_not_mechanically_reattached(monkeypatch):
    async def stream(*args):
        return '他们已经出发了。'
    monkeypatch.setattr(engine, 'streamed_text', stream)
    output = asyncio.run(engine.generate(PROFILE, [{'ordinal': 0, 'original_text': '他们已经出发了。总'}], [], '', '', None))
    assert output == [(0, '他们已经出发了。')]


def test_generation_keeps_short_targets_isolated_from_neighbor_speech():
    unit = {'ordinal': 0, 'speaker': 'Mike', 'original_text': '你原来在', 'sources': [{'text': '你原来在'}]}
    payload = engine.payload_for(PROFILE, [unit], ['GPU'], [{'text': '此前负责海南业务。'}], [{'text': '另一位说他在北京。'}])
    content = json.loads(payload['messages'][1]['content'])
    assert content['context_before'] == content['context_after'] == ''
    assert content['source_segments'] == ['你原来在'] and content['target']['text'] == '你原来在'
    assert list(content)[-1] == 'target'
    assert '海南' not in payload['messages'][1]['content'] and '北京' not in payload['messages'][1]['content']
    original = '完整的来源句子。' * 60
    content = json.loads(engine.payload_for(PROFILE, [{'original_text': original}], [], '前' * 900, '后' * 900)['messages'][1]['content'])
    assert len(content['context_before'][0]['text']) == len(content['context_after'][0]['text']) == 200
    assert content['context_before'][0]['truncated'] and content['context_after'][0]['truncated']
    assert content['target']['text'] == original


def test_short_context_expansion_is_bounded_and_does_not_block(monkeypatch):
    calls = []
    async def stream(*args):
        calls.append(1)
        return '我此前在某个公司工作，后来负责了两个项目，现在所有工作都已经完成。'
    monkeypatch.setattr(engine, 'streamed_text', stream)
    output = asyncio.run(engine.generate(PROFILE, [{'ordinal': 0, 'original_text': '州。对。'}], [], '', '', None))
    assert output == [(0, '州。对。')] and output.method == 'basic_cleanup' and len(calls) == 2
    with pytest.raises(engine.InvalidOutput):
        engine.clean_output('临时地点和项目的完整说明。' * 3, '你原来在')


def test_clear_short_reply_still_uses_model_without_fallback_count(monkeypatch):
    calls = []
    async def stream(*args):
        calls.append(1)
        return '嗯。'
    monkeypatch.setattr(engine, 'streamed_text', stream)
    output = asyncio.run(engine.generate(PROFILE, [{'ordinal': 0, 'original_text': '嗯。'}], [], '长上下文', '更多上下文', None))
    assert output == [(0, '嗯。')] and output.method == 'model' and len(calls) == 1


def test_context_budget_preserves_neighbor_speakers_and_truncation():
    unit = {'original_text': '甲' * 80, 'speaker': 'current'}
    before = [{'speaker': 'a', 'text': '前' * 30, 'truncated': False}, {'speaker': 'b', 'text': '后' * 30, 'truncated': False}]
    content = json.loads(engine.payload_for(PROFILE, [unit], [], before, '同一发言的下文')['messages'][1]['content'])
    assert content['context_before'] == [{'speaker': 'a', 'text': '前' * 10, 'truncated': True}, {'speaker': 'b', 'text': '后' * 30, 'truncated': False}]
    assert content['context_after'] == [{'speaker': 'current', 'text': '同一发言的下文', 'truncated': False}]


def test_natural_batches_keep_decimals_model_names_and_full_source_coverage():
    data = source(1)
    data['segments'][0]['text'] = ('我们运行 qwen3.5:4b 与 v0.1.2，单价为 15.5 元。' * 15)
    group = engine.prepare_units(data)[0]
    parts = engine.prepare_parts(PROFILE, group, [])
    assert len(parts) > 1 and all(len(part['original_text']) <= 160 for part in parts)
    assert ''.join(part['original_text'] for part in parts) == group['original_text']
    for part in parts:
        assert part['original_text'].count('qwen3.5:4b') == part['original_text'].count('v0.1.2') == part['original_text'].count('15.5')
        assert not part['forced_after']
        for span in part['sources']:
            assert data['segments'][0]['text'][span['start_offset']:span['end_offset']] == span['text']


def test_generation_never_copies_neighbor_batch_context_into_request(monkeypatch):
    requests = []
    async def stream(config, payload, activity=None):
        requests.append(json.loads(payload['messages'][1]['content']))
        return '本段介绍项目安排。'
    monkeypatch.setattr(engine, 'streamed_text', stream)
    original = '本段介绍项目安排。' * 10
    asyncio.run(engine.generate(PROFILE, [{'ordinal': 0, 'original_text': original}], [], [{'speaker': 'other', 'text': '隔壁在东京工作。'}], '旁边说预算五千。', None))
    assert requests[0]['context_before'] == [] and requests[0]['context_after'] == []
    assert '东京' not in json.dumps(requests[0], ensure_ascii=False) and '五千' not in json.dumps(requests[0], ensure_ascii=False)

@pytest.mark.parametrize('failure', [TimeoutError('timeout'), httpx.ConnectError('offline')])
def test_optional_strengthening_transport_failure_keeps_usable_draft(monkeypatch, failure):
    calls = []
    async def stream(*args):
        calls.append(1)
        if len(calls) == 2:
            raise failure
        return '我我说明。'
    monkeypatch.setattr(engine, 'streamed_text', stream)
    result = asyncio.run(engine.generate(PROFILE, [{'ordinal': 0, 'original_text': '那个那个我我说明。'}], [], '', '', None))
    assert result == [(0, '我我说明。')] and result.method == 'model'
    assert len(calls) == 2


def test_initial_transport_failure_remains_recoverable(monkeypatch):
    async def stream(*args):
        raise TimeoutError('offline')
    monkeypatch.setattr(engine, 'streamed_text', stream)
    with pytest.raises(TimeoutError):
        asyncio.run(engine.generate(PROFILE, [{'ordinal': 0, 'original_text': '说明。'}], [], '', '', None))
