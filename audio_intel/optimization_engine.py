"""Bounded Ollama generation and source-preserving reading units."""
from __future__ import annotations

import asyncio
import json
import re
import ssl
import os
import time
from copy import deepcopy
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import optimization_store as store
from .transcript import paragraphs, scene_transcripts

from .optimization_policy import PROMPT_VERSION, SYSTEM, SCHEMA, REVIEW_SYSTEM, REVIEW_SCHEMA, REVIEW_EXAMPLES, DEFAULT_MODE, MODE_INSTRUCTIONS, omission_candidate, remaining_repetition


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('请输入 HTTP(S) 服务地址，不含用户名、密码或查询参数 / Enter an HTTP(S) base URL')
    path = parsed.path.rstrip('/')
    for suffix in ('/api/chat', '/api/tags', '/api'):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def client(config: dict) -> httpx.AsyncClient:
    # Proxy opt-out must not discard explicitly configured CA trust.
    verify = ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE') or None, capath=os.environ.get('SSL_CERT_DIR') or None)
    return httpx.AsyncClient(base_url=config['base_url'] + '/', trust_env=config['use_proxy'], verify=verify,
                            follow_redirects=False, timeout=httpx.Timeout(120, connect=10), limits=httpx.Limits(max_connections=2))


async def remote_json(connection: httpx.AsyncClient, path: str, payload: dict | None = None) -> dict:
    async with connection.stream('GET' if payload is None else 'POST', path, json=payload) as response:
        response.raise_for_status()
        content = bytearray()
        async for part in response.aiter_bytes():
            content.extend(part)
            if len(content) > 8 * 1024**2:
                raise ValueError('Ollama response exceeds limit / Ollama 响应过大')
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError('Invalid Ollama response / Ollama 响应格式不正确')
    return result


async def discover(config: dict) -> dict:
    async with client(config) as connection:
        version, tags = await asyncio.gather(remote_json(connection, 'api/version'), remote_json(connection, 'api/tags'))
    return {'version': version.get('version'), 'models': [{'name': m['name'], 'digest': m['digest'], 'size': m.get('size'), 'capabilities': m.get('capabilities', [])} for m in tags.get('models', []) if 'name' in m and 'digest' in m]}


async def verify(config: dict) -> dict:
    async with client(config) as connection:
        tags, details, running = await asyncio.gather(remote_json(connection, 'api/tags'), remote_json(connection, 'api/show', {'model': config['model']}), remote_json(connection, 'api/ps'))
        selected = next((m for m in tags.get('models', []) if m['name'] == config['model']), None)
        if not selected or 'completion' not in details.get('capabilities', []):
            raise ValueError('请选择支持文本生成的已安装模型 / Choose an installed text generation model')
        maximum = max((int(v) for k, v in details.get('model_info', {}).items() if k.endswith('.context_length')), default=0)
        actual = next((int(m.get('context_length') or 0) for m in running.get('models', []) if m['name'] == config['model']), 0)
        context = min(maximum, actual if actual >= 16384 else 32768)
        if context < 16384:
            raise ValueError('本功能要求至少 16K 上下文 / At least 16K context is required')
        thinking = details.get('thinking', {}).get('values')
        if thinking and False not in thinking:
            raise ValueError('请选择可关闭思考的模型 / Choose a model supporting non-thinking output')
        profile = {**config, 'context_length': context, 'model_context_length': maximum, 'model_digest': selected['digest'], 'prompt_version': PROMPT_VERSION}
        sample = [{'id': 'p1', 'ordinal': 0, 'speaker': 'speaker1', 'original_text': '那个那个预算是15万元，不是50万元。这个暂时还没有确认。'}]
        await generate(profile, sample, [], '', '', None)
        running = await remote_json(connection, 'api/ps')
        observed = next((int(m.get('context_length') or 0) for m in running.get('models', []) if m['name'] == config['model']), 0)
        if observed < 16384:
            raise ValueError('无法验证实际运行窗口至少为 16K / Unable to verify the allocated context window')
        profile['context_length'] = min(context, observed)
        profile['verified_at'] = store.db.utcnow()
        return profile


