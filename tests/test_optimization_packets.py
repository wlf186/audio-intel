"""Packet contracts: source coverage, durable calls and partial-item recovery."""
import asyncio
from collections import Counter
import json
import threading
import re
import httpx

from fastapi.testclient import TestClient
import pytest

from audio_intel import api, db
from audio_intel import optimization_budget as calls, optimization_engine as engine
from audio_intel import optimization_packet_policy as policy, optimization_packet_store as packets
from audio_intel import optimization_provider as provider, optimization_store as store
from audio_intel import optimization_summary_policy as summaries
from test_optimization import PROFILE, job, start, url, wait
from test_voiceprint_groups_exports import local


def transcript(count=1, *, length=1, video=False):
    text = '嗯，那个预算四千元，但是还没批准。你明天问一下，好吗？'
    result = {'language': 'Chinese', 'duration': count * 3, 'speakers': [],
              'segments': [{'id': i, 'speaker': str(i % 2), 'start': i * 3,
                            'end': (i + 1) * 3, 'text': text * length}
                           for i in range(count)]}
    if video:
        result['video'] = {'scenes': [{'id': 'scene', 'start': 0, 'end': count * 3}]}
    return result


@pytest.fixture
def packet_model(local, monkeypatch):
    store.save_settings({**PROFILE, 'model': 'qwen3.5:9b', 'packet_presence_penalty': 0})
    requests = []
    async def completion(connection, body, activity=None):
        data = json.loads(body['messages'][-1]['content'])
        requests.append(data)
        if 'response_format' not in body:
            return data['target']['text'].replace('嗯，那个', '')
        schema = body['response_format']['json_schema']['schema']
        def review(item):
            return {'verdict': 'pass', 'source_quote': '', 'candidate_quote': '', 'reason': ''}
        if isinstance(data, dict):  # joint independent review and insights
            return json.dumps({
                'reviews': {item['id']: review(item) for item in data['units']},
                'groups': {item['id']: '预算四千元，尚未批准。' for item in data['groups']},
                'scenes': {item['id']: '预算审批与询问安排' for item in data['scenes']}})
        values = {}
        for item in data:
            field = schema['properties'][item['id']]
            if 'candidate' in item:
                values[item['id']] = {**review(item), 'summary': '预算四千元，尚未批准。'}
            elif field.get('type') == 'object':
                values[item['id']] = {'facts': [], 'text': item['source'].replace('嗯，那个', '')}
            else:
                system = body['messages'][0]['content']
                values[item['id']] = ('预算四千元，尚未批准。' if 'TOP1小结' in system
                                      else '预算审批与询问安排' if '主题标题' in system
                                      else item['source'].replace('嗯，那个', ''))
        return json.dumps(values, ensure_ascii=False)
    monkeypatch.setattr(provider, 'completion', completion)
    # Use strict JSON schemas in the fake provider, as on the measured candidate.
    store.save_settings({**store.settings(), 'structured_output_mode': 'json_schema'})
    return requests, completion


def test_small_video_finishes_all_artifacts_in_two_real_calls(packet_model):
    requests, _ = packet_model
    identifier = job(transcript(2, video=True))
    with TestClient(api.create_app()) as client:
        estimate = client.post(url(identifier) + '/estimate', json={}).json()
        assert estimate['estimated_calls'] == 2
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']
        assert run['calls_used'] == 2 and run['review_state'] == 'completed'
        result = client.get(url(identifier) + '/result', params={'run_id': run['id']}).json()
        assert all(p['review_outcome'] == 'pass' for p in result['paragraphs'])
        assert all(p['group_summary']['state'] == 'ready' for p in result['paragraphs'])
        assert result['scenes'][0]['title_summary']['title']
        assert calls.usage(run['id'])['stages'] == {'generation': 1, 'joint': 1}
        assert len(requests) == 2 and 'candidate' not in requests[0][0]
        assert requests[1]['units'][0]['candidate']  # Separate call with generated prose.
    with db.connect() as connection:
        assert all(row[0] for row in connection.execute('SELECT node_key FROM optimization_calls'))


def test_short_multi_scene_video_still_needs_only_two_calls(packet_model):
    requests, _ = packet_model
    data = transcript(9)
    data['video'] = {'scenes': [{'id': 'scene' + str(i), 'start': i * 3, 'end': (i + 1) * 3}
                               for i in range(9)]}
    identifier = job(data)
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']['id']
        assert calls.usage(run)['calls_used'] == len(requests) == 2
        values = client.get(url(identifier) + '/summaries', params={'run_id': run}).json()
        assert len(values['scenes']) == 9
        assert all(value['state'] == 'ready' for value in values['scenes'].values())


def test_fixed_short_plan_recovers_without_extra_plan_rows_or_noop_writes(packet_model):
    requests, _ = packet_model
    data = transcript()
    data['segments'][0]['text'] = '预算四千元，但还没批准。你明天问一下，好吗？'
    identifier = job(data)
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']['id']
        assert calls.usage(run)['stages'] == {'generation': 1, 'group': 1}
        assert len(requests) == 2 and 'target' in requests[0]
        plan = packets.nodes(run)
        assert [node['key'] for node in plan] == ['generation:0', 'joint:1']
        with db.connect() as connection:
            assert not connection.execute('SELECT 1 FROM optimization_packet_nodes WHERE run_id=?', (run,)).fetchone()
        before = store.get(run)
        packets.finalization(run, [], [])
        assert store.get(run) == before
        db.init_db()
        assert packets.nodes(run) == plan


