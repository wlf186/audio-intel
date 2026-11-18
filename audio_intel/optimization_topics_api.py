"""Authenticated auxiliary topic discovery for already published drafts."""
from __future__ import annotations

import asyncio
import json
import math
import shutil

from fastapi import Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import db, optimization_store as store, optimization_topics as topics
from . import optimization_budget as calls, optimization_engine as engine
from .asr_speakers import speaker_update_lock
from .document_download import reader_lease


class TopicRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    regenerate: bool = False
    max_llm_calls: int | None = Field(None, strict=True, ge=1, le=2147483647)
    estimate_fingerprint: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')


class TopicRef(BaseModel):
    segment_id: int
    start_offset: int
    end_offset: int


class TopicRange(BaseModel):
    start: float
    end: float
    timing_precision: str
    source_refs: list[TopicRef]


class Topic(BaseModel):
    id: str
    title: str
    summary: str
    summary_method: str = "model"
    ranges: list[TopicRange]


class TopicCatalogue(BaseModel):
    topics: list[Topic]
    unclassified: list[TopicRange]
    no_substantive_content: bool


class TopicResponse(BaseModel):
    run_id: str
    version: int | None
    topic_state: str
    topic_revision: int
    topic_completed_nodes: int
    topic_total_nodes: int
    result: TopicCatalogue | None
    error: str | None
    outline: dict | None = None


def estimate(run, result, *, regenerate=False, limit=None):
    atoms = topics.sources(result)
    count = topics.estimate(run['config'], atoms)
    if run['topic_state'] == 'completed' and not regenerate:
        count = 0
    elif not regenerate:
        with db.connect() as c:
            completed = c.execute('SELECT count(*) FROM optimization_topic_nodes WHERE run_id=? AND value_json IS NOT NULL', (run['id'],)).fetchone()[0]
        count = max(1, count - completed)
    used = run['calls_used']
    cap = limit if limit is not None else run['calls_limit']
    return {'estimated_calls': count, 'used_calls': used, 'minimum_limit': used + count,
            'recommended_limit': math.ceil((used + count + (max(5, math.ceil(count * .5)) if count else 0)) / 10) * 10,
            'max_llm_calls': cap, 'sufficient': cap >= used + count, 'can_start': cap >= used + count,
            'breakdown': {'topics': count},
            'fingerprint': store.digest({'run': run['id'], 'source': store.digest(atoms), 'config': run['config'], 'version': topics.VERSION, 'regenerate': regenerate})}


