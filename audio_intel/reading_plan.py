"""Immutable source-backed reading blocks; topics precede prose generation."""
from __future__ import annotations
from bisect import bisect_left, bisect_right
from copy import deepcopy
import hashlib
from . import optimization_store as store
from .transcript import formatted_positions, join_text

VERSION = 2

def units(result: dict, catalogue: dict) -> list[dict]:
    from .optimization_engine import prepare_units
    from .optimization_packet_policy import reading_ceiling
    topics = catalogue.get('topics', [])
    refs = {}
    for topic in topics:
        for interval in topic['ranges']:
            for ref in interval['source_refs']:
                refs.setdefault(ref['segment_id'], []).append((ref['start_offset'], ref['end_offset'], topic['id']))
    segments = {s['id']: s for s in result['segments']}
    output = []
    for unit in prepare_units(result):
        for span in unit['sources']:
            a, b = span['start_offset'], span['end_offset']
            candidates = refs.get(span['segment_id'], [])
            cuts = sorted({a, b, *(max(a, min(b, x)) for left, right, _ in candidates for x in (left, right))})
            original = segments[span['segment_id']]
            words = original.get('words') or []
            positions = formatted_positions(original['text'], words) if words else []
            for left, right in zip(cuts, cuts[1:]):
                if left == right: continue
                owner = next((key for lo, hi, key in candidates if lo <= left and hi >= right), None)
                text = original['text'][left:right]
                # Non-substantive bridges remain beside their source, not new topics.
                if owner is None and not text.strip(): owner = output[-1]['reading_topic_id'] if output else None
                start, end = span['start'], span['end']
                if positions:
                    first = max(0, bisect_right(positions, left)-1)
                    last = min(len(words)-1, max(first, bisect_left(positions, right)-1))
                    start, end = words[first]['start'], words[last]['end']
                part = {**span, 'text': text, 'start_offset': left, 'end_offset': right, 'start': start, 'end': end}
                previous = output[-1] if output else None
                if (previous and previous['reading_topic_id'] == owner and previous['speaker'] == unit['speaker']
                    and previous['sources'][-1]['segment_id'] == span['segment_id'] and start-previous['end'] <= 15
                    and len(previous['original_text'])+len(text) <= reading_ceiling(previous['original_text']+text,1200)):
                    target = previous
                    old = target['sources'][-1]
                    if old['segment_id']==part['segment_id'] and old['end_offset']==left:
                        target['sources'][-1]={**old,'text':old['text']+text,'end_offset':right,'end':max(old['end'],end)}
                    else: target['sources'].append(part)
                    target['original_text']=join_text(target['original_text'],text)
                    target['end']=max(target['end'],end)
                    target['scene_ids']=list(dict.fromkeys([*target['scene_ids'],*unit['scene_ids']]))
                    target['segment_ids']=list(dict.fromkeys([*target['segment_ids'],span['segment_id']]))
                else:
                    output.append({**unit,'id':'g'+str(len(output)+1),'sources':[part],'original_text':text,
                                   'start':start,'end':end,'segment_ids':[span['segment_id']], 'reading_topic_id':owner, 'timing_precision':'word' if positions else 'segment'})
    previous_topic, occurrence = object(), ''
    for number, unit in enumerate(output,1):
        identity=hashlib.sha256(store.encode([(s['segment_id'],s['start_offset'],s['end_offset']) for s in unit['sources']]).encode()).hexdigest()[:20]
        unit['reading_block_id']='block_'+identity
        if unit['reading_topic_id'] != previous_topic:
            occurrence='occurrence_'+identity;previous_topic=unit['reading_topic_id']
        unit['occurrence_id']=occurrence
        unit['reading_number']=number
        unit['merged_source_count']=len(unit['segment_ids'])
    return output