def test_semantic_merge_retains_every_original_group_and_never_crosses_owner():
    result = transcript(6, length=7, video=True)
    for item in result['segments'][:3]:
        item['speaker'] = 'same'
    original = engine.prepare_units(result)
    config = {**PROFILE, 'rewrite_mode': 'rewrite', 'summary_scene_ids': ['scene']}
    merged = policy.prepare(config, result, [])
    old_spans = Counter((span['segment_id'], span['start_offset'], span['end_offset'], span['text'])
                        for unit in original for span in unit['sources'])
    expanded = [detail for unit in merged for detail in unit.get('_detail_sources', [unit])]
    new_spans = Counter((span['segment_id'], span['start_offset'], span['end_offset'], span['text'])
                        for unit in expanded for span in unit['sources'])
    assert old_spans == new_spans
    assert [detail['original_text'] for detail in expanded] == [unit['original_text'] for unit in original]
    for unit in merged:
        assert len({result['segments'][span['segment_id']]['speaker'] for span in unit['sources']}) == 1


def test_dense_long_prose_gets_readable_paragraphs_without_semantic_edits():
    source = ('版本 1.7 仍在测试，预算尚未批准。目标如下：一、保留问题；二、保留条件；三、保留技术词。'
              'The API is not ready yet. Can we wait? ' * 16).strip()
    formatted = policy.format_paragraphs(source)
    assert '\n\n' in formatted
    assert ''.join(formatted.split()) == ''.join(source.split())
    assert '1.7' in formatted and 'not ready yet.' in formatted
    assert policy.format_paragraphs('Short source.') == 'Short source.'
    assert policy.format_paragraphs(source + '\nExisting layout.') == source + '\nExisting layout.'
    english = ('word ' * 48 + 'Dr. Smith says the API is still unavailable. ') * 4
    assert 'Dr.\n' not in policy.format_paragraphs(english)
    review = policy.review_result(None, source, policy.format_paragraphs(engine.basic_cleanup(source)))
    assert review['outcome'] == 'pass'
    assert 'Literal-only cleanup' in review['details']['diagnostic']
    assert policy.review_result(None, source, engine.basic_cleanup(source))['outcome'] == 'pass'


def test_literal_cleanup_preserves_conditions_questions_quotes_and_technical_words():
    source = ('呃，我们需要需要提前提前准备。嗯，对对对。预算不是三千，是四千，但还没有批准。'
              '是不是要再等一下？先不要发布 API 1.7。医生讨论呃逆。'
              '他说“呃，提前提前准备”，这句直接引语需要保留。' * 3)
    cleaned = policy.literal_cleanup(source)
    assert '我们需要提前准备。对。' in cleaned
    assert '预算不是三千，是四千，但还没有批准。' in cleaned
    assert cleaned.count('是不是要再等一下？') == source.count('是不是要再等一下？')
    assert '先不要发布 API 1.7。医生讨论呃逆。' in cleaned
    assert '“呃，提前提前准备”' in cleaned
    assert policy.literal_cleanup('嗯，对。预算未批。') == engine.basic_cleanup('嗯，对。预算未批。')
    reviewed = policy.review_result(None, source, policy.format_paragraphs(cleaned))
    assert reviewed['outcome'] == 'pass'
    assert 'Literal-only cleanup' in reviewed['details']['diagnostic']


def test_medium_dense_prose_is_split_without_changing_facts():
    source = '预算尚未批准。' + ('这里保留业务判断、责任归属和原有问题，不增加新结论。' * 16)
    assert 360 < len(source) < 800
    formatted = policy.format_paragraphs(source)
    assert '\n\n' in formatted
    assert ''.join(formatted.split()) == ''.join(source.split())


def test_packet_cleanup_removes_delimited_hesitations_without_touching_quoted_speech():
    source = ('这这个方案啊，嗯，我们需要需要提前准备。这里这里的 API 1.7 还没批；'
              '你呢？预算不是三千，是四千。只能先试两天，仍然可能不行。'
              '他说 "我我觉得，嗯，不能发布" 然后问“你你批准了吗？”。' * 3)
    cleaned = policy.literal_cleanup(source)
    assert '这个方案，我们需要提前准备。这里的 API 1.7 还没批；' in cleaned
    assert '你呢？预算不是三千，是四千。只能先试两天，仍然可能不行。' in cleaned
    assert '他说 "我我觉得，嗯，不能发布" 然后问“你你批准了吗？”。' in cleaned
    assert cleaned.count('？') == source.count('？')
    acknowledgement = '嗯。啊！' + '预算还没有批准。' * 20
    assert policy.literal_cleanup(acknowledgement).startswith('嗯。啊！')
    lexical = '额定功率是10瓦。他说“确认参数”额定功率仍未批准；医生说“继续观察”呃逆尚未好转。' * 4
    assert policy.literal_cleanup(lexical) == lexical


