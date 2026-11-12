"""Durable per-run call limits and deterministic, model-free admission estimates."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import math
from functools import wraps

from fastapi import HTTPException

from . import db

DEFAULT_LIMIT = 300
DEFAULT_OUTPUT = 4096
_scope: ContextVar[tuple[str, str, str | None] | None] = ContextVar('optimization_call_scope', default=None)


class BudgetExhausted(Exception):
    """Must propagate past model fallback handlers without publishing a draft."""


def migrate(c):
    columns = {r[1] for r in c.execute('PRAGMA table_info(transcript_optimizations)')}
    for name, declaration in {'calls_limit': 'INTEGER', 'calls_used': 'INTEGER NOT NULL DEFAULT 0',
                              'estimate_json': 'TEXT', 'budget_error': 'TEXT'}.items():
        if name not in columns:
            c.execute(f'ALTER TABLE transcript_optimizations ADD COLUMN {name} {declaration}')
    c.execute('''CREATE TABLE IF NOT EXISTS optimization_calls (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES transcript_optimizations(id) ON DELETE CASCADE,
        stage TEXT NOT NULL, state TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, finished_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS optimization_budget_retries (
        job_id TEXT NOT NULL, key_hash TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES transcript_optimizations(id) ON DELETE CASCADE,
        max_calls INTEGER, PRIMARY KEY(job_id,key_hash))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_optimization_calls_run ON optimization_calls(run_id,id)')
    c.execute('UPDATE schema_meta SET version=24 WHERE version<24')


@contextmanager
def scope(run_id: str, stage: str, node_key: str | None = None):
    token = _scope.set((run_id, stage, node_key))
    try:
        yield
    finally:
        _scope.reset(token)


def tracked(stage):
    def decorate(function):
        @wraps(function)
        async def wrapped(self, run_id, *args, **kwargs):
            with scope(run_id, stage):
                return await function(self, run_id, *args, **kwargs)
        return wrapped
    return decorate


def reserve() -> int | None:
    value = _scope.get()
    if value is None:
        return None
    run_id, stage, node_key = value
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT calls_limit,calls_used,state,summary_state FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
        if row is None or not (row['state'] == 'running' or row['state'] == 'succeeded' and row['summary_state'] == 'running'):
            import asyncio
            raise asyncio.CancelledError()
        # Legacy runs have no reliable starting count and retain their snapshots.
        if row['calls_limit'] is None:
            return None
        if row['calls_used'] >= row['calls_limit']:
            raise BudgetExhausted('本次调用预算已用完，请调高本次总预算后继续 / Call budget exhausted; raise this run limit and resume')
        c.execute('UPDATE transcript_optimizations SET calls_used=calls_used+1,revision=revision+1,updated_at=? WHERE id=?', (db.utcnow(), run_id))
        if node_key is None:
            call_id = c.execute("INSERT INTO optimization_calls(run_id,stage,state,created_at) VALUES(?,?,'started',?)", (run_id, stage, db.utcnow())).lastrowid
        else:
            call_id = c.execute("INSERT INTO optimization_calls(run_id,stage,node_key,state,created_at) VALUES(?,?,?,'started',?)", (run_id, stage, node_key, db.utcnow())).lastrowid
        c.commit()
    return call_id


def finish(call_id: int | None, error: BaseException | None):
    if call_id is not None:
        with db.connect() as c:
            c.execute('UPDATE optimization_calls SET state=?,error=?,finished_at=? WHERE id=?',
                      ('completed' if error is None else 'interrupted' if isinstance(error, BaseException) and not isinstance(error, Exception) else 'failed',
                       type(error).__name__ if error else None, db.utcnow(), call_id))


def pause(run_id: str):
    with db.connect() as c:
        c.execute("UPDATE transcript_optimizations SET budget_error='exhausted',error=?,state=CASE WHEN published=0 THEN 'paused' ELSE state END,summary_state=CASE WHEN summary_state='running' THEN 'paused' ELSE summary_state END,revision=revision+1,updated_at=? WHERE id=?",
                  ('本次调用预算已用完，请调整本次预算并继续 / Call budget exhausted; raise this run limit and resume', db.utcnow(), run_id))


def usage(run_id: str) -> dict:
    with db.connect() as c:
        r = c.execute('SELECT calls_limit,calls_used,estimate_json,budget_error FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
        stages = {stage: count for stage, count in c.execute('SELECT stage,count(*) FROM optimization_calls WHERE run_id=? GROUP BY stage', (run_id,))}
        failures = c.execute("SELECT count(*) FROM optimization_calls WHERE run_id=? AND state IN ('failed','interrupted','started')", (run_id,)).fetchone()[0]
    return {'run_id': run_id, 'recorded': r['calls_limit'] is not None, 'max_llm_calls': r['calls_limit'],
            'calls_used': r['calls_used'] if r['calls_limit'] is not None else None,
            'remaining_calls': max(0, r['calls_limit'] - r['calls_used']) if r['calls_limit'] is not None else None,
            'estimate': json.loads(r['estimate_json']) if r['estimate_json'] else None,
            'stages': stages, 'failed_or_interrupted': failures}


def _legacy_breakdown(config: dict, units: list[dict], hotwords: list[str], pending_insights) -> dict:
    from . import optimization_engine as engine, optimization_batch as batching, optimization_summary_policy as policy
    generation = review = seams = 0
    for unit in units:
        try:
            parts = unit.get('_parts') or engine.prepare_parts(config, unit, hotwords)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        pending = [dict(p.get('source', p), id=str(i)) for i, p in enumerate(parts) if p.get('text') is None]
        generation += len(batching.pack(config, 'generation', pending, hotwords))
        # Review all parts conservatively; unchanged drafts later skip review.
        review += sum(1 for p in parts if p.get('review_outcome') is None)
        seams += sum(bool(p.get('source', p).get('forced_after')) and not p.get('seam_done') for p in parts[:-1])
    groups = [{'id': str(i), 'original_text': u['original_text']} for i, u in enumerate(units) if policy.meaningful(u['original_text']) and (pending_insights is None or ('group', str(i)) in pending_insights)]
    group_calls = len(batching.pack(config, 'group', groups, [])) if config.get('summary_version') else 0
    scenes = []
    for scene in config.get('summary_scene_ids', []):
        source = '\n'.join(u['original_text'] for u in units if scene in u.get('scene_ids', []))
        if policy.meaningful(source) and (pending_insights is None or ('scene', str(scene)) in pending_insights):
            scenes.append({'id': scene, 'original_text': source})
    # Upper-bound reduction tree using full source and bounded intermediate notes.
    scene_calls = 0
    for scene in scenes:
        source = scene['original_text']
        for _ in range(8):
            if engine.request_fits(config, policy.request(config, source, 'scene')):
                break
            count = math.ceil(len(source) / 800)  # splitter uses 800..1600 source characters
            scene_calls += count
            reduced = '文' * (count * 102)
            if len(reduced) >= len(source):
                break
            source = reduced
        scene_calls += 1
    breakdown = {'generation': generation, 'review': review, 'seam': seams, 'group': group_calls, 'scene': scene_calls}
    return breakdown


def estimate(config: dict, units: list[dict], hotwords: list[str], *, used: int = 0, limit: int | None = None, pending_insights: set[tuple[str, str]] | None = None) -> dict:
    from . import optimization_engine as engine, optimization_store as store
    from . import optimization_batch as batching, optimization_summary_policy as policy
    limit = limit if limit is not None else config.get('max_llm_calls', DEFAULT_LIMIT)
    breakdown = {}
    if config.get('packet_version') == 1:
        from . import optimization_packet_policy as packets, optimization_packet_store as packet_store
        breakdown = {key: 0 for key in ('generation', 'finalization', 'joint', 'group', 'scene')}
        body = {str(i): unit for i, unit in enumerate(units)}
        for stage, identifiers in packet_store.compile_plan(config, units, hotwords):
            if stage == 'generation':
                needed = any(body[key].get('text') is None for key in identifiers)
            elif stage in ('finalization', 'joint'):
                needed = any(body[key].get('review_outcome') in (None, 'pending') for key in identifiers)
                if pending_insights is None:
                    needed = True
                elif stage == 'joint':
                    needed |= bool(pending_insights)
                else:
                    needed |= any(('group', key) in pending_insights for key in identifiers)
            else:
                kind = 'scene' if stage == 'scene_reduction' else stage
                needed = pending_insights is None or any((kind, key) in pending_insights for key in identifiers)
            if not needed:
                continue
            if stage == 'scene_reduction':
                source = packets.scene_sources(units, identifiers)[0]['original_text']
                count = 1
                for _ in range(8):
                    if engine.request_fits(config, policy.request(config, source, 'scene')):
                        break
                    fragments = math.ceil(len(source) / 800)
                    count += fragments
                    reduced = '文' * (fragments * 102)
                    if len(reduced) >= len(source):
                        break
                    source = reduced
                breakdown['scene'] += count
            else:
                breakdown[stage] += 1
    else:
        breakdown = _legacy_breakdown(config, units, hotwords, pending_insights)
    expected = sum(breakdown.values())
    recommended = math.ceil((used + expected + max(5, math.ceil(expected * .5))) / 10) * 10
    return {'estimated_calls': expected, 'used_calls': used, 'minimum_limit': used + expected,
            'recommended_limit': recommended, 'max_llm_calls': limit, 'sufficient': limit >= recommended,
            'can_start': limit >= used + expected, 'breakdown': breakdown,
            'fingerprint': store.digest({'configuration': {k: config.get(k) for k in ('base_url', 'model', 'use_proxy', 'context_length', 'max_output_tokens', 'extra_body', 'structured_output_mode', 'rewrite_mode', 'summary_version', 'packet_version', 'packet_joint', 'packet_presence_penalty')}, 'batch_max_items': config.get('batch_max_items', 1), 'hotwords': hotwords, 'sources': [{k: u.get(k) for k in ('original_text', 'speaker', 'sources', 'scene_ids', '_detail_sources')} for u in units]})}


def admit(value: dict):
    if not value['can_start']:
        from .api import ApiProblem
        raise ApiProblem(409, 'optimization_budget_insufficient', '调用预算不足，请调整本次预算 / Insufficient call budget', extras={'estimate': value})


def raise_limit(c, run_id: str, limit: int | None):
    if limit is None:
        return
    row = c.execute('SELECT calls_limit,calls_used FROM transcript_optimizations WHERE id=?', (run_id,)).fetchone()
    if row['calls_limit'] is None or limit < max(row['calls_limit'], row['calls_used']):
        raise HTTPException(409, '只能调高本次总预算 / Only an increase to this run limit is allowed')
    c.execute('UPDATE transcript_optimizations SET calls_limit=?,budget_error=NULL,revision=revision+1 WHERE id=?', (limit, run_id))
