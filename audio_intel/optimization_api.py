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

from . import db, optimization_engine as engine, optimization_store as store, optimization_summaries as insights
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
    context_length: int | None = Field(None, strict=True, ge=engine.MINIMUM_CONTEXT, description='请求上下文窗口（tokens），覆盖 Ollama 默认值；新连接默认16K / Requested context in tokens; overrides the Ollama default. New connections default to 16K.')


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
    observed_context_length: int | None = Field(None, description='最近验证时观测的窗口，非实时值 / Context observed at verified_at, not a live reading')
    verified_at: str | None = None
    model_digest: str | None = None
    prompt_version: int | None = None


class SummaryCounts(BaseModel):
    total: int = Field(0, description='本稿中该类型的小结总项数，非分页数 / Total insight items of this kind in this run, not a paginated count')
    processed: int = Field(0, description='已结束项数，等于 ready + skipped + failed / Finished items: ready + skipped + failed')
    ready: int = Field(0, description='有可用输出，包含关键词和原文要点；不表示事实认证 / Usable output including keywords and source excerpts; not factual certification')
    skipped: int = Field(0, description='无实质内容而跳过的项；v2 的目录、介绍、引用和过程仍计入有效内容 / Items skipped for no substantive content; v2 keeps outlines, introductions, references and procedures eligible')
    failed: int = Field(0, description='未能交付输出的项，对应 unavailable / Items without deliverable output, in unavailable state')
    model_ready: int = Field(0, description='首次模型生成即被接受的项；历史未知方式不计入 / Items accepted on initial model generation; excludes historically unknown methods')
    repaired_ready: int = Field(0, description='异常定向修复后被接受的项，与 model_ready 分开统计 / Items accepted after targeted repair, counted separately from model_ready')
    keyword_ready: int = Field(0, description='采用模型摘取的原文关键词的项 / Items delivered using model-extracted literal source keywords')
    extractive_ready: int = Field(0, description='采用程序摘取的短句或短语的项 / Items delivered using rule-selected excerpts or phrases')
    fallback_ready: int = Field(0, description='兜底交付项，等于 keyword_ready + extractive_ready；已包含在 ready 内 / Fallback items: keyword_ready + extractive_ready; already included in ready')


class SummaryStatus(BaseModel):
    version: int | None = Field(None, description='小结策略版本，独立于正文 prompt_version 和 SQLite 版本；旧稿可为 null / Insight policy version, independent of prose prompt_version and SQLite schema; null for legacy drafts')
    state: Literal['not_requested', 'pending', 'queued', 'running', 'paused', 'partial', 'completed'] = Field(description='小结阶段状态；completed 表示本轮结束，实际交付情况见 counts；not_requested 用于无小结的旧稿 / Insight-stage state; completed ends this round, counts describe delivery; not_requested identifies legacy drafts without insights')
    revision: int = 0
    counts: dict[Literal['group', 'scene'], SummaryCounts] = Field(default_factory=dict, description='分别统计分组小结与画面标题；有效交付率为 ready/(total-skipped)，分母为0时不适用 / Separate group and scene counts; delivery rate is ready/(total-skipped), not applicable for a zero denominator')


class InsightMetadata(BaseModel):
    generation_method: Literal['model', 'model_repair', 'model_keywords', 'source_excerpt'] | None = Field(None, description='本小结的交付方式：首次模型、定向修复、模型关键词、程序摘取；历史未知可为 null。不同于正文同名字段 / Insight delivery method: initial model, targeted repair, model keywords or rule-selected excerpt; null when historically unknown. Distinct from the prose field with the same name')
    attempt_count: int = Field(0, description='已结束的模型请求尝试数，包含连接失败；不计程序摘取。长画面累计各归纳节点，可能超过3；旧稿默认0不代表曾记录调用 / Finished model request attempts including connection failures, excluding local extraction. Long scenes sum reduction nodes and may exceed 3; legacy default 0 is not evidence of recorded calls')
    quality_notice: Literal['keywords', 'source_excerpt'] | None = Field(None, description='展示用降级标记：keywords=关键词，source_excerpt=原文要点；非质量评分 / Display notice for keywords or source excerpts; not a quality score')
    skip_reason: Literal['no_substantive_content'] | None = Field(None, description='仅无实质内容时为 no_substantive_content；其余为 null / no_substantive_content only for genuinely empty/non-substantive input; otherwise null')


