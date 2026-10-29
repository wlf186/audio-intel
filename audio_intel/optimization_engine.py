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
        for attempt in range(3):
            try:
                generated = await generate(profile, sample, [], '', '', None)
                if getattr(generated, 'method', 'model') != 'model':
                    raise InvalidOutput('模型未返回可用正文 / Model did not return usable prose')
                break
            except (InvalidOutput, json.JSONDecodeError) as exc:
                if getattr(exc, 'exhausted', False):
                    raise
                if attempt == 2:
                    raise InvalidOutput(
                        '模型输出未符合要求，已自动重试两次；请重试，持续失败时检查 Ollama 版本及模型 / '
                        'Invalid model output after two retries; check Ollama and model. ' + str(exc)
                    ) from exc
                await asyncio.sleep(2 ** attempt)
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
    from .optimization_sources import source_groups
    units = source_groups(result)
    if not units:
        raise ValueError('任务没有可优化的文字 / No transcript text to optimize')
    return units


def payload_for(config: dict, batch: list[dict], hotwords: list[str], before, after) -> dict:
    if len(batch) != 1:
        raise ValueError('Edit one source group at a time')
    unit = batch[0]
    mode = config.get('rewrite_mode', DEFAULT_MODE)
    if mode not in MODE_INSTRUCTIONS:
        raise ValueError('Unknown rewrite mode')
    # Short turns are often acknowledgements or fragments. Adjacent speech
    # must not become their content, especially with a small local model.
    allowance = 0 if len(unit['original_text']) <= 30 else min(200, len(unit['original_text']) // 2)
    def bounded_context(value, *, previous=False):
        if not allowance:
            return ''
        if not isinstance(value, list):
            value = [{'speaker': unit.get('speaker'), 'text': str(value or ''), 'truncated': False}]
        selected, remaining = [], allowance
        for part in reversed(value) if previous else value:
            if remaining <= 0:
                break
            text = str(part.get('text', ''))
            if not text:
                continue
            excerpt = text[-remaining:] if previous else text[:remaining]
            selected.append({'speaker': part.get('speaker'), 'text': excerpt,
                             'truncated': bool(part.get('truncated')) or len(excerpt) < len(text)})
            remaining -= len(excerpt)
        return list(reversed(selected)) if previous else selected
    content = {'editing_goal': MODE_INSTRUCTIONS[mode], 'hotwords': hotwords,
               'context_before': bounded_context(before, previous=True), 'context_after': bounded_context(after),
               'source_segments': [span['text'] for span in unit.get('sources', [])] or [unit['original_text']],
               'target': {'speaker': unit.get('speaker'), 'text': unit['original_text']}}
    return {'model': config['model'], 'messages': [{'role': 'system', 'content': SYSTEM},
            {'role': 'user', 'content': json.dumps(content, ensure_ascii=False)}],
            'stream': True, 'think': False,
            'options': {'num_ctx': config['context_length'], 'num_predict': 4096, 'temperature': 0.2, 'presence_penalty': 0}}


def review_messages(source: dict, candidate: dict) -> list[dict]:
    # Editing instructions must never enter the independent fact checker.
    content = {key: source[key] for key in ('target', 'hotwords', 'context_before', 'context_after') if key in source}
    content['candidate'] = candidate.get('text', '')
    return [{'role': 'system', 'content': REVIEW_SYSTEM},
            {'role': 'user', 'content': json.dumps(content, ensure_ascii=False)}]


def request_fits(config: dict, payload: dict) -> bool:
    # UTF-8 bytes conservatively bound tokens, including structured grammar.
    return len(json.dumps(payload['messages'], ensure_ascii=False).encode()) + len(json.dumps(payload.get('format', {}))) + 5120 <= config['context_length']


def fits(config: dict, batch: list[dict], hotwords: list[str], before='', after='') -> bool:
    payload = payload_for(config, batch, hotwords, before, after)
    source = json.loads(payload['messages'][1]['content'])
    review_payload = {**payload, 'messages': review_messages(source, {'text': '文' * (len(batch[0]['original_text']) * 2 + 128)}), 'format': REVIEW_SCHEMA}
    return request_fits(config, payload) and request_fits(config, review_payload)


def seam_review_fits(config: dict, length: int, hotwords: list[str]) -> bool:
    # A forced boundary can move at most 160 draft characters into the prior
    # part and add 64 punctuation/joining characters. Review owns one part plus
    # its two seam neighbors; reserve their full maximum valid drafts up front.
    draft_limit = max(256, length * 2 + 128) + 224
    payload = {'messages': review_messages({'target': {'text': '文' * (length * 3)}, 'hotwords': hotwords},
                                           {'text': '文' * (draft_limit * 3)}), 'format': REVIEW_SCHEMA}
    return request_fits(config, payload)


def prepare_parts(config: dict, unit: dict, hotwords: list[str]) -> list[dict]:
    """Persist a deterministic partition; only owned spans contribute progress."""
    text = unit['original_text']
    parts, offset = [], 0
    # Smaller prose requests improve fact ownership and avoid turning a long
    # multi-source turn into a summary. This is independent of reading groups.
    ceiling = 160
    while ceiling >= 40 and (not fits(config, [{'original_text': '文' * ceiling, 'speaker': unit.get('speaker')}], hotwords)
                              or not seam_review_fits(config, ceiling, hotwords)):
        ceiling -= 20
    if ceiling < 40:
        raise ValueError('热词超出窗口预算，请增大窗口并重新验证 / Increase the context window')
    # Test ASCII periods against the full source, not a truncated slice, so a
    # decimal/model version at the request edge cannot masquerade as a sentence.
    sentence_ends = [match.end() for match in re.finditer(r'(?:[。！？；!?;]|\.(?=\s|$))\s*', text)]
    comma_ends = [match.end() for match in re.finditer(r'[，,]\s*', text)]
    whitespace_ends = [match.end() for match in re.finditer(r'\s+', text)]

    def boundary(offset, size):
        limit = min(len(text), offset + size)
        if len(text) - offset <= min(100, size):
            return len(text), False
        breaks = [end for end in sentence_ends if offset + min(40, size // 2) <= end <= limit]
        if not breaks:
            breaks = [end for end in comma_ends if offset + min(60, size // 2) <= end <= limit]
        if breaks:
            return min(breaks, key=lambda end: (abs(end - offset - min(90, size)), -end)), False
        spaces = [end for end in whitespace_ends if offset + size // 2 <= end <= limit]
        end = spaces[-1] if spaces else limit
        return end, end < len(text)

    while offset < len(text):
        end, forced = boundary(offset, ceiling)
        sources, cursor = [], 0
        for span in unit.get('sources', []):
            start = text.find(span['text'], cursor)
            if start < 0:
                raise ValueError('Source mapping could not be verified')
            stop = start + len(span['text']); cursor = stop
            left, right = max(offset, start), min(end, stop)
            if left < right:
                sources.append({**span, 'text': text[left:right], 'start_offset': span['start_offset'] + left - start,
                                'end_offset': span['start_offset'] + right - start})
        parts.append({'original_text': text[offset:end], 'speaker': unit.get('speaker'), 'sources': sources,
                      'start_offset': offset, 'end_offset': end, 'forced_after': forced})
        offset = end
    return parts


def validate_budget(config: dict, units: list[dict], hotwords: list[str]) -> None:
    for unit in units:
        unit['_parts'] = prepare_parts(config, unit, hotwords)


class InvalidOutput(ValueError):
    pass


class ReviewFailed(ValueError):
    """Legacy compatibility for stored v5 failures and external callers."""
    def __init__(self, message, candidate_text=None, review_details=None):
        super().__init__(message)
        self.candidate_text, self.review_details = candidate_text, review_details


async def streamed_text(config: dict, payload: dict, activity=None) -> str:
    if not request_fits(config, payload):
        raise InvalidOutput('请求超出上下文预算 / Request exceeds context budget')
    async with asyncio.timeout(120), client(config) as connection:
        content, final, last = '', None, time.monotonic()
        async with connection.stream('POST', 'api/chat', json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                if len(line) > 1024**2:
                    raise InvalidOutput('Ollama response too large')
                try:
                    item = json.loads(line)
                except ValueError as exc:
                    raise InvalidOutput('Invalid stream event') from exc
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
        return content


async def structured(config: dict, payload: dict, activity=None) -> dict:
    try:
        value = json.loads(await streamed_text(config, payload, activity))
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidOutput('模型 JSON 无效 / Invalid model JSON') from exc
    if not isinstance(value, dict):
        raise InvalidOutput('Invalid model object')
    return value


def basic_cleanup(text: str) -> str:
    """Literal disfluencies only: no invented facts or inferred word corrections."""
    pattern = r'(?P<word>不是特别|还没有|第一个|我们|你们|他们|这个|那个|然后|因为|所以|但是|就是|不是|还没|不止|我|你|他)(?:[\s，,、]*(?P=word))+'
    value = re.sub(pattern, lambda match: match['word'], text)
    value = re.sub(r'^[\s，,]*(?:那个|呃|额)[，,\s]*', '', value)
    value = re.sub(r'([，,。；;！？!?])\1+', r'\1', value).strip()
    return value or text.strip()


class Generation(list):
    def __init__(self, values, method='model'):
        super().__init__(values)
        self.method = method


def clean_output(value: str, original: str) -> str:
    text = value.strip()
    if text.startswith('```') and text.endswith('```'):
        text = re.sub(r'^```(?:text|markdown)?\s*\n?', '', text)[:-3].strip()
    if text.startswith('{'):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and isinstance(parsed.get('text'), str):
                text = parsed['text'].strip()
            else:
                raise InvalidOutput('Not transcript prose')
        except json.JSONDecodeError as exc:
            raise InvalidOutput('Not transcript prose') from exc
    limit = max(12, len(original) * 2 + 6) if len(original) <= 30 else len(original) * 2 + 64
    if not text or len(text) > limit:
        raise InvalidOutput('Invalid edited text length')
    return text


def missing_qualifiers(original: str, candidate: str) -> list[str]:
    """Narrow lexical safety check, not a general semantic fact classifier.

    Common equivalent scope markers are accepted. Ordinary negation (不/不是),
    retracted locations/numbers and editing style never trigger this check.
    """
    equivalents = {
        '不止': ('不止', '不只', '不仅', '不限于', '超过', '多于'),
        '不只': ('不止', '不只', '不仅', '不限于', '超过', '多于'),
        '不仅': ('不止', '不只', '不仅', '不限于'),
        '并非': ('并非', '不是', '并不', '不等于'),
        '未必': ('未必', '不一定', '不见得', '不能确定', '无法确定'),
        '只能': ('只能', '仅能', '只可', '仅可', '仅限', '只允许', '只支持'),
    }
    return [word for word, alternatives in equivalents.items()
            if word in original and not any(alternative in candidate for alternative in alternatives)]


async def generate(config: dict, batch: list[dict], hotwords: list[str], before, after, activity) -> list[tuple[int, str]]:
    original = batch[0]['original_text']
    payload = payload_for(config, batch, hotwords, '', '')
    best = None
    lost_qualifiers = []
    for attempt in range(2):
        try:
            text = clean_output(await streamed_text(config, payload, activity), original)
            lost_qualifiers = missing_qualifiers(original, text)
            if lost_qualifiers:
                raise InvalidOutput('Edited prose lost a source qualifier')
            needs_edit = bool(remaining_repetition(original, text) or (text == original and basic_cleanup(original) != original))
            if best is None or not needs_edit or len(re.findall(r'那个|然后|嗯|呃|啊|哎|哇', text)) < len(re.findall(r'那个|然后|嗯|呃|啊|哎|哇', best)):
                best = text
            if not needs_edit:
                return Generation([(batch[0]['ordinal'], text)])
        except (InvalidOutput, json.JSONDecodeError):
            pass
        except (httpx.HTTPError, TimeoutError):
            if best is not None:
                return Generation([(batch[0]['ordinal'], best)])
            raise
        if attempt == 0:
            guidance = '请进一步整理target，主动删除嗯、啊、那个、然后等口头铺垫，合并重复意思，重组为简洁连贯的完整句子。只保留无法确定的局部原词，其余部分必须整理。保留所有独立信息和疑问，不补事实。只输出正文。'
            if lost_qualifiers:
                guidance += '上次正文丢失了原文限定词：' + '、'.join(lost_qualifiers) + '。本次必须保留这些限定原词及其范围，分句关系含糊时用分号分开，不猜测关系。'
            payload['messages'].append({'role': 'user', 'content': guidance})
    return Generation([(batch[0]['ordinal'], best if best is not None else basic_cleanup(original))], 'model' if best is not None else 'basic_cleanup')


def validate_draft(value: dict, original: str) -> None:
    # Compatibility validator for historical import/checkpoint callers.
    if not isinstance(value, dict) or set(value) != {'action', 'text'} or value['action'] not in ('keep', 'omit') or not isinstance(value['text'], str):
        raise InvalidOutput('Invalid edit contract')
    if value['action'] == 'omit':
        if value['text'] or not omission_candidate(original):
            raise InvalidOutput('Cannot omit meaningful text')
    else:
        clean_output(value['text'], original)


def grounded_quote(quote: str, text: str, *, allow_empty=False) -> str:
    import unicodedata
    if not quote:
        if allow_empty:
            return ''
        raise InvalidOutput('Missing source evidence')
    normalized, positions = '', []
    for index, char in enumerate(text):
        for value in unicodedata.normalize('NFKC', char):
            if not value.isspace():
                normalized += value; positions.append(index)
    needle = ''.join(c for c in unicodedata.normalize('NFKC', quote) if not c.isspace())
    start = normalized.find(needle)
    if not needle or start < 0 or normalized.find(needle, start + 1) >= 0:
        raise InvalidOutput('Review evidence is not uniquely grounded')
    return text[positions[start]:positions[start + len(needle) - 1] + 1]


def validate_review(value: dict, original: str, candidate: str) -> dict:
    if value.get('verdict') not in ('pass', 'fact', 'uncertain'):
        raise InvalidOutput('Invalid review verdict')
    verdict = value['verdict']
    if verdict == 'pass':
        return {'outcome': 'pass', 'details': {'stage': 'review', 'issues': []}}
    if verdict == 'uncertain':
        return {'outcome': 'uncertain', 'details': {'stage': 'review', 'issues': []}}
    if any(not isinstance(value.get(key), str) for key in ('source_quote', 'candidate_quote', 'reason')) or not value['reason'].strip() or len(value['reason']) > 600:
        raise InvalidOutput('Invalid fact evidence')
    issue = {'source_quote': grounded_quote(value['source_quote'], original),
             'candidate_quote': grounded_quote(value['candidate_quote'], candidate, allow_empty=True), 'reason': value['reason']}
    return {'outcome': 'fact', 'details': {'stage': 'review', 'issues': [issue]}}


async def review(config: dict, original: str, candidate: str, hotwords: list[str], before='', after='') -> dict:
    if original == candidate:
        return {'outcome': 'pass', 'details': {'stage': 'review', 'issues': []}}
    payload = {'model': config['model'], 'messages': review_messages({'target': {'text': original}, 'hotwords': hotwords,
               'context_before': before, 'context_after': after}, {'text': candidate}), 'format': REVIEW_SCHEMA,
               'stream': True, 'think': False, 'options': {'num_ctx': config['context_length'], 'num_predict': 4096, 'temperature': 0}}
    first = None
    for attempt in range(2):
        try:
            result = validate_review(await structured(config, payload), original, candidate)
            if result['outcome'] == 'pass':
                return result if first is None else {'outcome': 'uncertain', 'details': {'stage': 'review', 'issues': []}}
            if result['outcome'] == 'uncertain':
                return result
            if first is not None:
                return result
            first = result
            payload['messages'][0]['content'] += '\n这是独立复查：只报告确定的事实差异。合理去重和风格改变必须判 pass。'
        except (InvalidOutput, json.JSONDecodeError):
            continue
    return {'outcome': 'uncertain' if first else 'unavailable', 'details': {'stage': 'review', 'issues': []}}


async def repair_seam(config: dict, left: dict, right: dict, hotwords: list[str]) -> tuple[str, str]:
    """Replace only a bounded junction; persisted originals remain authoritative."""
    # Reserve a disjoint suffix for the next seam when this is a short middle
    # part. Once an incoming prefix has moved left, the next seam only sees the
    # unconsumed suffix, including after pause/restart.
    head_size = min(160, len(right['text']) // 2 if right['source']['forced_after'] else len(right['text']))
    tail, head = left['text'][-160:], right['text'][:head_size]
    if not tail or not head:
        return left['text'], right['text']
    source = left['source']['original_text'] + right['source']['original_text']
    unit = {'ordinal': 0, 'speaker': left['source'].get('speaker'), 'original_text': source}
    payload = payload_for(config, [unit], hotwords, '', '')
    content = json.loads(payload['messages'][1]['content'])
    content['junction_draft'] = tail + '\n' + head
    content['editing_goal'] = '只整理 junction_draft 中因分批产生的重复、半句话和标点。target仅供核对事实；不要输出 junction_draft 之外的信息。只输出接缝正文。'
    payload['messages'][1]['content'] = json.dumps(content, ensure_ascii=False)
    try:
        joined = clean_output(await streamed_text(config, payload), tail + head)
        if len(joined) > len(tail) + len(head) + 64:
            raise InvalidOutput('Seam exceeds its bounded edit window')
        return left['text'][:-len(tail)] + joined, right['text'][len(head):]
    except InvalidOutput:
        return left['text'], right['text']


def reading_result(result: dict, run_id: str, *, complete: bool = False) -> dict:
    run, units = store.snapshot(run_id)
    if complete and not run['published']:
        raise ValueError('请等待优化完成后导出 / Export requires a completed draft')
    if source_hash(result) != run['source_hash']:
        raise ValueError('原文已变化，请重新优化 / Source text changed; regenerate the draft')
    current = {s['id']: s for s in result.get('segments', [])}
    scenes = deepcopy(result.get('video', {}).get('scenes', []))
    for scene in scenes:
        scene['paragraphs'] = []
    from .optimization_sources import scene_index, matching_scenes
    indexed_scenes = scene_index(scenes)
    scene_positions = {scene['id']: i for i, scene in enumerate(scenes)}
    values = []
    for unit in units:
        first = current.get(unit['segment_ids'][0], {})
        related = sorted({scene['id'] for span in unit['sources'] for scene in matching_scenes(indexed_scenes, span['start'], span['end'])}, key=scene_positions.__getitem__)
        value = {**unit, 'omitted': unit['text'] == '', 'speaker': first.get('speaker', unit.get('speaker')),
                 'speaker_label': first.get('speaker_label', unit.get('speaker_label')),
                 'merged_source_count': len(set(unit['segment_ids'])), 'scene_ids': related}
        values.append(value)
        if scenes:
            if run['config'].get('prompt_version', 1) >= 6:
                owner = scenes[scene_positions[related[0]]] if related else scenes[0]
            else:
                owner = max(scenes, key=lambda scene: max(0, min(unit['end'], scene['end']) - max(unit['start'], scene['start'])))
            owner['paragraphs'].append(value)
    fields = ('review_state', 'reviewed_units', 'uncertain_units', 'unavailable_units', 'basic_cleanup_units')
    return {'run_id': run_id, 'complete': bool(run['published']), 'model': run['model'],
            'prompt_version': run['config'].get('prompt_version', 1), 'rewrite_mode': run['config'].get('rewrite_mode'),
            'needs_review_units': run['needs_review_units'], 'paragraphs': values, 'scenes': scenes,
            'review_total_units': run['total_units'], **{field: run[field] for field in fields}}