def test_planned_editing_example_has_grounded_quotes_and_complete_edited_text():
    source = json.loads(policy.PLANNED_EXAMPLE[0]['content'])
    output = json.loads(policy.PLANNED_EXAMPLE[1]['content'])
    for item in source:
        result = output[item['id']]
        assert all(fact['quote'] in item['source'] for fact in result['facts'])
        assert result['text'] == json.loads(policy.EXAMPLE[1]['content'])[item['id']]


def test_dense_dialogue_keeps_fine_editing_boundaries_and_fluent_prose_can_merge():
    def transcript(text):
        return {'segments': [{'id': 's1', 'start': 0, 'end': 180,
                              'speaker': 'a', 'text': text}]}
    noisy = transcript(('嗯，那个预算还没批准。然后是谁去问？是不是只能先试？' * 30))
    original = engine.prepare_units(noisy, source_assignment_version=2)
    units = policy.semantic_units(noisy, source_assignment_version=2)
    assert len(units) > 1
    assert all(len(unit['original_text']) <= 400 for unit in units)
    fine = [source for unit in units for source in unit.get('_detail_sources', [unit])]
    assert [source['sources'] for source in fine] == [source['sources'] for source in original]
    assert sum(unit['original_text'].count('？') for unit in units) == noisy['segments'][0]['text'].count('？')
    fluent = transcript('预算尚未批准，负责人将在会议后继续核实。' * 35)
    assert any(len(unit['original_text']) > 400
               for unit in policy.semantic_units(fluent, source_assignment_version=2))


def test_summary_cannot_be_replaced_with_an_editing_review_or_dismissal():
    source = '小林来了吗？明天的产品交流和演示是谁去？主持人能听到吗？'
    assert policy.insight_error(source, '原文为不确定询问，候选改为肯定陈述。', 'group')
    assert policy.insight_error(source, '用户提供的内容为无意义的语音转录片段。', 'group')
    assert policy.insight_error(source, '询问小林是否加入及产品交流演示安排。', 'group') is None
    assert policy.insight_error('原文与候选版本的差异需要复核。', '原文与候选差异需复核。', 'group') is None


def test_ungrounded_fact_evidence_is_uncertain_without_repolling():
    result = policy.review_result({'verdict': 'fact', 'source_quote': 'not in source',
                                   'candidate_quote': '', 'reason': 'invented'},
                                  'Only a proposal, not approved.', 'The proposal is approved.')
    assert result['outcome'] == 'uncertain'
    assert result['details']['issues'] == []
    assert 'grounded' in result['details']['diagnostic']


def test_partial_packet_commits_before_pause_and_resume_filters_valid_ids(packet_model, monkeypatch):
    requests, normal = packet_model
    entered = threading.Event()
    accepted_ids, generation_requests = set(), []
    async def partial(connection, body, activity=None):
        data = json.loads(body['messages'][-1]['content'])
        # The initial response deliberately omits exactly one item.
        if isinstance(data, list) and 'candidate' not in data[0] and '档位要求' in body['messages'][0]['content']:
            generation_requests.append([item['id'] for item in data])
            if len(generation_requests) == 1:
                value = json.loads(await normal(connection, body, activity))
                value.pop(data[-1]['id'])
                accepted_ids.update(value)
                return json.dumps(value)
        elif body['messages'][-1]['role'] == 'user' and body['messages'][-1]['content'].startswith('只修复'):
            entered.set()
            await asyncio.sleep(60)
        return await normal(connection, body, activity)
    # Handle the repair instruction before decoding its non-JSON content.
    async def transport(connection, body, activity=None):
        if body['messages'][-1]['content'].startswith('只修复'):
            entered.set()
            await asyncio.sleep(60)
        return await partial(connection, body, activity)
    monkeypatch.setattr(provider, 'completion', transport)
    identifier = job(transcript(7, length=7))
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        assert entered.wait(4)
        run = store.status(identifier)['candidate']['id']
        assert accepted_ids
        assert all(store.units(run)[int(key)]['text'] is not None for key in accepted_ids)
        plan = packets.nodes(run)
        assert client.post(url(identifier) + '/pause', json={'run_id': run}).status_code == 200
        used = calls.usage(run)['calls_used']
        before_resume = len(requests)
        monkeypatch.setattr(provider, 'completion', normal)
        assert client.post(url(identifier) + '/resume', json={'run_id': run}).status_code == 200
        done = wait(client, identifier, lambda value: bool(value['published']))['published']
        assert done['id'] == run and done['calls_used'] > used
        assert packets.nodes(run) == plan
        assert calls.usage(run)['failed_or_interrupted'] == 1
    resumed = [data for data in requests[before_resume:] if isinstance(data, list)
               and data and 'candidate' not in data[0]]
    assert resumed
    assert all(not accepted_ids.intersection(item['id'] for item in data) for data in resumed)