def source_hash(result: dict) -> str:
    # Renaming people or editing picture boundaries does not change source text.
    return store.digest([(s['id'], s['text'], s['start'], s['end']) for s in result.get('segments', [])])


def prepare_units(result: dict) -> list[dict]:
    groups = scene_transcripts(result, include_sources=True) if result.get('video', {}).get('scenes') else [{'paragraphs': paragraphs(result.get('segments', []), include_sources=True)}]
    units = []
    for group in groups:
        for paragraph in group['paragraphs']:
            text = paragraph['text']
            offset = 0
            while offset < len(text):
                end = min(len(text), offset + 800)
                if end < len(text):
                    breaks = [m.end() for m in re.finditer(r'[。！？.!?；;\n]\s*', text[offset:end]) if m.end() >= 400]
                    if breaks:
                        end = offset + breaks[-1]
                part = text[offset:end]
                if part.strip():
                    sources = []
                    cursor = 0
                    for source in paragraph['sources']:
                        start = text.find(source['text'], cursor)
                        if start < 0:
                            raise ValueError('Source mapping could not be verified')
                        stop = start + len(source['text']); cursor = stop
                        left, right = max(offset, start), min(end, stop)
                        if left < right:
                            sources.append({**source, 'text': text[left:right], 'start_offset': source['start_offset'] + left - start, 'end_offset': source['start_offset'] + right - start})
                    units.append({'id': f'p{len(units) + 1}', 'speaker': paragraph['speaker'], 'speaker_label': paragraph.get('speaker_label'),
                                  'start': min(s['start'] for s in sources), 'end': max(s['end'] for s in sources),
                                  'original_text': part, 'sources': sources, 'segment_ids': list(dict.fromkeys(s['segment_id'] for s in sources))})
                offset = end
    if not units:
        raise ValueError('任务没有可优化的文字 / No transcript text to optimize')
    return units


def payload_for(config: dict, batch: list[dict], hotwords: list[str], before, after) -> dict:
    if len(batch) != 1:
        raise ValueError('Edit one source paragraph at a time')
    unit = batch[0]
    mode = config.get('rewrite_mode', DEFAULT_MODE)
    if mode not in MODE_INSTRUCTIONS:
        raise ValueError('Unknown rewrite mode')
    content = {'rewrite_mode': mode, 'editing_goal': MODE_INSTRUCTIONS[mode], 'hotwords': hotwords, 'context_before': before, 'context_after': after,
               'target': {'speaker': unit['speaker'], 'text': unit['original_text']}}
    schema = deepcopy(SCHEMA)
    if not omission_candidate(unit['original_text']):
        schema['properties']['action']['enum'] = ['keep']
    return {'model': config['model'], 'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': json.dumps(content, ensure_ascii=False)}],
            'format': schema, 'stream': True, 'think': False,
            'options': {'num_ctx': config['context_length'], 'num_predict': 4096, 'temperature': 0.2, 'presence_penalty': 0}}


def review_messages(source: dict, candidate: dict) -> list[dict]:
    messages = [{'role': 'system', 'content': REVIEW_SYSTEM}]
    for example, decision in REVIEW_EXAMPLES:
        messages.extend([{'role': 'user', 'content': json.dumps(example, ensure_ascii=False)},
                         {'role': 'assistant', 'content': json.dumps({key: decision[key] for key in REVIEW_SCHEMA['properties']}, ensure_ascii=False)}])
    messages.append({'role': 'user', 'content': json.dumps({**source, 'candidate': candidate}, ensure_ascii=False)})
    return messages