class GroupSummary(InsightMetadata):
    state: Literal['pending', 'running', 'ready', 'not_applicable', 'unavailable'] = Field(description='ready 包含有用的关键词／原文要点；not_applicable 无实质内容；unavailable 未交付 / ready includes usable keywords/excerpts; not_applicable means no substantive content; unavailable means no delivery')
    sentences: list[str] = Field(default_factory=list, description='最多两句小结，每句中文50字或英文30词；关键词或原文要点也在此返回，混合语种按比例预算 / At most two summary sentences, each within 50 Chinese characters or 30 words; also carries keywords/excerpts, with proportional mixed-script budgets')
    error: str | None = None


class SceneSummary(InsightMetadata):
    state: Literal['pending', 'running', 'ready', 'not_applicable', 'unavailable'] = Field(description='ready 包含有用的关键词／归纳要点；长画面可从分片归纳结果摘取 / ready includes usable keywords/excerpts; long-scene excerpts may come from reduced notes')
    title: str | None = Field(None, description='画面标题，中文20字或英文10词；降级短语同样受限 / Scene title within 20 Chinese characters or 10 words; fallback phrases share the limit')
    error: str | None = None


class SummariesResponse(BaseModel):
    run_id: str
    summary_status: SummaryStatus
    groups: dict[str, GroupSummary] = Field(description='以分组 ordinal 的十进制字符串为键 / Indexed by the decimal string of each group ordinal')
    scenes: dict[str, SceneSummary] = Field(description='以不可变来源快照的画面 ID 为键 / Indexed by scene IDs from the immutable source snapshot')


class OptimizationSummary(BaseModel):
    summary_status: SummaryStatus | None = None
    id: str
    job_id: str
    state: Literal['queued', 'running', 'pausing', 'paused', 'failed', 'succeeded', 'deleting']
    published: bool
    phase: Literal['generation', 'review', 'summaries', 'complete'] = 'generation'
    reviewed_parts: int = 0
    total_parts: int = 0
    requires_regeneration: bool = False
    model: str
    rewrite_mode: Literal['polish', 'rewrite', 'editorial'] | None = None
    total_chars: int
    completed_chars: int
    completed_units: int
    total_units: int
    needs_review_units: int = 0
    review_state: Literal['queued', 'running', 'paused', 'completed', 'unavailable'] | None = None
    reviewed_units: int = 0
    review_total_units: int = 0
    uncertain_units: int = 0
    unavailable_units: int = 0
    basic_cleanup_units: int = 0
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
    phase: Literal['workflow', 'generation', 'review'] = Field('workflow', description='控制整个流程；旧值为兼容别名 / Controls the whole workflow; legacy values are aliases')


class SourceSpan(BaseModel):
    segment_id: int
    text: str
    start_offset: int
    end_offset: int
    start: float
    end: float


class ReviewIssue(BaseModel):
    source_quote: str
    candidate_quote: str
    reason: str


class ReviewDetails(BaseModel):
    diagnostic: str | None = Field(None, description='复核证据或格式问题的诊断；无法定位引用时保持 uncertain，不补造证据 / Diagnostic for review evidence or format defects; ungrounded citations remain uncertain without fabricated evidence')
    stage: Literal['generation', 'review']
    issues: list[ReviewIssue] = Field(default_factory=list)


class OptimizedParagraph(BaseModel):
    group_summary: GroupSummary | None = None
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
    generation_method: Literal['model', 'basic_cleanup', 'mixed'] | None = None
    review_outcome: Literal['pending', 'pass', 'fact', 'uncertain', 'unavailable'] | None = None
    scene_ids: list[str] = Field(default_factory=list)
    merged_source_count: int = 1
    candidate_text: str | None = None
    review_details: ReviewDetails | None = None
    sources: list[SourceSpan]
    segment_ids: list[int]