def test_v24_migration_preserves_sources_calls_and_legacy_plan_absence(local):
    result = transcript()
    identifier = job(result)
    config = {**PROFILE, 'budget_policy_version': 1, 'max_llm_calls': 7, 'call_estimate': {}}
    run, _ = store.create(identifier, 'legacy-packet-upgrade', False, config, engine.source_hash(result),
                          [], engine.prepare_units(result))
    before = store.units(run), store.parts(run, 0), store.get(run)['config']
    store.transition(run, 'running', ('queued',))
    with calls.scope(run, 'generation'):
        calls.finish(calls.reserve(), None)
    with db.connect() as connection:
        connection.execute('DROP TABLE optimization_packet_nodes')
        connection.execute('ALTER TABLE optimization_calls DROP COLUMN node_key')
        connection.execute('UPDATE schema_meta SET version=24')
    db.init_db()
    db.init_db()
    assert (store.units(run), store.parts(run, 0), store.get(run)['config']) == before
    assert calls.usage(run)['calls_used'] == 1
    assert packets.nodes(run) == []
    with db.connect() as connection:
        assert connection.execute('SELECT version FROM schema_meta').fetchone()[0] == 25
        assert not connection.execute('PRAGMA foreign_key_check').fetchall()


def test_packet_sampling_is_verified_and_advanced_override_wins():
    payload = {'messages': []}
    assert 'presence_penalty' not in provider.request_body(PROFILE, payload)
    profile = {**PROFILE, 'packet_presence_penalty': 0}
    assert provider.request_body(profile, payload)['presence_penalty'] == 0
    assert provider.request_body({**profile, 'extra_body': {'presence_penalty': .2}}, payload)['presence_penalty'] == .2
    assert 'presence_penalty' not in provider.request_body(
        {**profile, 'extra_body': {'presence_penalty': None}}, payload)


def test_only_validated_model_profiles_enable_packets_without_mutating_saved_settings(local):
    unsupported = {**PROFILE, 'model': 'qwen3.5:4b'}
    policy.configure(unsupported)
    assert unsupported['packet_version'] is None
    assert 'packet_presence_penalty' not in unsupported
    supported = {**PROFILE, 'model': 'qwen3.5:9b'}
    store.save_settings(supported)
    policy.configure(supported)
    assert supported['packet_version'] is None
    assert 'packet_presence_penalty' not in store.settings()
    supported['packet_presence_penalty'] = 0  # Returned by the new synthetic verification.
    policy.configure(supported)
    assert supported['packet_version'] == 1
    store.save_settings(supported)
    assert not policy.capabilities(unsupported)['enabled']
    assert policy.capabilities(supported)['max_items'] == 12
    with TestClient(api.create_app()) as client:
        value = client.get('/api/v1/capabilities').json()['asr']['transcript_optimization']['batching']
        assert value['enabled'] and value['eligible_models'] == ['qwen3.5:9b']
        store.save_settings(unsupported)
        value = client.get('/api/v1/capabilities').json()['asr']['transcript_optimization']['batching']
        assert not value['enabled'] and value['max_items'] == 1


@pytest.mark.parametrize('stage', ['review', 'finalization', 'joint'])
def test_independent_review_receives_the_submitted_hotword_snapshot(stage):
    config = {**PROFILE, '_hotwords': ['Atlas']}
    item = {'id': '0', 'original_text': '阿特拉斯预算还没有批准。',
            'candidate': 'Atlas 的预算尚未批准。'}
    request = (policy.joint_payload(config, [item], [], []) if stage == 'joint'
               else policy.payload(config, [item], stage))
    assert 'Atlas' in request['messages'][0]['content']
    assert '来源明确对应' in request['messages'][0]['content']