def fits(config: dict, batch: list[dict], hotwords: list[str], before='', after='') -> bool:
    payload = payload_for(config, batch, hotwords, before, after)
    source = json.loads(payload['messages'][1]['content'])
    original = batch[0]['original_text']
    candidate = {'action': 'keep', 'text': '文' * max(256, len(original) * 2 + 128)}
    repair = deepcopy(payload['messages'])
    repair[1]['content'] = json.dumps({**source, 'previous_draft': candidate, 'issues': [
        {'kind': 'fact', 'source_quote': original, 'candidate_quote': candidate['text'], 'reason': '文' * 400}]}, ensure_ascii=False)
    # Budget the actual review examples and largest bounded repair, not a fixed allowance.
    return all(len(json.dumps(messages, ensure_ascii=False).encode()) + len(json.dumps(schema)) + 4096 + 1024 <= config['context_length']
               for messages, schema in ((payload['messages'], SCHEMA), (repair, SCHEMA), (review_messages(source, candidate), REVIEW_SCHEMA)))


def validate_budget(config: dict, units: list[dict], hotwords: list[str]) -> None:
    if any(not fits(config, [unit], hotwords, '文' * 600, '文' * 600) for unit in units):
        raise ValueError('热词和正文超出窗口预算，请增大 Ollama 上下文并重新验证 / Increase the context window and verify again')


class InvalidOutput(ValueError):
    pass


async def structured(config: dict, payload: dict, activity) -> dict:
    if len(json.dumps(payload['messages'], ensure_ascii=False).encode()) + len(json.dumps(payload['format'])) + 4096 + 1024 > config['context_length']:
        raise ValueError('请求超出上下文预算 / Request exceeds context budget')
    async with asyncio.timeout(300), client(config) as connection:
        content, final, last = '', None, time.monotonic()
        async with connection.stream('POST', 'api/chat', json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                if len(line) > 1024**2:
                    raise InvalidOutput('Ollama response too large')
                item = json.loads(line)
                if item.get('error'):
                    raise ValueError('Ollama: ' + str(item['error'])[:300])
                content += item.get('message', {}).get('content', '')
                if len(content) > 100_000:
                    raise InvalidOutput('Output exceeds budget')
                if activity and time.monotonic() - last >= 1:
                    await asyncio.to_thread(activity, len(content)); last = time.monotonic()
                if item.get('done'):
                    final = item
        if not final or final.get('done_reason') != 'stop':
            raise InvalidOutput('输出截断或未正常结束 / Output was truncated')
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError) as exc:
            raise InvalidOutput('模型 JSON 无效 / Invalid model JSON') from exc
        if not isinstance(parsed, dict):
            raise InvalidOutput('模型对象无效 / Invalid model object')
        return parsed


class ReviewFailed(ValueError):
    pass


def validate_draft(value: dict, original: str) -> None:
    if set(value) != {'action', 'text'} or value.get('action') not in ('keep', 'omit') or not isinstance(value.get('text'), str):
        raise InvalidOutput('Invalid edit contract')
    if value['action'] == 'omit':
        if value['text'] != '' or not omission_candidate(original):
            raise InvalidOutput('非占位语不得省略 / Cannot omit meaningful text')
    elif not value['text'].strip() or len(value['text']) > max(256, len(original) * 2 + 128):
        raise InvalidOutput('Invalid edited text length')


def validate_review(value: dict, original: str, candidate: str) -> list[dict]:
    if set(value) != {'verdict', 'source_quote', 'candidate_quote', 'reason'} or any(not isinstance(v, str) for v in value.values()) or value['verdict'] not in ('pass', 'fact', 'style'):
        raise InvalidOutput('Invalid review contract')
    issue = {key: value[key] for key in ('source_quote', 'candidate_quote', 'reason')}
    if value['verdict'] == 'pass':
        if any(issue.values()):
            raise InvalidOutput('Passing review must not report an issue')
        return []
    if not issue['source_quote'] or issue['source_quote'] not in original or issue['candidate_quote'] not in candidate or not issue['reason'].strip() or len(issue['reason']) > 400:
        raise InvalidOutput('Review evidence is not grounded in this paragraph')
    return [{'kind': value['verdict'], **issue}]


