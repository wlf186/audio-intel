"""Authenticated configuration, durable drafts and one remote-I/O worker."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal

import httpx
from fastapi import Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import db, optimization_engine as engine, optimization_store as store
from .asr_speakers import speaker_update_lock
from .document_download import reader_lease, deletion_guard

TAG = 'Expression optimization / 表达优化'
_managers: dict[str, 'Manager'] = {}


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(max_length=2048)
    use_proxy: bool = False

    @field_validator('base_url')
    @classmethod
    def url(cls, value):
        return engine.normalize_url(value)


class ModelConnection(Connection):
    model: str = Field(min_length=1, max_length=256)


class SaveConfiguration(ModelConnection):
    enabled: bool = False
    verification_token: str | None = None


class ConfigurationResponse(BaseModel):
    base_url: str
    use_proxy: bool
    model: str
    enabled: bool
    context_length: int | None = None
    model_context_length: int | None = None
    verified_at: str | None = None
    model_digest: str | None = None
    prompt_version: int | None = None


class OptimizationSummary(BaseModel):
    id: str
    job_id: str
    state: Literal['queued', 'running', 'pausing', 'paused', 'failed', 'succeeded', 'deleting']
    published: bool
    requires_regeneration: bool = False
    model: str
    rewrite_mode: Literal['polish', 'rewrite', 'editorial'] | None = None
    total_chars: int
    completed_chars: int
    completed_units: int
    total_units: int
    needs_review_units: int = 0
    activity_chars: int
    progress: float
    revision: int
    error: str | None
    created_at: str
    updated_at: str


class OptimizationStatus(BaseModel):
    enabled: bool
    published: OptimizationSummary | None
    candidate: OptimizationSummary | None


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    regenerate: bool = False
    rewrite_mode: Literal['polish', 'rewrite', 'editorial'] = Field('rewrite', description='自然润色 / polish; 流畅重写 / rewrite (default); 深度成稿 / editorial. Saved per run; resume retains it.')


class RunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    run_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class SourceSpan(BaseModel):
    segment_id: int
    text: str
    start_offset: int
    end_offset: int
    start: float
    end: float


class OptimizedParagraph(BaseModel):
    id: str
    ordinal: int
    speaker: str | None
    speaker_label: str | None
    start: float
    end: float
    original_text: str
    text: str | None
    omitted: bool = False
    review_status: Literal['accepted', 'needs_review'] | None = None
    review_reason: Literal['review_failed', 'invalid_output'] | None = None
    sources: list[SourceSpan]
    segment_ids: list[int]


class OptimizedResult(BaseModel):
    run_id: str
    complete: bool
    model: str
    rewrite_mode: Literal['polish', 'rewrite', 'editorial'] | None = None
    paragraphs: list[OptimizedParagraph]
    needs_review_units: int = 0
    scenes: list[dict[str, Any]]


def parent(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'Task not found / 任务不存在')
    if job['kind'] != 'asr' or job['state'] != 'succeeded' or (job.get('request') or {}).get('purpose') not in (None, 'video_transcription'):
        raise HTTPException(409, '需要已完成的普通音视频转写 / A completed audio/video transcription is required')
    return job


def public_status(job_id: str) -> dict:
    return {**store.status(job_id), 'enabled': bool(store.settings().get('enabled'))}


def owned(job_id: str, run_id: str) -> dict:
    item = store.get(run_id)
    if item['job_id'] != job_id:
        raise HTTPException(404, 'Optimization not found')
    return item


def error_message(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return '模型响应超时，请检查服务后继续 / Model response timed out; check the service and resume'
    if isinstance(exc, httpx.HTTPStatusError):
        return f'Ollama HTTP {exc.response.status_code}，请检查服务和模型 / Check the service and model'
    if isinstance(exc, httpx.RequestError):
        return '无法连接 Ollama，请检查服务地址和代理 / Cannot connect to Ollama; check URL and proxy'
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:500]
    return str(exc)[:500] or 'Optimization failed / 优化失败'


class Manager:
    def __init__(self, config):
        self.config = config
        self.current: asyncio.Task | None = None
        self.current_id: str | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.gate = asyncio.Lock()

    async def stop(self, job_id: str, deleting: bool = False):
        values = store.status(job_id)
        for item in (values['candidate'], values['published'] if deleting else None):
            if item is None:
                continue
            store.transition(item['id'], 'deleting' if deleting else 'pausing', ('queued', 'running', 'pausing', 'paused', 'failed', 'succeeded') if deleting else ('queued', 'running', 'pausing'))
            if self.current_id == item['id'] and self.current:
                self.current.cancel()
                await asyncio.gather(self.current, return_exceptions=True)
            if not deleting:
                store.transition(item['id'], 'paused', ('pausing',))

    async def check_model(self, profile: dict):
        if profile.get('prompt_version') != engine.PROMPT_VERSION:
            raise ValueError('优化策略已更新，请重新生成 / Optimization policy changed; regenerate')
        models = await asyncio.wait_for(engine.discover(profile), 30)
        if not any(m['name'] == profile['model'] and m['digest'] == profile['model_digest'] for m in models['models']):
            raise ValueError('模型已变化或被删除，请重新验证并生成 / Model changed or was removed; verify and regenerate')

    async def execute(self, run_id: str):
        try:
            run = store.get(run_id)
            profile = run['config']
            for attempt in range(3):
                try:
                    await self.check_model(profile)
                    break
                except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError) as exc:
                    if attempt == 2 or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 and exc.response.status_code != 429):
                        raise
                    await asyncio.sleep(2 ** attempt)
            job = parent(run['job_id'])
            if engine.source_hash(job['result']) != run['source_hash']:
                raise ValueError('原文已变化，请重新生成 / Source changed; regenerate')
            units = store.units(run_id)
            hotwords = run['hotwords']

            async def process(batch, before, after):
                reason = None
                for attempt in range(3):
                    try:
                        output = await engine.generate(profile, batch, hotwords, before, after, lambda count: store.activity(run_id, count))
                        if not store.checkpoint(run_id, output):
                            raise asyncio.CancelledError()
                        return
                    except engine.ReviewFailed:
                        reason = 'review_failed'
                        break
                    except (engine.InvalidOutput, json.JSONDecodeError):
                        if attempt == 2:
                            reason = 'invalid_output'
                            break
                    except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError) as exc:
                        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 and exc.response.status_code != 429:
                            raise
                        if attempt == 2:
                            raise
                    await asyncio.sleep(2 ** attempt)
                if not store.checkpoint(run_id, [(batch[0]['ordinal'], batch[0]['original_text'])], review_reason=reason):
                    raise asyncio.CancelledError()

            from .optimization_policy import context
            for index, unit in enumerate(units):
                if unit['text'] is not None:
                    continue
                await process([unit], context(units[:index], before=True), context(units[index + 1:]))
            if engine.source_hash(parent(run['job_id'])['result']) != run['source_hash']:
                raise ValueError('Source changed; regenerate / 原文已变化，请重新生成')
            if store.publish(run_id):
                store.clear_export_caches()
        except asyncio.CancelledError:
            store.transition(run_id, 'paused', ('running', 'pausing'), '已暂停 / Paused')
            raise
        except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError) as exc:
            recoverable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code == 429 or exc.response.status_code >= 500
            store.transition(run_id, 'paused' if recoverable else 'failed', ('running',), error_message(exc))
        except Exception as exc:
            store.transition(run_id, 'failed', ('running',), error_message(exc))

    async def run(self):
        self.loop = asyncio.get_running_loop()
        with db.connect() as connection:
            connection.execute("UPDATE transcript_optimizations SET state='paused',error='服务中断 / Service interrupted',revision=revision+1,updated_at=? WHERE state IN ('running','pausing')", (db.utcnow(),))
            interrupted_deletions = [r[0] for r in connection.execute("SELECT DISTINCT job_id FROM transcript_optimizations WHERE state='deleting'")]
        for job_id in interrupted_deletions:
            store.delete(job_id)
        try:
            while True:
                if store.settings().get('enabled'):
                    with db.connect() as connection:
                        row = connection.execute("SELECT id FROM transcript_optimizations WHERE state='queued' ORDER BY created_at,id LIMIT 1").fetchone()
                    if row and store.transition(row['id'], 'running', ('queued',)):
                        self.current_id = row['id']
                        self.current = asyncio.create_task(self.execute(row['id']))
                        await asyncio.gather(self.current, return_exceptions=True)
                        self.current_id, self.current = None, None
                await asyncio.sleep(.25)
        finally:
            if self.current:
                self.current.cancel()
                await asyncio.gather(self.current, return_exceptions=True)


def stop_for_purge(job_id: str):
    manager = _managers.get(str(db.settings.database_path))
    if manager and manager.loop:
        future = asyncio.run_coroutine_threadsafe(manager.stop(job_id, deleting=True), manager.loop)
        future.result(timeout=10)
    store.delete(job_id)


def register(app, api):
    manager = Manager(api.settings)
    app.state.optimization_manager = manager
    probes: dict[str, tuple[float, dict]] = {}
    probe_gate = asyncio.Semaphore(1)
    auth = Depends(api.require_api_key)
    root = '/api/v1/settings/transcript-optimization'
    jobs = '/api/v1/jobs/{job_id}/transcript-optimization'
    errors = {**api.AUTH_RESPONSES, **api.CONFLICT_RESPONSE, **api.VALIDATION_RESPONSE, **api.NOT_FOUND_RESPONSE,
              429: {'description': 'Queue/resource limit; honor Retry-After / 队列或资源已满'}}
    prior = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with prior(application):
            key = str(api.settings.database_path)
            _managers[key] = manager
            task = asyncio.create_task(manager.run())
            try:
                yield
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                _managers.pop(key, None)
    app.router.lifespan_context = lifespan

    @app.get(root, summary='读取优化配置 / Get optimization configuration', description=api.bilingual('读取优化配置', 'Read the saved Ollama connection and enabled state.'), operation_id='getOptimizationConfiguration', tags=[TAG], response_model=ConfigurationResponse, responses=errors)
    def configuration(_: None = auth):
        return store.settings()

    @app.post(root + '/discover', summary='获取模型 / Discover models', description=api.bilingual('获取模型', 'Discover installed Ollama models using the selected proxy policy.'), operation_id='discoverOptimizationModels', tags=[TAG], responses=errors)
    async def discover(body: Connection, _: None = auth):
        try:
            async with probe_gate:
                return await asyncio.wait_for(engine.discover(body.model_dump()), 30)
        except Exception as exc:
            raise HTTPException(422, error_message(exc)) from exc

    @app.post(root + '/verify', summary='验证优化模型 / Verify optimization model', description=api.bilingual('验证优化模型', 'Run synthetic structured generation and verify the allocated context window.'), operation_id='verifyOptimizationModel', tags=[TAG], responses=errors)
    async def verify(body: ModelConnection, _: None = auth):
        try:
            async with probe_gate:
                profile = await asyncio.wait_for(engine.verify(body.model_dump()), 330)
            token = uuid.uuid4().hex
            for key in list(probes):
                if probes[key][0] < time.monotonic() or len(probes) >= 16:
                    del probes[key]
            probes[token] = (time.monotonic() + 600, profile)
            return {**profile, 'verification_token': token}
        except Exception as exc:
            raise HTTPException(422, error_message(exc)) from exc

    @app.put(root, summary='保存优化配置 / Save optimization configuration', description=api.bilingual('保存优化配置', 'Enabling requires a matching verified profile. Disabling pauses pending work.'), operation_id='saveOptimizationConfiguration', tags=[TAG], response_model=ConfigurationResponse, responses=errors)
    async def save(body: SaveConfiguration, _: None = auth):
        async with manager.gate:
            current = store.settings()
            identity = {key: getattr(body, key) for key in ('base_url', 'model', 'use_proxy')}
            probe = probes.get(body.verification_token or '')
            profile = probe[1] if probe and probe[0] > time.monotonic() else current
            verified = all(profile.get(k) == v for k, v in identity.items()) and profile.get('verified_at') and profile.get('context_length', 0) >= 16384
            if body.enabled and not verified:
                raise HTTPException(409, '请先验证所选模型 / Verify the selected model first')
            value = {**(profile if verified else identity), 'enabled': body.enabled}
            store.save_settings(value)
            if not body.enabled:
                for job_id in store.summaries():
                    await manager.stop(job_id)
            return value

    @app.get(jobs, summary='读取优化状态 / Get optimization status', description=api.bilingual('读取优化状态', 'Return published/candidate summaries including needs_review_units. Supports ETag polling.'), operation_id='getOptimizationStatus', tags=[TAG], response_model=OptimizationStatus, responses=errors)
    def status(job_id: str, request: Request, response: Response, _: None = auth):
        parent(job_id)
        value = public_status(job_id)
        etag = '"' + store.digest(value) + '"'
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers={'ETag': etag})
        response.headers['ETag'] = etag
        response.headers['Cache-Control'] = 'no-store'
        return value

    @app.post(jobs, summary='创建优化任务 / Create optimization', description=api.bilingual('创建优化任务', 'Requires Idempotency-Key. rewrite_mode selects polish/rewrite/editorial (default rewrite), saved in the run snapshot and idempotency fingerprint. Returns 202 on creation, 200 on replay, 409 on conflict and 429 at queue capacity.'), operation_id='createTranscriptOptimization', tags=[TAG], response_model=OptimizationStatus, status_code=202, responses=errors)
    async def create(job_id: str, body: CreateRequest, response: Response, idempotency_key: str = Header(..., alias='Idempotency-Key'), _: None = auth):
        key = api.validate_idempotency_key(idempotency_key)
        async with manager.gate:
            if store.replay(job_id, key, body.regenerate, body.rewrite_mode if 'rewrite_mode' in body.model_fields_set else None):
                response.status_code = 200
                return public_status(job_id)
            config = {**store.settings(), 'prompt_version': engine.PROMPT_VERSION, 'rewrite_mode': body.rewrite_mode}
            if not config.get('enabled'):
                raise HTTPException(409, '请在系统状态启用表达优化 / Enable expression optimization in System')
            candidate = store.status(job_id)['candidate']
            if body.regenerate and candidate and candidate['requires_regeneration']:
                await manager.stop(job_id)
            def prepare():
                with reader_lease(job_id), speaker_update_lock(job_id):
                    job = parent(job_id)
                    units = engine.prepare_units(job['result'])
                    hotwords = list(dict.fromkeys(term for item in job['request'].get('hotword_lists', []) for term in item.get('terms', [])))
                    engine.validate_budget(config, units, hotwords)
                    return store.create(job_id, key, body.regenerate, config, engine.source_hash(job['result']), hotwords, units, api.settings.min_free_disk_bytes)
            try:
                _, replayed = await asyncio.to_thread(prepare)
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(409, str(exc)) from exc
            if replayed:
                response.status_code = 200
            return public_status(job_id)

    @app.post(jobs + '/pause', summary='暂停优化 / Pause optimization', description=api.bilingual('暂停优化', 'Cancel the current generation and preserve committed checkpoints.'), operation_id='pauseTranscriptOptimization', tags=[TAG], response_model=OptimizationStatus, responses=errors)
    async def pause(job_id: str, body: RunRequest, _: None = auth):
        async with manager.gate:
            item = owned(job_id, body.run_id)
            if item['published']:
                raise HTTPException(409, 'Completed draft cannot be paused')
            await manager.stop(job_id)
            return public_status(job_id)

    @app.post(jobs + '/resume', summary='继续优化 / Resume optimization', description=api.bilingual('继续优化', 'Resume from saved checkpoints including retained originals after model and source verification. Obsolete policy candidates require explicit regeneration (409).'), operation_id='resumeTranscriptOptimization', tags=[TAG], response_model=OptimizationStatus, responses=errors)
    async def resume(job_id: str, body: RunRequest, _: None = auth):
        async with manager.gate:
            if not store.settings().get('enabled'):
                raise HTTPException(409, '表达优化已关闭 / Optimization is disabled')
            item = owned(job_id, body.run_id)
            try:
                await manager.check_model(item['config'])
            except Exception as exc:
                raise HTTPException(409, error_message(exc)) from exc
            if engine.source_hash(parent(job_id)['result']) != item['source_hash']:
                raise HTTPException(409, 'Source changed; regenerate')
            store.resume(body.run_id)
            return public_status(job_id)

    @app.get(jobs + '/result', summary='读取优化稿 / Read optimized transcript', description=api.bilingual('读取优化稿', 'Read source-mapped paragraphs and current picture grouping. text=null is pending; omitted=true with text="" is a reviewed filler omission. review_status=needs_review retains the exact original after bounded review/output failures; accepted means current-policy checks passed, not a quality guarantee. Null review_status is pending or unknown historical metadata. needs_review_units counts retained paragraphs, which do not prevent completion. Historical drafts remain readable. Partial results are explicitly marked.'), operation_id='getOptimizedTranscript', tags=[TAG], response_model=OptimizedResult, responses=errors)
    def result(job_id: str, run_id: str = Query(..., pattern=r'^[a-f0-9]{32}$'), _: None = auth):
        with reader_lease(job_id), speaker_update_lock(job_id):
            owned(job_id, run_id)
            try:
                return engine.reading_result(parent(job_id)['result'], run_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.delete(jobs, summary='删除优化 / Delete optimization', description=api.bilingual('删除优化', 'Requires purge=true. Cancel generation and delete drafts, checkpoints and export caches.'), operation_id='deleteTranscriptOptimization', tags=[TAG], status_code=204, responses=errors)
    async def delete(job_id: str, purge: bool = Query(False), _: None = auth):
        if not purge:
            raise HTTPException(409, 'Set purge=true / 请确认删除优化数据')
        async with manager.gate:
            parent(job_id)
            with deletion_guard(job_id) as allowed:
                if not allowed:
                    raise HTTPException(409, '正在读取任务，请稍后重试 / Task is being read; retry shortly')
                await manager.stop(job_id, deleting=True)
                await asyncio.to_thread(store.delete, job_id)
        return Response(status_code=204)
