"""Regression fixtures for observed small-model failure modes, without user text."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import io
import json
import threading
import time

from docx import Document
from fastapi.testclient import TestClient
import httpx
from pypdf import PdfReader
import pytest

from audio_intel import api, db, optimization_api as routes, optimization_engine as engine
from audio_intel import optimization_store as store, optimization_summaries as insights
from audio_intel import optimization_summary_policy as policy, transcript_docx, transcript_pdf
from audio_intel.transcript_export import TranscriptExportRequest, optimized_export_result
from test_optimization import configured, local, job, source, start, url, wait, PROFILE


@pytest.fixture
def v2(configured, monkeypatch):
    monkeypatch.setattr(insights, 'VERSION', 2)
    return configured


def evaluate(monkeypatch, source_text, kind, responses, circuit=None):
    calls, trace = [], []
    async def structured(profile, payload):
        calls.append(deepcopy(payload))
        value = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(value, Exception):
            raise value
        return value
    async def record(*args):
        trace.append(args)
    monkeypatch.setattr(engine, 'structured', structured)
    result = asyncio.run(policy.generate(PROFILE, source_text, kind, circuit or policy.Circuit(), record))
    return result, calls, trace


@pytest.mark.parametrize('text', ['目录：产品关系、部署方法。', '今天介绍上线步骤。', '资料来源：设备维护记录。', '流程说明。', '预算尚未确定。', '3', '不行', '第1章'])
def test_content_denominator_includes_intros_outlines_and_uncertain_text(text):
    assert policy.meaningful(text)


@pytest.mark.parametrize('text', ['', '  …！？', '嗯，好的，谢谢大家。', 'Hello, thank you!', '再见。'])
def test_only_genuine_no_content_is_skipped(monkeypatch, text):
    result, calls, trace = evaluate(monkeypatch, text, 'group', [AssertionError('should not call')])
    assert not calls and not trace
    assert result.skip_reason == 'no_substantive_content' and result.texts == []


@pytest.mark.parametrize('spoken,normalized', [('二十四', '24'), ('三千', '3000'), ('四千', '4,000'), ('一百五十万', '1500000'), ('一亿五千万', '150000000'), ('三点五', '3.5'), ('300万', '三百万')])
def test_numeric_notation_is_equivalent(spoken, normalized):
    assert policy.numbers(spoken) == policy.numbers(normalized)
    assert policy.candidate_error('总额' + spoken + '元。', '总额' + normalized + '元。', 'group') is None


@pytest.mark.parametrize('original,candidate', [
    ('新订单达到三千元需要复核。', '超三千元需复核。'),
    ('新增三千元与四千元单据。', '新增千元单据。'),
    ('第一次校验失败，修复后第二次校验通过。', '两次校验均通过。'),
    ('不是五百万元，实际为三百万元。', '预算从五百万元改到三百万元。'),
    ('刚才说五百万元不对，实际三百万元。', '预算从五百万元改到三百万元。'),
])
def test_demonstrated_numeric_or_scope_errors_are_rejected(original, candidate):
    assert policy.candidate_error(original, candidate, 'group')


def test_explicitly_retracted_number_stays_valid_without_unnecessary_repair():
    assert policy.candidate_error('五百万元不对，实际三百万元。', '五百万不对，实际三百万。', 'group') is None


def test_targeted_repair_receives_actual_candidate_and_error(monkeypatch):
    original = '第一次校验失败，修复后第二次校验通过。'
    first = {'texts': ['两次校验均通过。']}
    result, calls, trace = evaluate(monkeypatch, original, 'group', [first, {'texts': ['修复后第二次校验通过。']}])
    assert result.method == 'model_repair' and len(calls) == 2
    assert json.loads(calls[1]['messages'][-2]['content']) == first
    assert 'scope' in calls[1]['messages'][-1]['content']
    assert [t[1] for t in trace] == ['rejected', 'accepted']


def test_optional_second_summary_does_not_spend_retry(monkeypatch):
    result, calls, trace = evaluate(monkeypatch, '预算三千元，达到门槛需复核。', 'group', [{'texts': ['预算三千元，达到门槛需复核。', '编造9000元。']}])
    assert len(calls) == 1 and len(result.texts) == 1 and result.method == 'model'
    assert trace[0][3]  # Optional-point rejection stays diagnosable.


def test_keyword_selection_preserves_whole_terms_and_numbers(monkeypatch):
    original = '三千元与四千元单据需要财务复核，恢复代码版本时审批记录不变。'
    result, calls, trace = evaluate(monkeypatch, original, 'scene', [
        {'texts': ['标题' * 30]}, {'texts': ['标题' * 30]},
        {'keywords': ['千元单据', '三千元与四千元单据', '财务复核', '恢复代码版本时审批记录不变', '财务复核', '凭空捏造']},
    ])
    assert len(calls) == 3 and result.method == 'model_keywords'
    assert result.texts == ['三千元与四千元单据 · 财务复核']
    assert insights.fits_length(result.texts[0], 'scene')
    assert all(term in original for term in result.texts[0].split(' · '))


def test_keywords_do_not_cut_off_quantity_units():
    assert policy.select_keywords('三千元单据、二十四项检查', ['千元', '三千', '二十四', '三千元', '二十四项检查'], 'scene') == ['三千元 · 二十四项检查']


def test_malformed_response_breaks_consecutive_connection_failure_count(monkeypatch):
    circuit = policy.Circuit(failures=2)
    result, calls, trace = evaluate(monkeypatch, '周五执行维护。', 'scene', [engine.InvalidOutput('bad JSON')], circuit)
    assert not circuit.reason and circuit.failures == 0
    assert result.method == 'source_excerpt' and len(calls) == 3


@pytest.mark.parametrize('kind', ['group', 'scene'])
def test_invalid_keywords_fall_back_to_substantive_source(monkeypatch, kind):
    original = '新订单达到三千元必须财务复核，否则不能付款。'
    result, calls, trace = evaluate(monkeypatch, original, kind, [engine.InvalidOutput('bad JSON'), {}, {'keywords': ['不存在的东西']}])
    assert len(calls) == 3 and result.method == 'source_excerpt'
    assert result.texts[0] in original and insights.fits_length(result.texts[0], kind)
    assert [t[0] for t in trace] == ['generate', 'repair', 'keywords', 'source']


def test_transport_retries_share_budget_and_open_circuit(monkeypatch):
    circuit = policy.Circuit()
    result, calls, _ = evaluate(monkeypatch, '维护计划周五执行。', 'group', [httpx.ConnectError('down')], circuit)
    assert len(calls) == 3 and circuit.reason and result.method == 'source_excerpt'
    second, calls, trace = evaluate(monkeypatch, '新增单据必须复核。', 'scene', [AssertionError('circuit open')], circuit)
    assert not calls and second.texts[0] in '新增单据必须复核。'
    assert trace[0][0] == 'source'


def test_context_capacity_failure_uses_source_without_switching_model(monkeypatch):
    circuit = policy.Circuit()
    result, calls, _ = evaluate(monkeypatch, '维护计划周五执行。', 'scene', [engine.ContextWindowError('allocated 16K, requested 32K')], circuit)
    assert len(calls) == 1 and circuit.reason and result.method == 'source_excerpt'
    assert calls[0]['model'] == PROFILE['model'] and calls[0]['options']['num_ctx'] == 32768


def test_deadline_does_not_multiply_model_timeouts(monkeypatch):
    monkeypatch.setattr(policy, 'ITEM_SECONDS', .02)
    async def stall(*args):
        await asyncio.sleep(1)
    async def record(*args):
        pass
    monkeypatch.setattr(engine, 'structured', stall)
    before = time.monotonic()
    budget = policy.Budget()
    result = asyncio.run(policy.generate(PROFILE, '周五执行维护。', 'scene', policy.Circuit(), record, budget))
    assert time.monotonic() - before < .3 and budget.calls == 1
    assert result.method == 'source_excerpt'


def test_v2_api_automatically_delivers_both_kinds_and_diagnostics(v2, monkeypatch):
    async def invalid(*args):
        raise engine.InvalidOutput('synthetic malformed response')
    monkeypatch.setattr(engine, 'structured', invalid)
    raw = source(3); raw['video'] = {'scenes': [{'id': 'a', 'start': 0, 'end': 12, 'image': None}]}
    identifier = job(raw)
    original = deepcopy(db.get_job(identifier)['result'])
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        published = wait(client, identifier, lambda s: bool(s['published']))['published']
        run = published['id']
        assert published['summary_status']['version'] == 2
        for kind, total in [('group', 3), ('scene', 1)]:
            count = published['summary_status']['counts'][kind]
            assert count['ready'] == count['total'] == count['fallback_ready'] == count['extractive_ready'] == total
            assert count['failed'] == count['skipped'] == 0
        value = client.get(url(identifier) + '/summaries', params={'run_id': run}).json()
        assert all(g['generation_method'] == 'source_excerpt' and g['attempt_count'] == 3 for g in value['groups'].values())
        assert value['scenes']['a']['quality_notice'] == 'source_excerpt'
        assert 'candidate_json' not in json.dumps(value)
        assert store.get(run)['config']['request_policy_version'] == 2
        assert client.get('/api/v1/capabilities').json()['asr']['transcript_optimization']['summaries']['automatic_fallback']
        assert 'source_excerpt' not in client.get('/api/v1/jobs').text
    assert db.get_job(identifier)['result'] == original
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM optimization_summary_retries').fetchone()[0] == 0
        assert c.execute('SELECT count(*) FROM optimization_summary_attempts').fetchone()[0] == 16
        assert 'synthetic malformed response' in c.execute('SELECT error FROM optimization_summary_attempts LIMIT 1').fetchone()[0]
    store.delete(identifier)
    with db.connect() as c:
        assert not c.execute('SELECT * FROM optimization_summary_attempts').fetchall()
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_v20_additive_migration_preserves_published_v1_and_checkpoints(configured):
    identifier = job(source(1))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: bool(s['published']))['published']['id']
    before = store.units(run)
    value = insights.read(run)
    with db.connect() as c:
        c.execute('DROP TABLE optimization_summary_attempts')
        for name in ('generation_method', 'attempt_count', 'quality_notice', 'skip_reason'):
            c.execute(f'ALTER TABLE optimization_summaries DROP COLUMN {name}')
        c.execute('UPDATE schema_meta SET version=20')
    db.init_db(); db.init_db()
    assert store.units(run) == before and insights.read(run) == value
    with db.connect() as c:
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0] == 21
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_review_v2_single_fact_verdict_and_invalid_grounding_are_not_repolled(monkeypatch):
    calls = []
    async def structured(*args):
        calls.append(1)
        return {'verdict': 'fact', 'source_quote': '原文不连续拼接', 'candidate_quote': '预算五千元。', 'reason': '数字变化'}
    monkeypatch.setattr(engine, 'structured', structured)
    result = asyncio.run(engine.review({**PROFILE, 'request_policy_version': 2}, '预算三千元。', '预算五千元。', []))
    assert len(calls) == 1 and result['outcome'] == 'uncertain'
    assert result['details']['issues'] == [] and 'grounded' in result['details']['diagnostic']
    calls.clear()
    async def grounded(*args):
        calls.append(1)
        return {'verdict': 'fact', 'source_quote': '预算三千元。', 'candidate_quote': '预算五千元。', 'reason': '数字变化'}
    monkeypatch.setattr(engine, 'structured', grounded)
    result = asyncio.run(engine.review({**PROFILE, 'request_policy_version': 2}, '预算三千元。', '预算五千元。', []))
    assert len(calls) == 1 and result['outcome'] == 'fact'


def test_prose_transport_and_content_repair_share_actual_http_budget(monkeypatch):
    requests = []
    def response(request):
        requests.append(json.loads(request.content))
        if len(requests) == 2:
            return httpx.Response(503, text='temporarily unavailable')
        return httpx.Response(200, text=json.dumps({'message': {'content': ''}, 'done': True, 'done_reason': 'stop'}) + '\n')
    monkeypatch.setattr(engine, 'client', lambda config: httpx.AsyncClient(base_url='http://ollama.invalid/', transport=httpx.MockTransport(response)))
    async def context(*args, **kwargs):
        return 32768
    monkeypatch.setattr(engine, 'ensure_context', context)
    async def execute():
        manager = routes.Manager(None)
        return await manager.retry_io(lambda: engine.generate({**PROFILE, 'request_policy_version': 2}, [{'ordinal': 0, 'original_text': '那个我们我们周五确认预算。'}], [], '', '', None), request_limit=3)
    value = asyncio.run(execute())
    assert len(requests) == 3 and value.method == 'basic_cleanup'
    assert value[0][1] == engine.basic_cleanup('那个我们我们周五确认预算。')


def test_prose_repair_keeps_valid_candidate_if_next_request_fails(monkeypatch):
    requests = []
    def response(request):
        requests.append(json.loads(request.content))
        if len(requests) == 2:
            return httpx.Response(503, text='temporarily unavailable')
        return httpx.Response(200, text=json.dumps({'message': {'content': '我们我们周五确认预算。'}, 'done': True, 'done_reason': 'stop'}, ensure_ascii=False) + '\n')
    monkeypatch.setattr(engine, 'client', lambda config: httpx.AsyncClient(base_url='http://ollama.invalid/', transport=httpx.MockTransport(response)))
    async def context(*args, **kwargs):
        return 32768
    monkeypatch.setattr(engine, 'ensure_context', context)
    async def execute():
        manager = routes.Manager(None)
        return await manager.retry_io(lambda: engine.generate({**PROFILE, 'request_policy_version': 2}, [{'ordinal': 0, 'original_text': '我们我们周五确认预算。'}], [], '', '', None), request_limit=3)
    value = asyncio.run(execute())
    assert len(requests) == 2 and value.method == 'model'
    assert value[0][1] == '我们我们周五确认预算。'
    assert requests[1]['messages'][-2] == {'role': 'assistant', 'content': value[0][1]}


def test_long_scene_all_fragments_survive_a_bad_node_and_resume(v2, monkeypatch):
    original = '前半段介绍预算与审批。' * 500 + '尾部新增要求：周五完成隐私脱敏。'
    run, _ = store.create(job(source(1)), 'long-v2-source-key', False, {**PROFILE, 'summary_version': 2}, 'hash', [], engine.prepare_units(source(1)))
    with db.connect() as c:
        c.execute("UPDATE transcript_optimizations SET summary_state='running' WHERE id=?", (run,))
    calls, records = [], []
    async def structured(profile, payload):
        text = payload['messages'][1]['content']; calls.append(text)
        if '尾部新增要求' in text:
            raise engine.InvalidOutput('malformed last fragment')
        return {'texts': ['预算审批安排']}
    async def record(*args):
        records.append(args)
    monkeypatch.setattr(engine, 'structured', structured)
    profile = {**PROFILE, 'context_length': 16384}
    result = asyncio.run(insights.scene_title_v2(run, profile, original, policy.Circuit(), record))
    fragment_sources = [s for s in calls if len(s) > 1000]
    # Repairs repeat one node; every disjoint first fragment reconstructs source.
    unique = list(dict.fromkeys(fragment_sources))
    assert '尾部新增要求' in ''.join(calls)
    assert result.texts and insights.fits_length(result.texts[0], 'scene')
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM optimization_summary_fragments WHERE run_id=?', (run,)).fetchone()[0] >= 2
    first_count = len(calls)
    asyncio.run(insights.scene_title_v2(run, profile, original, policy.Circuit(), record))
    assert len(calls) - first_count == 1  # Final title only; fragment checkpoints reused.


def test_v2_pause_blocks_late_attempt_and_result(v2, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    async def late(*args):
        entered.set()
        try:
            while not release.is_set():
                await asyncio.sleep(.01)
        except asyncio.CancelledError:
            return {'texts': ['预算暂未确认。']}
        return {'texts': ['预算暂未确认。']}
    monkeypatch.setattr(engine, 'structured', late)
    identifier = job(source(1))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        assert entered.wait(3)
        run = client.get(url(identifier)).json()['candidate']['id']
        assert client.post(url(identifier) + '/pause', json={'run_id': run}).status_code == 200
        release.set()
        state = client.get(url(identifier)).json()
        assert state['candidate']['state'] == 'paused' and state['published'] is None
        assert insights.read(run)['groups']['0']['state'] == 'pending'
        with db.connect() as c:
            assert not c.execute('SELECT * FROM optimization_summary_attempts').fetchall()


def test_v2_pdf_word_fallback_labels_and_redaction_match(v2, monkeypatch):
    async def keywords(profile, payload):
        if 'keywords' in payload['format']['properties']:
            return {'keywords': ['阿特拉斯', '预算']}
        raise engine.InvalidOutput('bad summary')
    monkeypatch.setattr(engine, 'structured', keywords)
    raw = source(1); raw['video'] = {'scenes': [{'id': 'a', 'start': 0, 'end': 3, 'image': None}]}
    identifier = job(raw)
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: bool(s['published']))['published']['id']
    request = TranscriptExportRequest(format='docx', text_version='comparison', optimization_id=run, replacements=[{'source': '阿特拉斯', 'target': '项目X'}])
    result = optimized_export_result(db.get_job(identifier), request)
    assert result['segments'][0]['group_summary']['generation_method'] == 'model_keywords'
    assert '阿特拉斯' not in json.dumps(result, ensure_ascii=False)
    assert 'candidate_json' not in json.dumps(result)
    document = Document(io.BytesIO(transcript_docx.render('Meeting', result, v2.data_dir)))
    word = '\n'.join(p.text for p in document.paragraphs)
    pdf = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(transcript_pdf.render('Meeting', result, v2.data_dir))).pages)
    for text in (word, pdf):
        assert '关键词' in text and '项目X' in text