def install(app, root, auth, manager, owned, parent, public_status, errors):
    from .optimization_api import CallEstimate, OptimizationStatus
    from .api import validate_idempotency_key, bilingual
    path = root + '/topics'
    tags = ['Expression optimization / 表达优化']

    def eligible(job_id, body):
        run = owned(job_id, body.run_id)
        if run['config'].get('reading_plan_version') == 2:
            raise HTTPException(409, '议题与正文已绑定，请重新生成完整优化稿 / Topics are source-bound; regenerate the complete draft')
        if not run['published'] or run['calls_limit'] is None or run['config'].get('prompt_version') != engine.PROMPT_VERSION:
            raise HTTPException(409, '需要兼容的已发布稿及已记录预算，请重新优化 / Regenerate a compatible draft with a recorded budget')
        result = parent(job_id)['result']
        if engine.source_hash(result, source_assignment_version=run['config'].get('source_assignment_version', 1)) != run['source_hash']:
            raise HTTPException(409, 'Source changed; regenerate / 来源已变化')
        return run, result

    @app.get(path, response_model=TopicResponse, tags=tags, responses=errors,
             description=bilingual('按需读取已发布议题及原文引用，ETag命中只读版本元数据。', 'Read published source-bound topics on demand; ETag matches read metadata only.'), summary='读取全片议题 / Read recording topics', operation_id='getOptimizationTopics')
    def read(job_id: str, request: Request, response: Response, run_id: str = Query(..., pattern=r'^[a-f0-9]{32}$'), include_outline: bool = False, outline_version: int = Query(1, ge=1, le=2), _: None = auth):
        run = owned(job_id, run_id)
        etag = '"topics-' + run_id + '-' + str(run['topic_revision']) + '-' + str(run['summary_revision']) + '-' + str(include_outline) + '-' + str(outline_version) + '"'
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers={'ETag': etag})
        response.headers.update({'ETag': etag, 'Cache-Control': 'no-store'})
        value = topics.read(run_id)
        if not run['published']:
            value['result'] = None
        if include_outline and value['result']:
            from .reading_outline import build
            from . import optimization_summaries as insights
            raw = parent(job_id)['result']
            titles = insights.read(run_id)
            if outline_version == 2 and run['config'].get('reading_plan_ready'):
                from .reading_plan import outline
                value['outline'] = outline(raw, value['result'], store.snapshot(run_id)[1], titles.get('blocks', {}))
            else:
                value['outline'] = build(raw, value['result'], titles['scenes'])
        return value

    @app.post(path + '/estimate', response_model=CallEstimate, tags=tags, responses=errors,
              description=bilingual('本地预估额外调用，不请求模型。', 'Estimate extra calls locally without model requests.'), summary='预估议题调用预算 / Estimate topic calls', operation_id='estimateOptimizationTopics')
    def preflight(job_id: str, body: TopicRequest, _: None = auth):
        run, result = eligible(job_id, body)
        return estimate(run, result, regenerate=body.regenerate, limit=body.max_llm_calls)

    @app.post(path, response_model=OptimizationStatus, status_code=202, tags=tags, responses=errors,
              summary='补生成或重新识别议题 / Discover or regenerate topics', operation_id='createOptimizationTopics',
              description=bilingual('要求Idempotency-Key；首次202、重放或无工作200、冲突409、准入429。保留原快照与总调用预算。', 'Idempotency-Key required. 202 accepted, 200 replay/no work, 409 conflict, 429 admission. Original snapshot and total call allowance are retained.'))
    async def create(job_id: str, body: TopicRequest, response: Response, idempotency_key: str = Header(..., alias='Idempotency-Key'), _: None = auth):
        key = store.digest(validate_idempotency_key(idempotency_key))
        fingerprint = store.digest(body.model_dump(exclude={'estimate_fingerprint'}))
        async with manager.gate:
            with db.connect() as c:
                receipt = c.execute('SELECT fingerprint FROM optimization_topic_retries WHERE job_id=? AND key_hash=?', (job_id, key)).fetchone()
            if receipt:
                if receipt[0] != fingerprint: raise HTTPException(409, 'Idempotency key conflict')
                response.status_code = 200
                return public_status(job_id)
            if not store.settings().get('enabled'): raise HTTPException(409, 'Optimization is disabled')
            run, result = await asyncio.to_thread(eligible, job_id, body)
            candidate = store.status(job_id)['candidate']
            if candidate and candidate['state'] in ('queued','running','pausing'):
                raise HTTPException(409, 'Wait for the new draft / 请等待新稿完成')
            await manager.check_model(run['config'])
            if run['summary_state'] in ('queued', 'running'):
                raise HTTPException(409, 'Wait for the active summary stage / 请等待小结处理完成')
            if run['topic_state'] in ('queued', 'running'):
                raise HTTPException(409, 'Topic discovery is already active')
            def enqueue():
                with reader_lease(job_id), speaker_update_lock(job_id), db.connect() as c:
                    fresh = parent(job_id)['result']
                    if engine.source_hash(fresh, source_assignment_version=run['config'].get('source_assignment_version', 1)) != run['source_hash']:
                        raise HTTPException(409, 'Source changed; regenerate')
                    c.execute('BEGIN IMMEDIATE')
                    if c.execute("SELECT count(*) FROM transcript_optimizations WHERE state='queued' OR summary_state='queued' OR topic_state='queued'").fetchone()[0] >= 5:
                        raise HTTPException(429, 'Optimization queue full', headers={'Retry-After': '5'})
                    if shutil.disk_usage(db.settings.data_dir).free < manager.config.min_free_disk_bytes:
                        raise HTTPException(429, 'Insufficient disk space', headers={'Retry-After': '30'})
                    value = estimate(run, result, regenerate=body.regenerate, limit=body.max_llm_calls)
                    if body.estimate_fingerprint and body.estimate_fingerprint != value['fingerprint']:
                        raise HTTPException(409, 'Topic estimate changed; refresh')
                    calls.admit(value)
                    calls.raise_limit(c, body.run_id, body.max_llm_calls)
                    if run['topic_state'] == 'not_requested':
                        topics.initialize(c, body.run_id, result, run['config'])
                    elif body.regenerate:
                        topics.initialize(c, body.run_id, result, run['config'], reset=True)
                    if run['topic_state'] != 'completed' or body.regenerate:
                        c.execute("UPDATE transcript_optimizations SET topic_state='queued',topic_revision=topic_revision+1 WHERE id=?", (body.run_id,))
                    else:
                        response.status_code = 200
                    c.execute('INSERT INTO optimization_topic_retries VALUES(?,?,?,?)', (job_id, key, fingerprint, body.run_id))
                    c.commit()
            await asyncio.to_thread(enqueue)
            manager.wake.set()
            return public_status(job_id)

    @app.post(path + '/pause', response_model=OptimizationStatus, tags=tags, responses=errors,
              description=bilingual('保留节点检查点；未发布稿请暂停整个流程。', 'Preserve checkpoints; pause the whole workflow for unpublished drafts.'), summary='暂停议题补生成 / Pause auxiliary topics', operation_id='pauseOptimizationTopics')
    async def pause(job_id: str, body: TopicRequest, _: None = auth):
        async with manager.gate:
            run, _ = eligible(job_id, body)
            topics.pause(run['id'])
            if run['topic_state'] == 'running' and manager.current_id == run['id'] and manager.current:
                manager.current.cancel()
                await asyncio.gather(manager.current, return_exceptions=True)
            return public_status(job_id)

    # Resume is the same idempotent enqueue contract; no hidden retry loop.
    app.post(path + '/resume', response_model=OptimizationStatus, status_code=202, tags=tags,
             responses=errors, description=bilingual('使用原快照继续，要求Idempotency-Key。', 'Resume using original snapshots; Idempotency-Key required.'), summary='继续议题补生成 / Resume auxiliary topics', operation_id='resumeOptimizationTopics')(create)