async def generate(config: dict, batch: list[dict], hotwords: list[str], before, after, activity) -> list[tuple[int, str]]:
    payload = payload_for(config, batch, hotwords, before, after)
    original = batch[0]['original_text']
    source = json.loads(payload['messages'][1]['content'])
    best = None
    for attempt in range(2):
        try:
            candidate = await structured(config, payload, activity)
            validate_draft(candidate, original)
        except (InvalidOutput, json.JSONDecodeError):
            if best is not None:
                return [(batch[0]['ordinal'], best)]
            raise
        repetition = remaining_repetition(original, candidate['text'])
        review = deepcopy(payload)
        review['messages'] = review_messages(source, candidate)
        review['format'] = REVIEW_SCHEMA
        try:
            issues = validate_review(await structured(config, review, activity), original, candidate['text'])
        except (InvalidOutput, json.JSONDecodeError):
            if best is not None:
                return [(batch[0]['ordinal'], best)]
            raise
        # Exact equality proves fidelity but still requires a language-quality check.
        if candidate['text'] == original and issues and issues[0]['kind'] == 'fact':
            issues = [{'kind': 'style', 'source_quote': original, 'candidate_quote': original, 'reason': '候选与原文相同，没有新事实错误。请按档位主动整理可改善的语言，保留局部疑点。'}]
        if original.rstrip().endswith(('？', '?')) and not candidate['text'].rstrip().endswith(('？', '?')):
            issues = [{'kind': 'fact', 'source_quote': original[-100:], 'candidate_quote': candidate['text'][-100:], 'reason': '末尾的询问或确认必须保留为问题，不能改成既定事实。'}]
        fact_error = bool(issues and issues[0]['kind'] == 'fact')
        if repetition and not fact_error:
            issues = [{'kind': 'style', 'source_quote': repetition, 'candidate_quote': repetition, 'reason': '仍有明显的重复结巴，请去重并保留完整意思。'}]
        if not fact_error:
            # Prefer a reviewed rewrite over a later repair that simply restores the source.
            if best is None or candidate['text'] != original:
                best = candidate['text']
        if not issues:
            return [(batch[0]['ordinal'], best)]
        if attempt == 0:
            payload['messages'][1]['content'] = json.dumps({**source, 'previous_draft': candidate, 'issues': issues}, ensure_ascii=False)
    if best is not None:
        return [(batch[0]['ordinal'], best)]
    raise ReviewFailed(f"第 {batch[0]['ordinal'] + 1} 段未获得可靠改写 / No reliable rewrite for this paragraph")


def reading_result(result: dict, run_id: str, *, complete: bool = False) -> dict:
    run = store.get(run_id)
    if complete and not run['published']:
        raise ValueError('请等待优化完成后导出 / Export requires a completed draft')
    if source_hash(result) != run['source_hash']:
        raise ValueError('原文已变化，请重新优化 / Source text changed; regenerate the draft')
    current = {s['id']: s for s in result.get('segments', [])}
    values = []
    for unit in store.units(run_id):
        first = current.get(unit['segment_ids'][0], {})
        values.append({**unit, 'omitted': unit['text'] == '', 'speaker': first.get('speaker', unit['speaker']), 'speaker_label': first.get('speaker_label', unit['speaker_label'])})
    scenes = deepcopy(result.get('video', {}).get('scenes', []))
    for scene in scenes:
        scene['paragraphs'] = []
    if scenes:
        for unit in values:
            owner = max(range(len(scenes)), key=lambda i: max(0, min(unit['end'], scenes[i]['end']) - max(unit['start'], scenes[i]['start'])))
            scenes[owner]['paragraphs'].append(unit)
    return {'run_id': run_id, 'complete': bool(run['published']), 'model': run['model'], 'rewrite_mode': run['config'].get('rewrite_mode'), 'needs_review_units': run['needs_review_units'], 'paragraphs': values, 'scenes': scenes}