def test_documented_fine_insight_example_and_schema(local, monkeypatch, capsys):
    from audio_intel.api_docs import API_DESCRIPTION
    section = API_DESCRIPTION.split('## 合并正文与细分小结 / Merged prose and fine insights')[1]
    code = re.findall(r'```python\n(.*?)```', section, re.S)[0]
    def handle(request):
        assert request.headers['Authorization'] == 'Bearer synthetic-key'
        assert request.url.params['run_id'] == 'a' * 32
        return httpx.Response(200, json={'paragraphs': [{'group_summary': {'subgroups': [
            {'id': 'd:g1', 'start': 0, 'end': 4, 'sentences': ['Keep the original records.']}]}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    for key, value in {'AUDIO_INTEL_API_KEY': 'synthetic-key', 'AUDIO_INTEL_BASE_URL': 'http://test.invalid',
                       'OPTIMIZATION_JOB_ID': 'b' * 32, 'OPTIMIZATION_RUN_ID': 'a' * 32}.items():
        monkeypatch.setenv(key, value)
    exec(compile(code, '<fine-insights-example>', 'exec'), {})
    assert 'd:g1 0 4' in capsys.readouterr().out
    schema = api.create_app().openapi()
    assert 'subgroups' in schema['components']['schemas']['GroupSummary']['properties']


def test_packet_json_duplicates_reject_only_the_repeated_id():
    from audio_intel.optimization_packets import unique_object
    assert json.loads('{"a":"valid","b":"one","b":"two"}', object_pairs_hook=unique_object) == {'a': 'valid'}


def test_cross_item_shortcut_is_not_a_completed_body():
    with pytest.raises(ValueError, match='shortcut'):
        policy.checked('这个任务需要确认负责人。', '已在前文合并。')
    assert policy.checked('同上。', '同上。') == '同上。'


@pytest.mark.parametrize(('source', 'draft', 'message'), [
    ('The next topic is how we build small applications.',
     'Lighting and realistic movement make the game fun.', 'source-word overlap'),
    ('We use Blender to render a short product video.',
     'Lighting and realistic movement make the game fun.', 'source-word overlap'),
    ('The project manager checks the work at each desk.',
     'We can render a video and play the game.', 'source-word overlap'),
    ('We can create shapes on this board and edit them.',
     'They feel realistic and are fun to play.', 'source-word overlap'),
    ('The API is not ready yet.', 'API 尚未就绪。', 'English source in English'),
    ('Please ask Mira whether the Atlas build is ready.',
     'Please ask Nora whether the Cedar build is ready.', 'source names'),
])
def test_english_body_rejects_translation_foreign_content_and_changed_names(source, draft, message):
    with pytest.raises(ValueError, match=message):
        policy.checked_item({'original_text': source, 'scene_ids': []}, draft, english=True)


@pytest.mark.parametrize(('source', 'draft'), [
    ('I have used the API, but it is not ready yet.',
     'The API is not ready yet, although I have used it.'),
    ("I've asked Mira to check the Atlas build.",
     'I have asked Mira to review the Atlas build.'),
    ('Please use Fable Five Point Two with Blender.',
     'Please use Fable 5.2 with Blender.'),
    ('Do the rollout steps still need review?',
     'Are the deployment steps still awaiting review?'),
    ('a short fragment about the board,', 'a short fragment about the board,'),
    ('这是 API 的英文示例。', '这是 API 的英文示例。'),
])
def test_english_body_keeps_paraphrasing_fragments_and_mixed_language(source, draft):
    assert policy.english_body_error(source, draft) is None
    assert policy.checked_item({'original_text': source, 'scene_ids': []}, draft, english=True) == draft


def test_english_generation_examples_preserve_language_without_changing_cjk_requests():
    config = {**PROFILE, 'rewrite_mode': 'rewrite', 'english_source': True}
    english = policy.payload(config, [{'id': '0', 'original_text': 'We should review the build.'}], 'generation')
    assert 'English prose' in english['messages'][0]['content']
    assert english['messages'][1:3] == policy.ENGLISH_EXAMPLE
    planned = policy.payload(config, [{'id': '0', 'original_text': 'Who will review it? Is it ready?'}], 'generation')
    assert planned['messages'][1:3] == policy.ENGLISH_PLANNED_EXAMPLE
    chinese = policy.payload(config, [{'id': '0', 'original_text': '请检查这份报告。'}], 'generation')
    assert 'English prose' not in chinese['messages'][0]['content']
    assert chinese['messages'][1:3] == policy.EXAMPLE


def test_english_video_edits_only_complete_interior_sentences():
    item = {'id': '0', 'scene_ids': ['scene'], 'original_text':
            'continued from the previous scene. Please review the Atlas build. And then'}
    assert policy.generation_source(item, english=True).strip() == 'Please review the Atlas build.'
    assert not policy.literal_frame(item, english=True)
    assert policy.checked_item(item, 'Please check the Atlas build.', english=True) == (
        'continued from the previous scene. Please check the Atlas build. And then')
    request = policy.payload({**PROFILE, 'rewrite_mode': 'rewrite', 'english_source': True}, [item], 'generation')
    data = json.loads(request['messages'][-1]['content'])
    assert data[0]['source'].strip() == 'Please review the Atlas build.'
    assert 'previous scene' not in data[0]['source'] and 'And then' not in data[0]['source']
    with pytest.raises(ValueError, match='only claim'):
        policy.checked_item(item, 'Please only review the Atlas build.', english=True)


def test_english_video_boundary_fragments_are_literal_and_audio_keeps_full_editing():
    fragment = {'id': '0', 'scene_ids': ['scene'],
                'original_text': 'video in three dimensions. So not'}
    assert policy.literal_frame(fragment, english=True) and not policy.generation_source(fragment, english=True)
    audio = {**fragment, 'scene_ids': []}
    assert not policy.literal_frame(audio, english=True)
    assert policy.generation_source(audio, english=True) == audio['original_text']
    chinese = {**fragment, 'original_text': '这句话延续上一个画面。请检查。然后'}
    assert not policy.literal_frame(chinese, english=True)
    assert policy.generation_source(chinese, english=True) == chinese['original_text']
    abbreviation = {**fragment, 'original_text': 'Dr. Mira arrived. Please review the Atlas build. And then'}
    assert policy.generation_source(abbreviation, english=True).strip() == 'Please review the Atlas build.'


def test_english_rules_require_the_transcription_language_and_do_not_force_latin_languages():
    config = {**PROFILE, 'rewrite_mode': 'rewrite'}
    result = transcript(video=True)
    result['language'] = 'Spanish'
    result['segments'][0]['text'] = 'Este es el primer tema. Debemos revisar el informe. Y después'
    units = policy.prepare(config, result, [])
    assert 'english_source' not in config
    assert not policy.literal_frame(units[0])
    request = policy.payload(config, [units[0]], 'generation')
    assert 'English prose' not in request['messages'][0]['content']
    assert json.loads(request['messages'][-1]['content'])[0]['source'] == units[0]['original_text']
    result['language'] = 'English'
    result['segments'][0]['text'] = 'The first topic. Please review the Atlas build. And then'
    units = policy.prepare(config, result, [])
    assert config['english_source']
    assert policy.generation_source(units[0], english=True).strip() == 'Please review the Atlas build.'


@pytest.mark.parametrize('has_interior', [False, True])
def test_english_video_keeps_boundaries_insights_and_actual_budget(packet_model, monkeypatch, has_interior):
    async def completion(connection, body, activity=None):
        data = json.loads(body['messages'][-1]['content'])
        if isinstance(data, list):
            assert has_interior
            assert data[0]['source'].strip() == 'Please review the Atlas build.'
            return json.dumps({v['id']: v['source'].replace('review', 'check') for v in data})
        assert isinstance(data, dict) and data['units']
        return json.dumps({
            'reviews': {u['id']: {'verdict': 'pass', 'source_quote': '',
                                'candidate_quote': '', 'reason': ''} for u in data['units']},
            'groups': {g['id']: {'keywords': ['Atlas', 'build'] if has_interior else ['Blender', 'video']} for g in data['groups']},
            'scenes': {s['id']: {'keywords': ['Atlas', 'build'] if has_interior else ['Blender', 'video']} for s in data['scenes']}})
    monkeypatch.setattr(provider, 'completion', completion)
    result = transcript(video=True)
    result['language'] = 'English'
    result['segments'][0]['text'] = ('continued from another scene. Please review the Atlas build. And then'
                                   if has_interior else 'video rendered in Blender. So not')
    identifier = job(result)
    with TestClient(api.create_app()) as client:
        estimate = client.post(url(identifier) + '/estimate', json={}).json()
        assert estimate['breakdown']['generation'] == int(has_interior)
        assert estimate['estimated_calls'] == 1 + int(has_interior)
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']
        value = client.get(url(identifier) + '/result', params={'run_id': run['id']}).json()
        assert run['calls_used'] == 1 + int(has_interior)
        assert value['basic_cleanup_units'] == int(not has_interior)
        assert value['paragraphs'][0]['text'] == (result['segments'][0]['text'].replace('review', 'check')
                                                if has_interior else result['segments'][0]['text'])
        assert value['paragraphs'][0]['group_summary']['state'] == 'ready'
        assert value['scenes'][0]['title_summary']['state'] == 'ready'
        assert value['paragraphs'][0]['group_summary']['generation_method'] == 'model_keywords'
        assert value['scenes'][0]['title_summary']['generation_method'] == 'model_keywords'
        assert calls.usage(run['id'])['stages'] == ({'generation': 1, 'joint': 1}
                                                 if has_interior else {'joint': 1})


def test_insights_keep_explicit_restarts_without_promoting_the_retracted_value():
    assert policy.insight_error('预算三千，不，是四千，尚未审批。', '预算四千，尚未审批。', 'group') is None
    assert policy.insight_error('预算三千，不，是四千，尚未审批。', '预算三千已审批。', 'group')


def test_budget_exhaustion_is_a_pause_and_resume_keeps_all_failed_attempts(packet_model, monkeypatch):
    _, normal = packet_model
    requests = []
    async def offline(connection, body, activity=None):
        requests.append(body)
        raise httpx.ConnectError('interrupted provider')
    monkeypatch.setattr(provider, 'completion', offline)
    identifier = job(transcript())
    with TestClient(api.create_app()) as client:
        response = client.post(url(identifier), json={'max_llm_calls': 2},
                               headers={'Idempotency-Key': 'packet-budget-pause'})
        assert response.status_code == 202
        run = wait(client, identifier, lambda value: value['candidate']['state'] == 'paused')['candidate']['id']
        assert calls.usage(run)['calls_used'] == len(requests) == 2
        assert store.units(run)[0]['text'] is None
        assert store.status(identifier)['published'] is None
        monkeypatch.setattr(provider, 'completion', normal)
        response = client.post(url(identifier) + '/resume', json={'run_id': run, 'max_llm_calls': 6})
        assert response.status_code == 200
        done = wait(client, identifier, lambda value: bool(value['published']))['published']
        assert done['id'] == run and done['calls_used'] == 4
        assert calls.usage(run)['failed_or_interrupted'] == 2
        assert store.settings()['max_llm_calls'] == 300


def test_every_fine_insight_is_delivered_and_exported_with_redaction(packet_model):
    from audio_intel.transcript_export import TranscriptExportRequest, optimized_export_result, format_transcript
    from audio_intel import transcript_docx, transcript_pdf
    from docx import Document
    from pypdf import PdfReader
    import io
    data = transcript(5, length=6)
    for item in data['segments']:
        item['speaker'] = 'same'
        item['text'] = '预算四千元，但是还没批准。请明天向负责人核实。' * 6
    old = engine.prepare_units(data)
    identifier = job(data)
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']['id']
        result = client.get(url(identifier) + '/result', params={'run_id': run})
        assert result.status_code == 200
        paragraphs = result.json()['paragraphs']
        details = [detail for p in paragraphs for detail in p['group_summary']['subgroups']]
        assert len(details) == len(old)
        assert all(detail['state'] == 'ready' and detail['sentences'] for detail in details)
        assert [detail['id'] for detail in details] == ['d:' + unit['id'] for unit in old]
        assert '_detail_sources' not in result.text
        count = client.get(url(identifier) + '/summaries', params={'run_id': run}).json()['summary_status']['counts']['group']
        assert count['total'] == count['ready'] == len(details) + len(paragraphs)
    policy_export = TranscriptExportRequest(format='docx', text_version='optimized', optimization_id=run,
                                           replacements=[{'source': '四千元', 'target': '预算值'}])
    exported = optimized_export_result(db.get_job(identifier), policy_export)
    assert '四千元' not in json.dumps(exported, ensure_ascii=False)
    assert format_transcript(exported, 'txt').count('Segment summary') == len(old)
    document = Document(io.BytesIO(transcript_docx.render('Fine insights', exported, db.settings.data_dir)))
    assert '\n'.join(p.text for p in document.paragraphs).count('Segment summary') == len(old)
    pdf = PdfReader(io.BytesIO(transcript_pdf.render('Fine insights', exported, db.settings.data_dir)))
    assert '\n'.join(page.extract_text() for page in pdf.pages).count('Segment summary') == len(old)


def test_co_review_transport_retries_count_for_each_insight(packet_model, monkeypatch):
    _, normal = packet_model
    failures = []
    async def transient(connection, body, activity=None):
        data = json.loads(body['messages'][-1]['content'])
        if 'response_format' not in body:
            return '预算四千元，但尚未批准。你明天询问一下，好吗？'
        if isinstance(data, dict) and 'units' in data and len(failures) < 2:
            failures.append(1)
            raise httpx.ConnectError('temporary interruption')
        return await normal(connection, body, activity)
    monkeypatch.setattr(provider, 'completion', transient)
    identifier = job(transcript())
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']['id']
        assert calls.usage(run)['calls_used'] == 4
        values = client.get(url(identifier) + '/summaries', params={'run_id': run}).json()
        assert values['groups']['0']['attempt_count'] == 3


def test_insight_repair_cannot_reset_transport_allowance(packet_model, monkeypatch):
    _, normal = packet_model
    failures = []
    async def transient(connection, body, activity=None):
        data = json.loads(body['messages'][-1]['content'])
        if isinstance(data, list) and 'candidate' in data[0]:
            if len(failures) < 2:
                failures.append(1)
                raise httpx.ConnectError('temporary interruption')
            value = json.loads(await normal(connection, body, activity))
            for item in value.values():
                item['summary'] = '无关的超长文字' * 80
            return json.dumps(value)
        return await normal(connection, body, activity)
    monkeypatch.setattr(provider, 'completion', transient)
    identifier = job(transcript(7, length=7))
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']['id']
        assert calls.usage(run)['stages']['finalization'] == 3
        assert 'group' not in calls.usage(run)['stages']
        groups = client.get(url(identifier) + '/summaries', params={'run_id': run}).json()['groups']
        assert all(item['attempt_count'] == 3 for item in groups.values())
        assert all(item['generation_method'] == 'source_excerpt' for item in groups.values())


def test_output_truncation_splits_packets_without_increasing_limits(packet_model, monkeypatch):
    _, normal = packet_model
    attempts, truncated = [], []
    async def limited(connection, body, activity=None):
        attempts.append(body['max_tokens'])
        data = json.loads(body['messages'][-1]['content'])
        if isinstance(data, list) and '档位要求' in body['messages'][0]['content'] and len(data) > 1:
            truncated.append(len(data))
            raise provider.OutputLimitError('provider truncated the packet')
        return await normal(connection, body, activity)
    monkeypatch.setattr(provider, 'completion', limited)
    identifier = job(transcript(7, length=7))
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']['id']
        assert truncated
        assert set(attempts) == {4096}
        assert calls.usage(run)['calls_used'] == len(attempts)
        assert calls.usage(run)['failed_or_interrupted'] == len(truncated)
        assert all(unit['generation_method'] == 'model' for unit in store.units(run))


def test_auxiliary_retry_preserves_body_reviews_and_every_successful_fine_item(packet_model):
    requests, _ = packet_model
    data = transcript(5, length=6)
    for item in data['segments']:
        item['speaker'] = 'same'
        item['text'] = '预算四千元，但是还没批准。请明天向负责人核实。' * 6
    identifier = job(data)
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        run = wait(client, identifier, lambda value: bool(value['published']))['published']['id']
        body = store.units(run)[0]['text']
        used, before = calls.usage(run)['calls_used'], len(requests)
        with db.connect() as connection:
            connection.execute("UPDATE optimization_summaries SET state='unavailable',value_json=NULL WHERE run_id=? AND kind='group' AND item_id='d:g1'", (run,))
            connection.execute("UPDATE optimization_units SET review_outcome='fact' WHERE run_id=?", (run,))
            connection.execute("UPDATE transcript_optimizations SET summary_state='partial' WHERE id=?", (run,))
        response = client.post(url(identifier) + '/summaries/retry', json={'run_id': run},
                               headers={'Idempotency-Key': 'packet-fine-retry-only'})
        assert response.status_code == 202
        wait(client, identifier, lambda value: value['published']['summary_status']['state'] == 'completed')
        assert calls.usage(run)['calls_used'] == used + 1
        assert store.units(run)[0]['text'] == body
        assert store.units(run)[0]['review_outcome'] == 'fact'
        assert len(requests[before:]) == 1
        assert [item['id'] for item in requests[-1]] == ['d:g1']


def test_resume_after_co_review_only_repairs_pending_insight(packet_model, monkeypatch):
    requests, normal = packet_model
    entered = threading.Event()
    async def interrupted(connection, body, activity=None):
        data = json.loads(body['messages'][-1]['content'])
        if isinstance(data, list) and 'candidate' in data[0]:
            values = json.loads(await normal(connection, body, activity))
            values[data[0]['id']]['summary'] = '超出长度限制' * 80
            return json.dumps(values)
        if 'TOP1小结' in body['messages'][0]['content'] and '档位要求' not in body['messages'][0]['content']:
            entered.set()
            await asyncio.sleep(60)
        return await normal(connection, body, activity)
    monkeypatch.setattr(provider, 'completion', interrupted)
    identifier = job(transcript(7, length=7))
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        assert entered.wait(4)
        run = store.status(identifier)['candidate']['id']
        body = [unit['text'] for unit in store.units(run)]
        assert all(unit['review_outcome'] == 'pass' for unit in store.units(run))
        assert store.get(run)['phase'] == 'summaries'
        assert client.post(url(identifier) + '/pause', json={'run_id': run}).status_code == 200
        before, used = len(requests), calls.usage(run)['calls_used']
        monkeypatch.setattr(provider, 'completion', normal)
        assert client.post(url(identifier) + '/resume', json={'run_id': run}).status_code == 200
        wait(client, identifier, lambda value: bool(value['published']))
        assert [unit['text'] for unit in store.units(run)] == body
        assert calls.usage(run)['calls_used'] == used + 1
        assert len(requests[before:]) == 1
        assert all('candidate' not in item for item in requests[-1])


@pytest.mark.parametrize('stage', ['group', 'scene'])
def test_english_insights_use_language_specific_instructions(stage):
    item = {'id': '0', 'original_text': 'The Atlas report needs more review before approval.'}
    config = {**PROFILE, 'rewrite_mode': 'rewrite', 'english_source': True}
    request = policy.payload(config, [item], stage)
    system = request['messages'][0]['content']
    assert 'English' in system and 'cut-off fragment' in system
    assert 'refusal' in system and 'attribution' in system
    assert not re.search('[\u3400-\u9fff]', system)
    assert json.loads(request['messages'][-1]['content']) == [{'id': '0', 'source': item['original_text']}]
    assert request['format']['properties']['0'] == {'type': 'string'}
    config.pop('english_source')
    assert policy.payload(config, [item], stage)['messages'][0]['content'] != system


@pytest.mark.parametrize('kind', ['group', 'scene'])
def test_fragmented_english_video_topics_only_accept_literal_source_phrases(kind):
    from audio_intel.optimization_packets import Processor
    processor = Processor.__new__(Processor)
    processor.profile = {'english_source': True, 'summary_scene_ids': ['frame']}
    item = {'id': '0', 'original_text': 'Alex said the model used Blender. The budget is four thousand, still unapproved.'}
    value = processor.resolve_insight(item, kind, {'keywords': ['Blender', 'foreign physics', 'unapproved']})
    assert value['method'] == 'model_keywords' and value['notice'] == 'keywords'
    assert value['texts'] == ['Blender · unapproved']
    with pytest.raises(ValueError):
        processor.resolve_insight(item, kind, {'keywords': ['Alex created the video', 'approved budget']})
    with pytest.raises(ValueError):
        processor.resolve_insight(item, kind, 'Alex created the video.')
    fallback = processor.resolve_insight(item, kind, None, fallback=True)
    assert fallback['method'] == 'source_excerpt'
    processor.profile = {'english_source': True, 'summary_scene_ids': []}
    assert processor.resolve_insight(item, kind, 'Blender')['method'] == 'model'
    processor.profile = {'summary_scene_ids': ['frame']}
    with pytest.raises(ValueError):
        processor.resolve_insight(item, kind, {'keywords': ['Blender']})


def test_english_video_topic_schema_covers_parent_details_titles_and_joint():
    cfg = {**PROFILE, 'rewrite_mode': 'rewrite', 'english_source': True, 'summary_scene_ids': ['frame']}
    item = {'id': '0', 'original_text': 'Alex said the model used Blender.', 'candidate': 'Alex said the model used Blender.'}
    for stage in ['group', 'scene']:
        request = policy.payload(cfg, [item], stage)
        assert request['format']['properties']['0']['required'] == ['keywords']
        assert 'VERBATIM' in request['messages'][0]['content']
    request = policy.payload(cfg, [item], 'finalization')
    assert request['format']['properties']['0']['properties']['summary']['required'] == ['keywords']
    request = policy.joint_payload(cfg, [item], [item], [item])
    for key in ['groups', 'scenes']:
        assert request['format']['properties'][key]['properties']['0']['required'] == ['keywords']
    assert policy.REVIEW_SCHEMA['properties'].get('summary') is None
    cfg.pop('english_source')
    assert policy.payload(cfg, [item], 'group')['format']['properties']['0'] == {'type': 'string'}


def test_literal_english_topics_reject_negation_and_word_substrings():
    source = 'The plan is unapproved. It was not approved. GPU4090 is available.'
    assert policy.topic_words(source, ['approved', '4090', 'GPU', 'available'], 'group') == ['available']
    assert policy.topic_words(source, ['unapproved', 'not approved', 'GPU4090'], 'group') == ['unapproved · not approved · GPU4090']