def freeze(run_id: str, result: dict) -> None:
    from . import db, optimization_topics as topics, optimization_summaries as insights, optimization_packet_store as packets
    from .optimization_engine import prepare_parts
    run=store.get(run_id)
    if run['config'].get('reading_plan_ready'): return
    plan=units(result, topics.read(run_id)['result'])
    config={**run['config'], 'reading_plan_ready':True,'summary_scene_ids':[u['reading_block_id'] for u in plan]}
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        fresh=c.execute('SELECT config_json,state FROM transcript_optimizations WHERE id=?',(run_id,)).fetchone()
        if not fresh or fresh['state']!='running':
            import asyncio
            raise asyncio.CancelledError()
        import json
        if json.loads(fresh['config_json']).get('reading_plan_ready'): return
        for table in ('optimization_units','optimization_summaries','optimization_packet_nodes'):
            c.execute(f'DELETE FROM {table} WHERE run_id=?',(run_id,))
        for i,unit in enumerate(plan):
            c.execute('INSERT INTO optimization_units(run_id,ordinal,source_json) VALUES(?,?,?)',(run_id,i,store.encode(unit)))
            for j,part in enumerate(prepare_parts(config,unit,run['hotwords'])):
                c.execute('INSERT INTO optimization_parts(run_id,unit_ordinal,part_ordinal,source_json) VALUES(?,?,?,?)',(run_id,i,j,store.encode(part)))
        c.execute("UPDATE transcript_optimizations SET config_json=?,phase='generation',total_units=?,completed_units=0,total_chars=?,completed_chars=0,revision=revision+1 WHERE id=?",(store.encode(config),len(plan),sum(len(u['original_text']) for u in plan),run_id))
        insights.initialize(c,run_id,config,plan)
        if config.get('packet_version'): packets.initialize(c,run_id,config,plan,run['hotwords'])
        c.commit()

def outline(result: dict, catalogue: dict, plan: list[dict], titles: dict) -> dict:
    names={t['id']:t for t in catalogue['topics']}
    scenes={s['id']:s for s in (result.get('video') or {}).get('scenes',[])}
    numbers={};n=0
    for scene in scenes.values():
        if scene.get('image'):n+=1;numbers[scene['id']]=n
    groups=[];items=[];merged={}
    for unit in plan:
        topic=names.get(unit.get('reading_topic_id'),{'id':None,'title':'其他内容 / Other content','summary':''})
        identity=unit['reading_block_id']
        title=titles.get(identity) or {}
        from .optimization_summary_policy import extract_source
        excerpts = extract_source(unit['original_text'], 'scene')
        fallback = excerpts[0] if excerpts else ''
        item={'id':identity,'kind':'block','title':title.get('title') or fallback or f"片段 {unit['reading_number']}",
              'number':unit['reading_number'],'start':unit['start'],'end':unit['end'],
              'primary_topic_id':topic['id'],'related_topic_ids':[], 'occurrence_id':unit['occurrence_id'],
              'paragraph_id':unit['id'],'scene_ids':unit['scene_ids'],'timing_precision':unit.get('timing_precision','segment'),
              'frame_numbers':[numbers[s] for s in unit['scene_ids'] if s in numbers],
              'source_refs':[{k:s[k] for k in ('segment_id','start_offset','end_offset','start','end')} for s in unit['sources']]}
        items.append(item)
        if not groups or groups[-1]['occurrence_id']!=unit['occurrence_id']:
            groups.append({'topic_id':topic['id'],'occurrence_id':unit['occurrence_id'],'title':topic['title'],'summary':'','primary_item_ids':[],'related_item_ids':[], 'start':unit['start'],'end':unit['end']})
        groups[-1]['primary_item_ids'].append(identity);groups[-1]['end']=max(groups[-1]['end'],unit['end'])
        key=topic['id']
        if key not in merged: merged[key]={**deepcopy(groups[-1]),'occurrence_id':'merged_'+str(key),'primary_item_ids':[],'summary':topic['summary']}
        merged[key]['primary_item_ids'].append(identity);merged[key]['end']=max(merged[key]['end'],unit['end'])
    seen=set()
    for group in groups:group['recurring']=group['topic_id'] in seen;seen.add(group['topic_id'])
    return {'version':2,'items':items,'groups':groups,'merged_groups':list(merged.values())}
