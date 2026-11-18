"""Source-grounded reading order shared by browsers and document exports."""
from __future__ import annotations

from collections import defaultdict, OrderedDict
from copy import deepcopy
from hashlib import sha256
from threading import Lock
import json
from .transcript import scene_transcripts, reading_sections, presentation_sections


_cache = OrderedDict()
_lock = Lock()


def build(result: dict, catalogue: dict, titles: dict | None = None) -> dict:
    # Cache only compact projections, keyed by content, never progress counters.
    key = sha256(json.dumps([result.get('segments', []), (result.get('video') or {}).get('scenes', []), catalogue, titles], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return deepcopy(_cache[key])
    value = _build(result, catalogue, titles)
    with _lock:
        _cache[key] = value
        while len(_cache) > 16: _cache.popitem(last=False)
    return deepcopy(value)


def _build(result: dict, catalogue: dict, titles: dict | None = None) -> dict:
    titles = titles or {}
    topics = catalogue.get('topics', [])
    references = defaultdict(list)
    for topic in topics:
        for interval in topic['ranges']:
            for ref in interval['source_refs']:
                references[ref['segment_id']].append((ref['start_offset'], ref['end_offset'], topic['id']))
    scenes = scene_transcripts(result, include_sources=True)
    units = scenes or [{'id': str(s['id']), 'start': s['start'], 'end': s['end'],
                        'paragraphs': [{'sources': [{'segment_id': s['id'], 'start_offset': 0, 'end_offset': len(s['text'])}]}]} for s in result.get('segments', [])]
    texts = {s['id']: s['text'] for s in result.get('segments', [])}
    groups = [{'topic_id': t['id'], 'title': t['title'], 'summary': t['summary'],
               'primary_item_ids': [], 'related_item_ids': []} for t in topics]
    groups.append({'topic_id': None, 'title': '未归类 / Unclassified', 'summary': '', 'primary_item_ids': [], 'related_item_ids': []})
    indexed = {g['topic_id']: g for g in groups}
    items, number = [], 0
    for unit in units:
        support = defaultdict(set)
        for paragraph in unit['paragraphs']:
            for ref in paragraph.get('sources', []):
                for a, b, topic in references.get(ref['segment_id'], []):
                    for offset in range(max(a, ref['start_offset']), min(b, ref['end_offset'])):
                        if texts[ref['segment_id']][offset].isalnum(): support[topic].add((ref['segment_id'], offset))
        scores = {key: len(value) for key, value in support.items() if value}
        method = 'source'
        if not scores:
            method = 'time'
            for topic in topics:
                # Union intervals so overlapping fallback timestamps do not double-count.
                spans = sorted((max(unit['start'], r['start']), min(unit['end'], r['end'])) for r in topic['ranges'] if r['start'] < unit['end'] and r['end'] > unit['start'])
                end, total = -1, 0
                for a, b in spans:
                    total += max(0, b - max(a, end)); end = max(end, b)
                if total: scores[topic['id']] = total
        ranked = sorted(scores, key=lambda key: (-scores[key], next(i for i, t in enumerate(topics) if t['id'] == key)))
        owner = ranked[0] if ranked else None
        identity = str(unit['id'])
        if unit.get('image'): number += 1
        title = (titles.get(identity) or unit.get('title_summary') or {}).get('title')
        item = {'id': identity, 'kind': 'scene' if scenes else 'paragraph',
                'title': title or (f'画面 {number}' if unit.get('image') else f'片段 {len(items)+1}'),
                'number': number if unit.get('image') else None, 'start': unit['start'], 'end': unit['end'],
                'primary_topic_id': owner, 'related_topic_ids': ranked[1:], 'assignment_method': method if owner else 'unclassified'}
        items.append(item); indexed[owner]['primary_item_ids'].append(identity)
        for key in ranked[1:]: indexed[key]['related_item_ids'].append(identity)
    return {'version': 1, 'groups': [g for g in groups if g['primary_item_ids'] or g['related_item_ids']], 'items': items}


def sections(result: dict) -> list[dict]:
    """Reorder presentation only, keeping each image and paragraph exactly once."""
    outline = result.get('reading_outline')
    if outline and outline['version'] == 2:
        return block_sections(result, outline)
    output = reading_sections(result)
    if result.get('video'): output = presentation_sections(output)
    outline = result.get('reading_outline')
    if not outline: return output
    if result.get('video'):
        numbers = {str(scene['id']): n for n, scene in enumerate(result['video']['scenes'], 1)}
        indexed = {str(s['id']): {**s, 'export_number': numbers[str(s['id'])]} for s in output}
        links = {key: key for key in indexed}
    else:
        # A merged paragraph remains intact and belongs to one primary topic.
        items = {i['id']: i for i in outline['items']}
        indexed, links = {}, {}
        for number, paragraph in enumerate(result.get('segments', []), 1):
            refs = [str(key) for key in paragraph.get('segment_ids', [])] or [str(r['segment_id']) for r in paragraph.get('sources', [])] or [str(paragraph['id'])]
            direct = [items[key] for key in refs if key in items]
            scores = defaultdict(float)
            for item in direct or outline['items']:
                weight = 1 if direct else max(0, min(item['end'], paragraph['end']) - max(item['start'], paragraph['start']))
                scores[item['primary_topic_id']] += weight
            ranked = sorted(outline['groups'], key=lambda group: -scores[group['topic_id']])
            owner = ranked[0]['topic_id'] if ranked and scores[ranked[0]['topic_id']] else None
            identity = 'paragraph:' + str(paragraph['id'])
            title = direct[0]['title'] if direct else f'片段 {number}'
            indexed[identity] = {'id': identity, 'paragraphs': [paragraph], '_topic_owner': owner, 'export_number': number,
                                 'start': paragraph['start'], 'end': paragraph['end'], 'title_summary': {'title': title}}
            for ref in refs: links.setdefault(ref, identity)
    labels = items_by_id(outline)
    grouped = []
    for group in outline['groups']:
        children = ([indexed[key] for key in group['primary_item_ids'] if key in indexed] if result.get('video') else
                    [s for s in indexed.values() if s['_topic_owner'] == group['topic_id']])
        grouped.append({'id': 'topic:' + str(group['topic_id']), 'topic_heading': group['title'], 'topic_id': group['topic_id'], 'paragraphs': [],
                        'topic_summary': group['summary'], 'related_item_ids': list(dict.fromkeys(links[key] for key in group['related_item_ids'] if key in links)),
                        'related_titles': {links[key]: labels[key]['title'] for key in group['related_item_ids'] if key in links}})
        grouped.extend(children)
    # Defensive compatibility for historical, incomplete projections.
    included = {s['id'] for s in grouped}
    grouped.extend(s for key, s in indexed.items() if key not in included)
    return grouped


def items_by_id(outline: dict) -> dict:
    return {item['id']: item for item in outline['items']}


def block_sections(result: dict, outline: dict) -> list[dict]:
    """One source-backed fragment per child; frames are reusable references."""
    segments = defaultdict(list)
    for segment in result['segments']:
        segments[segment.get('reading_block_id')].append(segment)
    scenes = {s['id']: s for s in (result.get('video') or {}).get('scenes', [])}
    items = items_by_id(outline)
    groups = outline['merged_groups'] if outline.get('merge_repeated_topics') else outline['groups']
    output = []
    for group in groups:
        children = [items[key] for key in group['primary_item_ids'] if segments.get(key)]
        if not children: continue
        output.append({'id':group['occurrence_id'], 'topic_id':group['topic_id'], 'topic_heading':group['title'], 'topic_summary':group['summary'], 'paragraphs':[], 'related_item_ids':[]})
        for block in children:
            frames = [scenes[key] for key in block['scene_ids'] if key in scenes and scenes[key].get('image')]
            output.append({'id':block['id'], 'reading_block':True, 'export_number':block['number'], 'title_summary':{'title':block['title']},
                           'start':block['start'], 'end':block['end'], 'paragraphs':segments[block['id']],
                           'image':frames[0]['image'] if frames else None, 'frame_scene_id':frames[0]['id'] if frames else None,
                           'frame_number':block['frame_numbers'][0] if frames else None})
            for index, frame in enumerate(frames[1:], 1):
                output.append({'id':block['id']+':'+frame['id'], 'reading_resource':True, 'reading_block':True,
                               'title_summary':{}, 'start':frame['start'], 'end':frame['end'], 'paragraphs':[],
                               'image':frame['image'], 'frame_scene_id':frame['id'], 'frame_number':block['frame_numbers'][index]})
    return output


def export_headings(result: dict, sections: list[dict]) -> dict[str, str]:
    """Visible export titles keyed by the same IDs as internal navigation."""
    outline = result.get('reading_outline')
    if not outline:
        return {}
    source_items = items_by_id(outline)
    occurrences = defaultdict(int)
    headings = {}
    for section in sections:
        if section.get('reading_resource'):
            continue
        identity = section['id']
        if 'topic_heading' in section:
            key = section['topic_id']
            occurrences[key] += 1
            title = section['topic_heading'].strip() or '议题'
            if occurrences[key] > 1:
                title += f'（续 {occurrences[key] - 1}）'
        else:
            title = (section.get('title_summary') or {}).get('title') or ''
            frame = section.get('frame_number')
            if frame and not section.get('reading_block'):
                title = f'画面 {frame}' + (f' · {title}' if title.strip() else '')
            if not title.strip():
                title = source_items.get(identity, {}).get('title') or f"片段 {section['export_number']}"
        headings[identity] = title
    return headings