class OptimizedResult(BaseModel):
    summary_status: SummaryStatus | None = None
    prompt_version: int = 1
    run_id: str
    complete: bool
    model: str
    rewrite_mode: Literal['polish', 'rewrite', 'editorial'] | None = None
    paragraphs: list[OptimizedParagraph]
    needs_review_units: int = 0
    review_state: Literal['queued', 'running', 'paused', 'completed', 'unavailable'] | None = None
    reviewed_units: int = 0
    review_total_units: int = 0
    uncertain_units: int = 0
    unavailable_units: int = 0
    basic_cleanup_units: int = 0
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
        for name in ('candidate', 'published'):
            item = values[name]
            if item is None:
                continue
            insights.pause(item['id'])
            if not deleting and name == 'published':
                if self.current_id == item['id'] and self.current:
                    self.current.cancel()
                    await asyncio.gather(self.current, return_exceptions=True)
                continue
            if deleting:
                store.transition(item['id'], 'deleting', ('queued', 'running', 'pausing', 'paused', 'failed', 'succeeded'))
            else:
                store.transition(item['id'], 'paused', ('queued', 'running', 'pausing'))
            if self.current_id == item['id'] and self.current:
                self.current.cancel()
                await asyncio.gather(self.current, return_exceptions=True)

    async def check_model(self, profile: dict):
        if profile.get('prompt_version') != engine.PROMPT_VERSION:
            raise ValueError('优化策略已更新，请重新生成 / Optimization policy changed; regenerate')
        models = await asyncio.wait_for(engine.discover(profile), 30)
        if not any(m['name'] == profile['model'] and m['digest'] == profile['model_digest'] for m in models['models']):
            raise ValueError('模型已变化或被删除，请重新验证并生成 / Model changed or was removed; verify and regenerate')

    async def retry_io(self, operation, request_limit=None):
        with engine.request_budget(request_limit):
            for attempt in range(3):
                try:
                    return await operation()
                except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError) as exc:
                    if attempt == 2 or engine.requests_remaining() == 0 or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 and exc.response.status_code != 429):
                        raise
                    await asyncio.sleep(2 ** attempt)

    async def execute(self, run_id: str):
        try:
            run = store.get(run_id)
            profile, hotwords = run['config'], run['hotwords']
            await self.retry_io(lambda: self.check_model(profile))
            job = parent(run['job_id'])
            if engine.source_hash(job['result']) != run['source_hash']:
                raise ValueError('原文已变化，请重新生成 / Source changed; regenerate')
            await engine.ensure_context(profile, load=True)
            units = store.units(run_id)
            for index, unit in enumerate(units):
                if unit['text'] is not None:
                    continue
                chunks = store.parts(run_id, unit['ordinal'])
                for part in chunks:
                    if part['text'] is not None:
                        continue
                    source = part['source']
                    batch = [{**source, 'ordinal': unit['ordinal']}]
                    try:
                        output = await self.retry_io(lambda: engine.generate(profile, batch, hotwords, '', '', lambda count: store.activity(run_id, count)), request_limit=3 if profile.get('request_policy_version') == 2 else None)
                        text = output[0][1]
                        method = getattr(output, 'method', 'model')
                    except (engine.InvalidOutput, engine.ReviewFailed, json.JSONDecodeError):
                        text, method = engine.basic_cleanup(source['original_text']), 'basic_cleanup'
                    if not store.checkpoint_part(run_id, unit['ordinal'], part['part_ordinal'], text, method):
                        raise asyncio.CancelledError()
                # Only forced, non-sentence boundaries need a bounded seam pass.
                chunks = store.parts(run_id, unit['ordinal'])
                for i in range(len(chunks) - 1):
                    if chunks[i]['source']['forced_after'] and not chunks[i]['seam_done']:
                        texts = await self.retry_io(lambda: engine.repair_seam(profile, chunks[i], chunks[i + 1], hotwords))
                        if not store.checkpoint_seam(run_id, unit['ordinal'], i, texts):
                            raise asyncio.CancelledError()
                        chunks = store.parts(run_id, unit['ordinal'])
                if not store.finish_unit(run_id, unit['ordinal']):
                    raise asyncio.CancelledError()
            if engine.source_hash(parent(run['job_id'])['result']) != run['source_hash']:
                raise ValueError('Source changed; regenerate / 原文已变化，请重新生成')
            await self.finish_review(run_id)
            await insights.process(run_id)
            if store.publish(run_id):
                store.clear_export_caches()
        except asyncio.CancelledError:
            store.transition(run_id, 'paused', ('running', 'pausing'), '已暂停 / Paused')
            raise
        except engine.ContextWindowError as exc:
            store.transition(run_id, 'paused', ('running',), error_message(exc))
        except (httpx.TransportError, httpx.HTTPStatusError, TimeoutError) as exc:
            recoverable = not isinstance(exc, httpx.HTTPStatusError) or exc.response.status_code == 429 or exc.response.status_code >= 500
            store.transition(run_id, 'paused' if recoverable else 'failed', ('running',), error_message(exc))
        except Exception as exc:
            store.transition(run_id, 'failed', ('running',), error_message(exc))

    async def finish_review(self, run_id: str):
        if not store.begin_review(run_id):
            raise asyncio.CancelledError()
        run = store.get(run_id)
        failures = run['review_failures']
        unavailable = {'outcome': 'unavailable', 'details': {'stage': 'review', 'issues': []}}
        for unit in store.units(run_id):
            chunks = store.parts(run_id, unit['ordinal'])
            for index, part in enumerate(chunks):
                if part['review_outcome'] is not None:
                    continue
                left = index - 1 if index and chunks[index - 1]['source']['forced_after'] else index
                right = index + 2 if part['source']['forced_after'] else index + 1
                window = chunks[left:right]
                original = ''.join(p['source']['original_text'] for p in window)
                candidate = '\n\n'.join(p['text'] for p in window if p['text'])
                verdict = unavailable
                if failures < 3:
                    try:
                        if run['config'].get('request_policy_version') == 2:
                            verdict = await self.retry_io(lambda: engine.review(run['config'], original, candidate, run['hotwords']), request_limit=2)
                        else:
                            verdict = await engine.review(run['config'], original, candidate, run['hotwords'])
                        failures = store.record_review_connection(run_id, False)
                    except (httpx.HTTPError, TimeoutError):
                        failures = store.record_review_connection(run_id, True)
                    except (engine.InvalidOutput, ValueError):
                        failures = store.record_review_connection(run_id, False)
                if not store.checkpoint_review(run_id, unit['ordinal'], index, verdict):
                    raise asyncio.CancelledError()
        if engine.source_hash(parent(run['job_id'])['result']) != run['source_hash']:
            raise ValueError('Source changed; regenerate / 原文已变化，请重新生成')

    async def run(self):
        self.loop = asyncio.get_running_loop()
        with db.connect() as connection:
            connection.execute("UPDATE transcript_optimizations SET summary_state='paused',summary_revision=summary_revision+1,revision=revision+1,updated_at=? WHERE summary_state IN ('queued','running')", (db.utcnow(),))
            connection.execute("UPDATE transcript_optimizations SET state='paused',error='服务中断 / Service interrupted',revision=revision+1,updated_at=? WHERE state IN ('running','pausing')", (db.utcnow(),))
            interrupted_deletions = [row[0] for row in connection.execute("SELECT DISTINCT job_id FROM transcript_optimizations WHERE state='deleting'")]
        for job_id in interrupted_deletions:
            store.delete(job_id)
        try:
            while True:
                if store.settings().get('enabled') and not self.gate.locked():
                    with db.connect() as connection:
                        generation = connection.execute("SELECT id,state FROM transcript_optimizations WHERE state='queued' OR (published=1 AND summary_state='queued') ORDER BY COALESCE(summary_requested_at,created_at),id LIMIT 1").fetchone()
                    identifier = generation[0] if generation else None
                    auxiliary = generation and generation['state'] == 'succeeded'
                    claimed = identifier and (auxiliary or store.transition(identifier, 'running', ('queued',)))
                    if claimed:
                        self.current_id = identifier
                        self.current = asyncio.create_task(self.execute_summaries(identifier) if auxiliary else self.execute(identifier))
                        await asyncio.gather(self.current, return_exceptions=True)
                        self.current_id, self.current = None, None
                await asyncio.sleep(.05)
        finally:
            if self.current:
                self.current.cancel()
                await asyncio.gather(self.current, return_exceptions=True)

    async def execute_summaries(self, run_id):
        try:
            run = store.get(run_id)
            await self.check_model(run['config'])
            if engine.source_hash(parent(run['job_id'])['result']) != run['source_hash']:
                raise ValueError('Source changed; regenerate')
            await insights.process(run_id)
        except asyncio.CancelledError:
            insights.pause(run_id)
            raise
        except Exception:
            insights.pause(run_id)


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

    @app.get(root, summary='读取优化配置 / Get optimization configuration', description=api.bilingual('读取已保存的 Ollama 连接、模型与启用状态；不返回模型服务密钥。', 'Read the saved Ollama connection and enabled state.'), operation_id='getOptimizationConfiguration', tags=[TAG], response_model=ConfigurationResponse, responses=errors)
    def configuration(_: None = auth):
        return store.settings()

    @app.post(root + '/discover', summary='获取模型 / Discover models', description=api.bilingual('按指定代理策略读取用户配置的 Ollama 模型列表，不发送历史转写。', 'Discover installed Ollama models using the selected proxy policy.'), operation_id='discoverOptimizationModels', tags=[TAG], responses=errors)
    async def discover(body: Connection, _: None = auth):
        try:
            async with probe_gate:
                return await asyncio.wait_for(engine.discover(body.model_dump()), 30)
        except Exception as exc:
            raise HTTPException(422, error_message(exc)) from exc

    @app.post(root + '/verify', summary='验证优化模型 / Verify optimization model', description=api.bilingual('使用合成文本在 330 秒内验证正文生成和实际上下文窗口；辅助检查不可用不阻止连接验证。', 'Run synthetic prose generation within 330 seconds and verify the allocated context window. Auxiliary checking availability does not gate connection verification.'), operation_id='verifyOptimizationModel', tags=[TAG], responses=errors)
    async def verify(body: ModelConnection, _: None = auth):
        try:
            value = body.model_dump(exclude_none=True)
            current = store.settings()
            same_connection = all(current.get(k) == value[k] for k in ('base_url', 'model', 'use_proxy'))
            value.setdefault('context_length', current.get('context_length', engine.DEFAULT_CONTEXT) if same_connection else engine.DEFAULT_CONTEXT)
            async with probe_gate:
                profile = await asyncio.wait_for(engine.verify(value), 330)
            token = uuid.uuid4().hex
            for key in list(probes):
                if probes[key][0] < time.monotonic() or len(probes) >= 16:
                    del probes[key]
            probes[token] = (time.monotonic() + 600, profile)
            return {**profile, 'verification_token': token}
        except Exception as exc:
            raise HTTPException(422, error_message(exc)) from exc

    @app.put(root, summary='保存优化配置 / Save optimization configuration', description=api.bilingual('启用需要匹配且有效的验证凭证或未更改的已验证配置；关闭会暂停未完成工作。', 'Enabling requires a matching verified profile. Disabling pauses pending work.'), operation_id='saveOptimizationConfiguration', tags=[TAG], response_model=ConfigurationResponse, responses=errors)
    async def save(body: SaveConfiguration, _: None = auth):
        async with manager.gate:
            current = store.settings()
            identity = {key: getattr(body, key) for key in ('base_url', 'model', 'use_proxy')}
            probe = probes.get(body.verification_token or '')
            profile = probe[1] if probe and probe[0] > time.monotonic() else current
            same_connection = all(profile.get(k) == v for k, v in identity.items())
            identity['context_length'] = body.context_length if body.context_length is not None else (profile.get('context_length', engine.DEFAULT_CONTEXT) if same_connection else engine.DEFAULT_CONTEXT)
            verified = all(profile.get(k) == v for k, v in identity.items()) and profile.get('verified_at') and profile.get('context_length', 0) >= engine.MINIMUM_CONTEXT
            if body.enabled and not verified:
                raise HTTPException(409, '请先验证所选模型 / Verify the selected model first')
            value = {**(profile if verified else identity), 'enabled': body.enabled}
            store.save_settings(value)
            if not body.enabled:
                for job_id in store.summaries():
                    await manager.stop(job_id)
            return value

    @app.get(jobs, summary='读取优化状态 / Get optimization status', description=api.bilingual('返回已发布稿与候选摘要、当前阶段及已保存检查进度；生成、有限检查及首轮小结尝试结束才发布。支持 ETag 轮询。', 'Return published/candidate summaries, workflow phase and persisted reviewed_parts/total_parts. Published means generation, bounded checks and initial summary attempts are complete. Supports ETag polling.'), operation_id='getOptimizationStatus', tags=[TAG], response_model=OptimizationStatus, responses=errors)
    def status(job_id: str, request: Request, response: Response, _: None = auth):
        parent(job_id)
        value = public_status(job_id)
        etag = '"' + store.digest(value) + '"'
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers={'ETag': etag})
        response.headers['ETag'] = etag
        response.headers['Cache-Control'] = 'no-store'
        return value

    @app.post(jobs, summary='创建优化任务 / Create optimization', description=api.bilingual('要求 Idempotency-Key；档位默认 rewrite 并保存快照。首次 202、重放 200、冲突 409、队列满 429。', 'Requires Idempotency-Key. rewrite_mode selects polish/rewrite/editorial (default rewrite), saved in the run snapshot and idempotency fingerprint. Returns 202 on creation, 200 on replay, 409 on conflict and 429 at queue capacity.'), operation_id='createTranscriptOptimization', tags=[TAG], response_model=OptimizationStatus, status_code=202, responses=errors)
    async def create(job_id: str, body: CreateRequest, response: Response, idempotency_key: str = Header(..., alias='Idempotency-Key'), _: None = auth):
        key = api.validate_idempotency_key(idempotency_key)
        async with manager.gate:
            if store.replay(job_id, key, body.regenerate, body.rewrite_mode if 'rewrite_mode' in body.model_fields_set else None):
                response.status_code = 200
                return public_status(job_id)
            config = {**store.settings(), 'prompt_version': engine.PROMPT_VERSION, 'rewrite_mode': body.rewrite_mode, 'summary_version': insights.VERSION,
                      'request_policy_version': 2 if insights.VERSION == 2 else 1}
            if not config.get('enabled'):
                raise HTTPException(409, '请在系统状态启用表达优化 / Enable expression optimization in System')
            candidate = store.status(job_id)['candidate']
            if body.regenerate and candidate and candidate['requires_regeneration']:
                await manager.stop(job_id)
            def prepare():
                with reader_lease(job_id), speaker_update_lock(job_id):
                    job = parent(job_id)
                    config['summary_scene_ids'] = [s['id'] for s in (job['result'].get('video') or {}).get('scenes', [])]
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

    @app.post(jobs + '/pause', summary='暂停优化 / Pause optimization', description=api.bilingual('暂停整个生成与检查流程，保留已保存正文和检查点；兼容旧 phase 值。', 'Pause the complete optimization workflow, including final checks; preserve committed text and checkpoints. Legacy phase values remain accepted.'), operation_id='pauseTranscriptOptimization', tags=[TAG], response_model=OptimizationStatus, responses=errors)
    async def pause(job_id: str, body: RunRequest, _: None = auth):
        async with manager.gate:
            item = owned(job_id, body.run_id)
            if item['published']:
                raise HTTPException(409, 'Completed draft cannot be paused')
            await manager.stop(job_id)
            return public_status(job_id)

    @app.post(jobs + '/resume', summary='继续优化 / Resume optimization', description=api.bilingual('校验模型和来源后继续整个流程，复用检查点；已发布稿不能恢复，旧策略候选需要重新生成（409）。', 'Resume the complete optimization workflow after model/source verification, preserving generation and check progress. Published results cannot be resumed. Obsolete policy candidates require regeneration (409).'), operation_id='resumeTranscriptOptimization', tags=[TAG], response_model=OptimizationStatus, responses=errors)
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
            if item['published']:
                raise HTTPException(409, 'Completed draft cannot be resumed')
            store.resume(body.run_id)
            return public_status(job_id)

    @app.get(jobs + '/summaries', summary='读取分组与画面小结 / Read group summaries and scene titles', description=api.bilingual('按任务读取小结附加数据，不载入完整正文。v2 包含生成方式、尝试次数、关键词／原文要点标记及分类计数，ready 包括有效兜底。兼容旧稿 not_requested；支持 ETag。', 'Read per-job insights without transcript prose. V2 includes generation method, attempt count, keyword/source-excerpt notices and separate counters; ready includes meaningful fallback. Legacy drafts return not_requested. Supports ETag.'), operation_id='getOptimizationSummaries', tags=[TAG], response_model=SummariesResponse, responses=errors)
    def summaries(job_id: str, request: Request, response: Response, run_id: str = Query(..., pattern=r'^[a-f0-9]{32}$'), _: None = auth):
        owned(job_id, run_id)
        value = insights.read(run_id)
        etag = '"' + store.digest(value) + '"'
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers={'ETag': etag})
        response.headers.update({'ETag': etag, 'Cache-Control': 'no-store'})
        return value

    @app.post(jobs + '/summaries/retry', summary='补试未生成小结 / Retry missing summaries', description=api.bilingual('仅补试已发布稿中未生成的小结，保留正文与成功项。要求 Idempotency-Key；首次入队 202、重放或无工作 200、冲突或旧稿 409、队列或磁盘限制 429。使用原模型、来源和窗口快照。', 'Retry missing insights of a published draft, preserving prose and successful items. Requires Idempotency-Key: 202 queued, 200 replay/no work, 409 conflict/legacy, 429 queue/disk limit. Uses original model, source and context snapshots.'), operation_id='retryOptimizationSummaries', tags=[TAG], response_model=OptimizationStatus, status_code=202, responses=errors)
    async def retry_summaries(job_id: str, body: RunRequest, response: Response, idempotency_key: str = Header(..., alias='Idempotency-Key'), _: None = auth):
        key = api.validate_idempotency_key(idempotency_key)
        async with manager.gate:
            item = owned(job_id, body.run_id)
            if insights.replay(job_id, body.run_id, key):
                response.status_code = 200
                return public_status(job_id)
            if not store.settings().get('enabled'):
                raise HTTPException(409, '表达优化已关闭 / Optimization is disabled')
            if not item['published'] or not item['summary_version']:
                raise HTTPException(409, '请重新优化旧稿 / Regenerate to add summaries to a legacy draft')
            try:
                await manager.check_model(item['config'])
            except Exception as exc:
                raise HTTPException(409, error_message(exc)) from exc
            with reader_lease(job_id), speaker_update_lock(job_id):
                if engine.source_hash(parent(job_id)['result']) != item['source_hash']:
                    raise HTTPException(409, 'Source changed; regenerate')
                queued = insights.retry(body.run_id, key, api.settings.min_free_disk_bytes)
            if not queued:
                response.status_code = 200
            return public_status(job_id)

    @app.post(jobs + '/summaries/pause', summary='暂停小结补试 / Pause summary retry', description=api.bilingual('停止已发布稿的小结补试，保留正文、成功项和中间检查点。未发布稿请使用暂停整个优化流程。', 'Pause auxiliary work for a published draft; preserve prose, successful insights and intermediate checkpoints. Use workflow pause for an unpublished draft.'), operation_id='pauseOptimizationSummaries', tags=[TAG], response_model=OptimizationStatus, responses=errors)
    async def pause_summaries(job_id: str, body: RunRequest, _: None = auth):
        async with manager.gate:
            item = owned(job_id, body.run_id)
            if not item['published']:
                raise HTTPException(409, 'Pause the optimization workflow instead')
            insights.pause(body.run_id)
            if manager.current_id == body.run_id and manager.current:
                manager.current.cancel()
                await asyncio.gather(manager.current, return_exceptions=True)
            return public_status(job_id)

    @app.get(jobs + '/result', summary='读取优化稿 / Read optimized transcript', description=api.bilingual('读取已完成、按画面组织的正文和不可变来源。生成、检查及首轮小结尝试结束才可读和导出，未发布候选返回 409；检查提示不撤销正文。', 'Read completed scene-scoped prose and source spans in one SQLite snapshot. Generation, bounded checks and initial summary attempts must finish before publication; candidates return 409. complete means readable and exportable. Check warnings never retract prose. Video paragraphs belong to one scene. Paragraph generation_method identifies model/basic_cleanup/mixed; nested group_summary and title_summary use their separate insight methods and fallback notices.'), operation_id='getOptimizedTranscript', tags=[TAG], response_model=OptimizedResult, responses=errors)
    def result(job_id: str, run_id: str = Query(..., pattern=r'^[a-f0-9]{32}$'), _: None = auth):
        with reader_lease(job_id), speaker_update_lock(job_id):
            owned(job_id, run_id)
            try:
                return engine.reading_result(parent(job_id)['result'], run_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.delete(jobs, summary='删除优化 / Delete optimization', description=api.bilingual('要求 purge=true；取消相关请求并清理正文、批次、检查记录和导出缓存。', 'Requires purge=true. Cancel generation and delete drafts, checkpoints and export caches.'), operation_id='deleteTranscriptOptimization', tags=[TAG], status_code=204, responses=errors)
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
