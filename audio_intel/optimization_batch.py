"""Small independent item envelopes; immutable source boundaries never change."""
from __future__ import annotations

import json
import re

# The current 9B paired gate regresses fact recall and item coverage at size 2.
# Keep new runs on size 1; larger packets remain available to isolated evaluations.
DEFAULT_MAX_ITEMS = 1

from . import optimization_engine as engine
from . import optimization_policy as prose
from . import optimization_summary_policy as summaries


def payload(config: dict, kind: str, items: list[dict], hotwords: list[str]) -> dict:
    item_schema = {'type': 'object', 'properties': {'id': {'type': 'string', 'enum': [p['id'] for p in items]}}, 'required': ['id'], 'additionalProperties': False}
    if kind == 'generation':
        item_schema['properties']['text'] = {'type': 'string'}
        item_schema['required'].append('text')
        system = prose.SYSTEM.replace('只输出成稿正文，不输出JSON、标题、解释或说话人标签。', '')
        task = {'editing_goal': prose.MODE_INSTRUCTIONS[config.get('rewrite_mode', prose.DEFAULT_MODE)],
                'hotwords': hotwords, 'items': [{'id': p['id'], 'target': p['original_text'], 'speaker': p.get('speaker')} for p in items]}
    elif kind == 'review':
        item_schema['properties'].update(prose.REVIEW_SCHEMA['properties'])
        item_schema['required'].extend(prose.REVIEW_SCHEMA['required'])
        system = prose.REVIEW_SYSTEM
        task = {'hotwords': hotwords, 'items': [{'id': p['id'], 'target': p['original_text'], 'candidate': p['candidate']} for p in items]}
    else:
        template = summaries.request(config, items[0]['original_text'], kind)
        system = re.sub(r'只返回JSON：.*?。|Return JSON .*?only\.', '', template['messages'][0]['content'])
        item_schema['properties']['texts'] = summaries.TEXT_SCHEMA['properties']['texts']
        item_schema['required'].append('texts')
        task = {'items': [{'id': p['id'], 'source': p['original_text']} for p in items]}
    system += '\n这是多个独立的编辑任务，绝不是把全部 items 合成一篇。输出必须覆盖这些 id：' + ', '.join(p['id'] for p in items) + '。每条输出只能含该条的内容。\n逐项独立处理 items，信息只能来自同一 id 的来源，禁止跨项借用事实。保持每项原文语言。每个 id 恰好返回一次。只返回 JSON {"items":[{"id":"原id",...}]}，不合并条目。'
    example = {'items': [{'id': p['id'], **({'text': '<该条的实际成稿>'} if kind == 'generation' else {'verdict': 'pass', 'source_quote': '', 'candidate_quote': '', 'reason': ''} if kind == 'review' else {'texts': ['<该条的实际小结或标题>']})} for p in items]}
    return {'response_example': example, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps(task, ensure_ascii=False)}],
            'format': {'type': 'object', 'properties': {'items': {'type': 'array', 'items': item_schema, 'minItems': len(items), 'maxItems': len(items)}}, 'required': ['items'], 'additionalProperties': False},
            'options': {'num_predict': min(config.get('max_output_tokens', 4096), 4096), 'temperature': .2 if kind == 'generation' else 0}}


def pack(config: dict, kind: str, items: list[dict], hotwords: list[str]) -> list[list[dict]]:
    size = min(4, max(1, config.get('batch_max_items', DEFAULT_MAX_ITEMS)))
    if size == 1:
        return [[item] for item in items]
    result, current = [], []
    for item in items:
        candidate = current + [item]
        # Output needs its own capacity check; a large input window alone is insufficient.
        output_estimate = sum(len(p['original_text'].encode()) * (2 if kind == 'generation' else 1) + (700 if kind == 'review' else 180) for p in candidate)
        from .optimization_summaries import CJK
        same_language = not current or bool(CJK.search(current[0]['original_text'])) == bool(CJK.search(item['original_text']))
        fits = (same_language and len(candidate) <= size and output_estimate <= config.get('max_output_tokens', 4096)
                and engine.request_fits(config, payload(config, kind, candidate, hotwords)))
        if current and not fits:
            result.append(current)
            current = []
        current.append(item)
    if current:
        result.append(current)
    return result


async def request(config: dict, kind: str, items: list[dict], hotwords: list[str]) -> dict:
    value = await engine.structured(config, payload(config, kind, items, hotwords))
    rows = value.get('items')
    if not isinstance(rows, list):
        raise engine.InvalidOutput('Missing batch items')
    output = {}
    for item in items:
        matches = [r for r in rows if isinstance(r, dict) and r.get('id') == item['id']]
        if len(matches) != 1:
            continue
        row = matches[0]
        try:
            if kind == 'generation':
                text = engine.clean_output(row['text'], item['original_text'])
                if engine.missing_qualifiers(item['original_text'], text) or prose.remaining_repetition(item['original_text'], text) or not summaries.numbers(text) <= summaries.numbers(item['original_text']):
                    continue
                borrowed = any(other['id'] != item['id'] and any(other['original_text'][i:i+8] in text and other['original_text'][i:i+8] not in item['original_text'] for i in range(max(0, len(other['original_text'])-7))) for other in items)
                if borrowed or '<该条' in text:
                    continue
                output[item['id']] = text
            elif kind == 'review':
                output[item['id']] = engine.validate_review(row, item['original_text'], item['candidate'])
            else:
                texts = row['texts']
                if not isinstance(texts, list) or not texts or len(texts) > (1 if kind == 'scene' else 2):
                    continue
                if not isinstance(texts[0], str) or '<该条' in texts[0] or summaries.candidate_error(item['original_text'], texts[0], kind):
                    continue
                output[item['id']] = summaries.Result([t for t in texts if isinstance(t, str) and not summaries.candidate_error(item['original_text'], t, kind)], 'model')
        except (ValueError, TypeError, KeyError):
            continue
    return output
