"""Authenticated presentation uploads, editing and concise PDF downloads."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import logging
import threading
import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from . import db, video_uploads as store
from .api_models import JobResponse, JobResultResponse, VideoSceneResponse
from .asr_speakers import speaker_update_lock
from .document_download import reader_lease
from .transcript import scene_transcripts
from .video_detection import SceneDetectionSettings


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class VideoUploadRequest(StrictModel):
    name: str = Field(min_length=1, max_length=255, description='原始视频文件名 / Original video filename')
    size_bytes: int = Field(gt=0, description='文件总字节数 / Total file bytes')


class UploadChunk(BaseModel):
    offset: int = Field(description='分片起始字节偏移 / Starting byte offset')
    size_bytes: int = Field(description='已确认的分片字节数 / Confirmed chunk bytes')
    sha256: str = Field(description='64 位十六进制 SHA-256 / 64 hexadecimal SHA-256 characters')


class VideoUploadResponse(BaseModel):
    id: str
    name: str
    size_bytes: int
    offset: int
    expires_at: float = Field(description='到期时间，Unix 秒 / Expiry as Unix seconds')
    job_id: str | None
    chunk_bytes: int
    chunks: list[UploadChunk]


class SceneParagraph(BaseModel):
    speaker: str
    speaker_label: str | None
    start: float
    end: float
    text: str
    segment_ids: list[int]


class ReadingScene(VideoSceneResponse):
    paragraphs: list[SceneParagraph]


class VideoScenesResponse(BaseModel):
    revision: int
    scenes: list[ReadingScene]


class VideoRegion(StrictModel):
    x: float = Field(ge=0, lt=1, description='左边界，占画面宽度的比例 / Left edge as a fraction of picture width')
    y: float = Field(ge=0, lt=1, description='上边界，占画面高度的比例 / Top edge as a fraction of picture height')
    width: float = Field(gt=0, le=1, description='区域宽度比例，x + width ≤ 1 / Width fraction; x + width ≤ 1')
    height: float = Field(gt=0, le=1, description='区域高度比例，y + height ≤ 1 / Height fraction; y + height ≤ 1')

    @model_validator(mode='after')
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError('Region must be inside the picture')
        return self


class VideoJobRequest(StrictModel):
    upload_id: str = Field(pattern=r'^[a-f0-9]{32}$', description='上传创建接口返回的 ID / ID returned by upload creation', json_schema_extra={'example': '0123456789abcdef0123456789abcdef'})
    model: str = 'qwen3-asr-0.6b'
    language: str = 'Auto'
    speaker_count: int | None = Field(None, ge=1, le=15)
    diarize: bool = True
    align: bool = True
    context: str = Field('', max_length=8000)
    hotword_list_ids: list[str] = Field(default_factory=list, max_length=8)
    compute_device: Literal['cpu', 'gpu'] | None = None
    use_voiceprint_library: bool = True
    voiceprint_group_ids: list[str] | None = Field(None, description="所选声纹分组；省略全部，提交时固定人员范围 / Selected voiceprint groups; omission selects all, eligible people are snapshotted")
    accelerate_single_task: bool = True
    audio_enhancement: Literal['auto', 'off'] | None = Field(None, description='推荐音频增强总开关：auto 自动降噪并提升轻声，off 关闭两项；不能与独立参数混用 / Recommended audio enhancement control: auto enables denoising and quiet-speech gain, off disables both; cannot mix with independent controls')
    speech_enhancement: bool | None = Field(None, description='兼容独立增益参数；默认开启，不能与总开关混用 / Compatible independent gain control; defaults to true, cannot mix with the combined control', json_schema_extra={'default': True})
    background_denoise: bool | None = Field(None, description='兼容独立降噪参数；默认开启，不能与总开关混用 / Compatible independent denoising control; defaults to true, cannot mix with the combined control', json_schema_extra={'default': True})
    speech_enhancement_strength: float | None = Field(None, ge=0, le=2, allow_inf_nan=False, description="自动人声增益力度，默认 1；0 跳过 / Automatic gain strength, default 1; zero skips")
    background_denoise_strength: float | None = Field(None, ge=0, le=1, allow_inf_nan=False, description="降噪混合比例，GTCRN 默认 0.5；0 跳过 / Denoising mix, GTCRN default 0.5; zero skips")
    video_roi: VideoRegion | None = None
    video_scene_detection: SceneDetectionSettings | None = Field(None, description='画面检测参数；省略使用推荐值 / Picture detection settings; omission uses recommended defaults')


from .transcript_export import TranscriptExportRequest


class SceneEdit(StrictModel):
    revision: int = Field(ge=1, description='开始编辑时读取的修订号；不能用后台新修订替换 / Revision read when editing began; never substitute a newer background revision')
    action: Literal['merge_next', 'split', 'boundary', 'frame', 'restore', 'apply_detection', 'set_export_visibility']
    export_hidden_scene_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(None, max_length=2000, description='完整的导出隐藏画面 ID 列表；空列表恢复全部 / Complete export-hidden picture IDs; empty restores all')
    preview_id: str | None = Field(None, pattern=r'^[a-f0-9]{32}$')
    scene_id: str | None = None
    time: float | None = Field(None, ge=0, description='视频起点后的秒数；拆分/边界严格位于区间内，截图允许左端点 / Seconds from video origin; split/boundary are strictly inside the interval, frame may use its left endpoint')


    @model_validator(mode='after')
    def detection_preview(self):
        if self.action == 'apply_detection' and not self.preview_id:
            raise ValueError('apply_detection requires preview_id')
        if self.action == 'set_export_visibility':
            if self.export_hidden_scene_ids is None:
                raise ValueError('set_export_visibility requires export_hidden_scene_ids')
            if len(set(self.export_hidden_scene_ids)) != len(self.export_hidden_scene_ids):
                raise ValueError('Duplicate picture IDs')
        elif self.export_hidden_scene_ids is not None:
            raise ValueError('export_hidden_scene_ids requires set_export_visibility')
        return self


def capabilities(config: Any) -> dict[str, Any]:
    from .video_detection import capability
    return {'audio_formats': store.AUDIO_FORMATS, 'concise_pdf': True,
            'video': {'supported': True, 'formats': store.VIDEO_FORMATS,
                      'max_upload_bytes': config.max_video_upload_bytes,
                      'chunk_bytes': config.video_upload_chunk_bytes,
                      'upload_idle_seconds': config.video_upload_idle_seconds,
                      'upload_lifetime_seconds': config.video_upload_lifetime_seconds,
                      'max_scenes': 2000, 'scene_editing': True, 'scene_detection': capability()}}


def register(app: Any, api: Any) -> None:
    config = api.settings
    from .video_previews import PreviewManager, register_previews
    previews = PreviewManager(config)
    register_previews(app, api, previews)
    auth = Depends(api.require_api_key)
    locks: dict[str, tuple[asyncio.Lock, int]] = {}
    preview_slots = threading.BoundedSemaphore(2)

    @contextmanager
    def task_reader(job_id: str):
        try:
            with reader_lease(job_id):
                yield
        except FileNotFoundError as exc:
            raise HTTPException(404, 'Task or frame is no longer available') from exc


    @contextmanager
    def picture_slot():
        if not preview_slots.acquire(blocking=False):
            raise HTTPException(429, 'Frame decoder is busy; retry shortly', headers={'Retry-After': '2'})
        try:
            yield
        finally:
            preview_slots.release()

    tags = [api.ASR_TAG]
    errors = {401: {'description': '需要鉴权 / Authentication required'},
              409: {'description': '状态、偏移或修订冲突 / State, offset or revision conflict'},
              410: {'description': '上传已过期 / Upload expired'},
              413: {'description': '超过大小上限 / Size limit exceeded'},
              429: {'description': '资源不足，按 Retry-After 重试 / Resource limit; honor Retry-After'}}

    @asynccontextmanager
    async def upload_lock(key: str):
        lock, users = locks.get(key, (asyncio.Lock(), 0))
        locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, users = locks[key]
            if users == 1:
                del locks[key]
            else:
                locks[key] = (lock, users - 1)

    async def cleanup() -> None:
        while True:
            try:
                for upload_id in await run_in_threadpool(store.expired_ids):
                    if upload_id not in locks:
                        async with upload_lock(upload_id):
                            await run_in_threadpool(store.discard, config, upload_id, expired_only=True)
            except Exception:
                logging.getLogger(__name__).exception('Expired video upload cleanup failed')
            await asyncio.sleep(900)

    prior = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Any):
        async with prior(application):
            task = asyncio.create_task(cleanup())
            await previews.start()
            try:
                yield
            finally:
                await previews.close()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    app.router.lifespan_context = lifespan

    @app.post('/api/v1/asr/video-uploads', tags=tags, status_code=201, response_model=VideoUploadResponse, responses=errors,
              description='创建 32 MiB 顺序分片上传，最大 25 GiB（以能力接口为准）。需 Idempotency-Key；24 小时未活动或满 7 天过期。\n\n**English:** Create a sequential 32 MiB upload, up to 25 GiB by default; capabilities are authoritative. Requires Idempotency-Key. Expires after 24 idle hours or 7 days total.',
              summary='创建可续传视频上传 / Create resumable video upload')
    async def create_upload(body: VideoUploadRequest, idempotency_key: str = Header(..., alias='Idempotency-Key'), _: None = auth) -> dict:
        api.ensure_service('asr')
        key = api.validate_idempotency_key(idempotency_key)
        async with app.state.admission._lock:
            row = await run_in_threadpool(store.create, config, body.name, body.size_bytes, key, app.state.admission._bytes)
        return await run_in_threadpool(store.public, row, config)

    @app.get('/api/v1/asr/video-uploads/{upload_id}', tags=tags, response_model=VideoUploadResponse, responses=errors,
             description='返回已确认偏移和有序 SHA-256 校验表，用于重新选择文件后的完整性检查。\n\n**English:** Return the durable offset and ordered SHA-256 manifest for verifying a reselected local file.',
              summary='读取续传偏移和校验值 / Read resume offset and checksums')
    async def upload_status(upload_id: str, _: None = auth) -> dict:
        async with upload_lock(upload_id):
            return await run_in_threadpool(store.public, await run_in_threadpool(store.get, upload_id), config)

    @app.head('/api/v1/asr/video-uploads/{upload_id}', tags=tags, response_class=Response, responses={**errors, 200: {'description': '上传状态，无响应体 / Upload status, no body', 'headers': {
        'Upload-Offset': {'description': '已确认字节数 / Confirmed bytes', 'schema': {'type': 'integer'}},
        'Upload-Length': {'description': '总字节数 / Total bytes', 'schema': {'type': 'integer'}},
        'Upload-Expires': {'description': 'Unix 秒 / Unix seconds', 'schema': {'type': 'number'}},
    }}},
              description='通过 Upload-Offset、Upload-Length、Upload-Expires 响应头返回状态。\n\n**English:** Read Upload-Offset, Upload-Length and Upload-Expires response headers.',
              summary='读取已确认上传偏移 / Read confirmed upload offset')
    async def upload_head(upload_id: str, _: None = auth) -> Response:
        row = await run_in_threadpool(store.get, upload_id)
        return Response(headers={'Upload-Offset': str(row['offset']), 'Upload-Length': str(row['size_bytes']), 'Upload-Expires': str(row['expires_at'])})

    @app.patch('/api/v1/asr/video-uploads/{upload_id}', tags=tags, response_model=VideoUploadResponse, responses=errors,
               description='二进制请求体，需 Upload-Offset 与十六进制 SHA-256 Upload-Checksum。每片大小等于 chunk_bytes，末片为剩余字节。校验通过并 fsync 后确认偏移。\n\n**English:** Send binary bytes with Upload-Offset and hexadecimal SHA-256 Upload-Checksum. Use exactly chunk_bytes except for the final remainder. Acknowledged offsets are checksum-verified and fsynced; identical replay does not rewrite bytes.',
              summary='追加校验分片 / Append a checksummed chunk',
               openapi_extra={'requestBody': {'required': True, 'content': {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    async def append_upload(upload_id: str, request: Request, upload_offset: int = Header(..., ge=0),
                            upload_checksum: str = Header(..., pattern=r'^[a-fA-F0-9]{64}$'), _: None = auth) -> dict:
        async with upload_lock(upload_id):
            row = await run_in_threadpool(store.get, upload_id)
            if row['job_id']:
                raise HTTPException(409, 'Upload already submitted')
            expected = min(config.video_upload_chunk_bytes, row['size_bytes'] - upload_offset)
            if upload_offset != row['offset']:
                previous = await run_in_threadpool(store.public, row, config)
                if any(c['offset'] == upload_offset and c['sha256'] == upload_checksum.lower() for c in previous['chunks']):
                    return previous
                raise HTTPException(409, 'Upload offset mismatch', headers={'Upload-Offset': str(row['offset'])})
            if expected <= 0:
                raise HTTPException(409, 'Upload already complete')
            length = request.headers.get('content-length')
            if length is not None and (not length.isdigit() or int(length) != expected):
                raise HTTPException(413, 'Chunk size does not match the expected size')
            async with app.state.admission._lock:
                if app.state.admission._active >= app.state.admission.max_concurrent:
                    raise HTTPException(429, 'Too many active uploads', headers={'Retry-After': '1'})
                app.state.admission._active += 1
            output = None
            acknowledged = False
            try:
                output = await run_in_threadpool(store.prepare_file, config, row)
                digest = hashlib.sha256()
                size = 0
                async for block in request.stream():
                    size += len(block)
                    if size > expected:
                        raise HTTPException(413, 'Chunk exceeds expected size')
                    digest.update(block)
                    await run_in_threadpool(output.write, block)
                if size != expected or digest.hexdigest() != upload_checksum.lower():
                    raise HTTPException(422, 'Chunk length or checksum mismatch / 分片大小或校验值不匹配')
                await run_in_threadpool(output.flush)
                await run_in_threadpool(os.fsync, output.fileno())
                store.acknowledge(config, row, size, digest.hexdigest())
                acknowledged = True
            finally:
                if output is not None:
                    # Synchronous finalization also runs when the client cancels.
                    if not acknowledged:
                        output.truncate(row['offset'])
                    output.close()
                async with app.state.admission._lock:
                    app.state.admission._active -= 1
            return await run_in_threadpool(store.public, await run_in_threadpool(store.get, upload_id), config)

    @app.delete('/api/v1/asr/video-uploads/{upload_id}', tags=tags, status_code=204, responses=errors,
                description='立即清理上传文件并释放预留空间。已提交的上传须通过任务删除接口清理。\n\n**English:** Immediately remove unsubmitted bytes and release their reservation. Submitted uploads belong to tasks and require task deletion.',
              summary='取消并清理未提交上传 / Discard an unsubmitted upload')
    async def discard_upload(upload_id: str, _: None = auth) -> Response:
        async with upload_lock(upload_id):
            await run_in_threadpool(store.discard, config, upload_id)
        return Response(status_code=204)

    @app.post('/api/v1/asr/video-jobs', tags=tags, status_code=202, response_model=JobResponse, responses={**errors, 200: {'model': JobResponse, 'description': '幂等重放 / Idempotent replay'}},
              description='上传完整后加入共享 ASR 队列。首次 202，同 key 同请求 200，冲突 409，队列或磁盘限制 429。ROI 为 0–1 归一化区域。\n\n**English:** Enqueue a complete upload in the shared ASR queue. Requires Idempotency-Key: first accept 202, identical replay 200, conflict 409, admission limit 429. ROI uses normalized 0–1 coordinates.',
              summary='提交视频转写与画面分析 / Submit video transcription and frame analysis')
    async def submit_video(body: VideoJobRequest, response: Response, idempotency_key: str = Header(..., alias='Idempotency-Key'), _: None = auth) -> dict:
        api.ensure_service('asr')
        key = api.validate_idempotency_key(idempotency_key)
        caller = body.model_dump(mode='json')
        if body.video_scene_detection is None:
            caller.pop('video_scene_detection', None)
        if body.voiceprint_group_ids is None:
            caller.pop('voiceprint_group_ids', None)
        else:
            caller['voiceprint_group_ids'] = sorted(set(body.voiceprint_group_ids))
        async with upload_lock(body.upload_id):
            old = await run_in_threadpool(db.find_idempotent_job, 'submit_video', api.idempotency_key_hash(key))
            caller.pop('speech_enhancement', None)
            caller.pop('background_denoise', None)
            caller.pop('speech_enhancement_strength', None)
            caller.pop('background_denoise_strength', None)
            caller.update(api.audio_enhancement_request(body.audio_enhancement, body.speech_enhancement, body.background_denoise, old,
                speech_enhancement_strength=body.speech_enhancement_strength, background_denoise_strength=body.background_denoise_strength))
            if old and 'audio_enhancement' not in old['request'].get('video_caller', {}) and caller['audio_enhancement'] == 'off':
                caller.pop('audio_enhancement')
            fingerprint = api.request_fingerprint(caller)
            if old:
                if old['request'].get('video_caller') != caller:
                    raise HTTPException(409, 'Idempotency-Key conflict')
                response.status_code = 200
                response.headers['Idempotency-Replayed'] = 'true'
                return api.public_job(old)
            row = await run_in_threadpool(store.get, body.upload_id)
            if row['job_id'] or row['offset'] != row['size_bytes']:
                raise HTTPException(409, 'Upload is incomplete or already submitted')
            selected, device, device_name = await run_in_threadpool(api.validate_asr_model_device, body.model, body.compute_device or config.default_compute_device)
            language = api.validate_asr_language(body.language)
            hotwords = await run_in_threadpool(api.hotword_request_data, body.context, ','.join(body.hotword_list_ids), None)
            scope = await run_in_threadpool(api.voiceprint_scope_data, body.voiceprint_group_ids, body.use_voiceprint_library and body.diarize, None)
            from .video_frames import probe
            partial, target = store.paths(config, row)
            try:
                await run_in_threadpool(probe, partial if partial.exists() else target)
            except Exception as exc:
                raise HTTPException(422, 'Video needs decodable audio and picture / 视频需包含可解码的音轨和画面') from exc
            decision = await app.state.admission.reserve('asr', 0)
            if not decision.accepted:
                raise api.admission_problem(decision, 'asr')
            try:
                payload = {**caller, **hotwords, **scope, 'video_caller': caller, 'purpose': 'video_transcription',
                           'video_scene_detection': (body.video_scene_detection or SceneDetectionSettings()).model_dump(),
                           'video_detector': 'presentation-v2',
                           'model': selected['public_id'], 'language': language, 'compute_device': device,
                           'compute_device_name': device_name, 'export_formats': []}
                job, replayed = await run_in_threadpool(store.complete, config, row, payload, api.idempotency_key_hash(key), fingerprint)
                if replayed:
                    response.status_code = 200
                return api.public_job(job)
            finally:
                await app.state.admission.release('asr', decision.reserved_bytes)

    def completed(job_id: str, video: bool = False) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, 'Task not found')
        if job['kind'] != 'asr' or job['state'] != 'succeeded' or not job.get('result'):
            raise HTTPException(409, 'A completed transcription is required')
        if video and not job['result'].get('video'):
            raise HTTPException(409, 'This task has no presentation analysis')
        return job

    pdf_headers = {
        'Content-Type': {'schema': {'type': 'string', 'const': 'application/pdf'}},
        'Content-Length': {'description': 'PDF 字节数 / PDF bytes', 'schema': {'type': 'integer'}},
        'Content-Disposition': {'description': 'UTF-8 下载文件名 / UTF-8 download filename', 'schema': {'type': 'string'}},
    }
    @app.head('/api/v1/jobs/{job_id}/transcript.pdf', tags=tags, response_class=Response,
              responses={**errors, 200: {'description': '导出响应头，无响应体 / Export headers, no body', 'headers': pdf_headers}},
              summary='准备简洁 PDF / Prepare concise PDF',
              description='准备同一缓存导出并返回下载头，无响应体。\n\n**English:** Prepare the same cached export and return download headers without a body.')
    @app.get('/api/v1/jobs/{job_id}/transcript.pdf', tags=tags, response_class=Response, responses={**errors, 200: {'headers': pdf_headers, 'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}}},
             description='合并连续同说话人发言及时间范围，保留原文；视频按画面组织图文。仅已完成 ASR，内存缓存，繁忙时 429。\n\n**English:** Merge adjacent speech from the same speaker and its time range, preserving words. Videos include images and matching discussion. Completed ASR only; memory cached, 429 when busy.',
              summary='下载简洁转写 PDF / Download concise transcript PDF')
    def pdf(job_id: str, request: Request, _: None = auth) -> Response:
        from .transcript_pdf import download
        try:
            with speaker_update_lock(job_id), task_reader(job_id):
                job = completed(job_id)
                payload = download(job['display_name'], job['result'], config.jobs_dir / job_id / 'output')
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        except (BlockingIOError, RuntimeError) as exc:
            raise HTTPException(429, str(exc), headers={'Retry-After': '2'}) from exc
        filename = quote(Path(job['display_name']).stem + '-transcript.pdf')
        return Response(payload if request.method != 'HEAD' else None, media_type='application/pdf', headers={'Content-Length': str(len(payload)), 'Content-Disposition': f"attachment; filename*=UTF-8''{filename}", 'Cache-Control': 'no-store'})

    @app.post('/api/v1/jobs/{job_id}/transcript-export', tags=tags, response_class=Response,
              operation_id='exportTranscript', summary='按需脱敏导出转写 / Export a transcript with optional redaction',
              description='默认缩写姓名、遮盖备注；最多 100 对字面关键词替换（每项最多 8000 字）。关闭 redact 停止文字脱敏；任务中的导出隐藏画面标记仍生效，PDF 显示占位框。只处理导出副本；原音视频和图片文件保持原样。\n\n**English:** Names are abbreviated and notes masked by default. Up to 100 literal replacements, 8000 characters per value. Disabling redact disables text transformations; saved export-hidden pictures still use PDF placeholders. Original media remains unchanged.',
              responses={**errors, **api.NOT_FOUND_RESPONSE, **api.VALIDATION_RESPONSE,
                         200: {'description': '所选格式的下载 / Download in the selected format', 'content': {mime: {'schema': api.BINARY_SCHEMA} for mime in ('application/pdf', 'application/json', 'text/plain', 'application/x-subrip', 'text/vtt')}}})
    def transcript_export(job_id: str, body: TranscriptExportRequest, _: None = auth) -> Response:
        from .transcript_export import export_result, format_transcript, MIME_TYPES
        from .transcript_pdf import download
        try:
            with speaker_update_lock(job_id), task_reader(job_id):
                job = completed(job_id)
                result = export_result(job['result'], body)
                title = 'Transcript' if body.redact else job['display_name']
                if body.format == 'pdf':
                    variant = hashlib.sha256(json.dumps([job['updated_at'], body.model_dump()], sort_keys=True).encode()).hexdigest()
                    payload = download(title, result, config.jobs_dir / job_id / 'output', cache_variant=variant)
                else:
                    content = format_transcript(result, body.format)
                    if len(content) > 4_000_000:
                        raise ValueError('Transcript exceeds export resource budget; split the source')
                    payload = content.encode('utf-8')
        except ValueError as exc:
            raise HTTPException(413, str(exc)) from exc
        except (BlockingIOError, RuntimeError) as exc:
            raise HTTPException(429, str(exc), headers={'Retry-After': '2'}) from exc
        stem = 'transcript' if body.redact else Path(job['display_name']).stem + '-transcript'
        filename = quote(stem + '.' + body.format)
        return Response(payload, media_type=MIME_TYPES[body.format], headers={'Content-Length': str(len(payload)), 'Content-Disposition': f"attachment; filename*=UTF-8''{filename}", 'Cache-Control': 'no-store'})

    @app.get('/api/v1/jobs/{job_id}/video-scenes', tags=tags, response_model=VideoScenesResponse, responses=errors,
             description='按画面返回可阅读段落及当前修订号，完整原始分段仍在任务结果中。\n\n**English:** Read illustrated paragraphs and the current revision; original detailed segments remain in job results.',
              summary='读取画面与合并发言 / Read presentation sections and paragraphs')
    def scenes(job_id: str, _: None = auth) -> dict:
        job = completed(job_id, True)
        return {'revision': job['result']['video']['revision'], 'scenes': scene_transcripts(job['result'])}

    @app.get('/api/v1/jobs/{job_id}/video-frames/{name}', tags=tags, response_class=Response, responses={**errors, 200: {'content': {'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}}},
             description='只允许读取当前或自动分段中引用的 JPEG。\n\n**English:** Return a JPEG referenced by current or original automatic sections only.',
              summary='读取已保存关键帧 / Read a saved presentation frame')
    def frame_image(job_id: str, name: str, _: None = auth) -> Response:
        with speaker_update_lock(job_id), task_reader(job_id):
            job = completed(job_id, True)
            video = job['result']['video']
            allowed = {s['image'] for s in video['scenes'] + video['original_scenes']}
            if name not in allowed or Path(name).name != name:
                raise HTTPException(404, 'Frame not found')
            path = config.jobs_dir / job_id / 'output/frames' / name
            return Response(path.read_bytes(), media_type='image/jpeg', headers={'Cache-Control': 'private, max-age=300'})

    @app.get('/api/v1/jobs/{job_id}/video-preview', tags=tags, response_class=Response, responses={**errors, 200: {'content': {'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}}},
             description='按时间解码画面到内存，不写入磁盘。解码并发受限，繁忙时返回 429。\n\n**English:** Decode a timestamp to an in-memory image without disk writes. Bounded decoder concurrency returns 429 when busy.',
              summary='预览指定时刻的画面 / Preview a frame at a timestamp')
    def preview(job_id: str, time: float = Query(..., ge=0, allow_inf_nan=False), _: None = auth) -> Response:
        from .video_frames import picture_at
        with picture_slot(), speaker_update_lock(job_id), task_reader(job_id):
            job = completed(job_id, True)
            video = job['result']['video']
            if time >= video['duration']:
                raise HTTPException(422, 'Timestamp is outside the video')
            picture = picture_at(Path(job['request']['input_path']), time, video.get('roi'), video.get('time_origin', 0))
            buffer = io.BytesIO()
            picture.save(buffer, format='JPEG', quality=85)
            return Response(buffer.getvalue(), media_type='image/jpeg', headers={'Cache-Control': 'no-store'})

    @app.patch('/api/v1/jobs/{job_id}/video-scenes', tags=tags, response_model=JobResultResponse, responses=errors,
               description='携带 revision 修订号进行合并、拆分、边界/截图调整、恢复、应用检测预览或设置导出隐藏画面。冲突返回 409，请重新读取。\n\n**English:** Submit revision with merge_next, split, boundary, frame, restore, apply_detection (with preview_id) or set_export_visibility (with export_hidden_scene_ids). Stale revision returns 409; reload before retrying.',
              summary='修正视频分段 / Edit presentation sections')
    def edit(job_id: str, body: SceneEdit, _: None = auth) -> dict:
        if body.action == 'apply_detection':
            return previews.apply(job_id, body.preview_id, body.revision)
        if body.action == 'set_export_visibility':
            with speaker_update_lock(job_id), task_reader(job_id):
                job = completed(job_id, True)
                result = copy.deepcopy(job['result'])
                video = result['video']
                if video['revision'] != body.revision:
                    raise HTTPException(409, 'Sections changed; reload before editing')
                hidden = set(body.export_hidden_scene_ids or [])
                if not hidden <= {scene['id'] for scene in video['scenes']}:
                    raise HTTPException(422, 'Picture IDs must belong to the current sections')
                ordered = [scene['id'] for scene in video['scenes'] if scene['id'] in hidden]
                if ordered == (video.get('export_hidden_scene_ids') or []):
                    return result
                video.update(export_hidden_scene_ids=ordered, revision=video['revision'] + 1)
                with db.connect() as connection:
                    changed = connection.execute("UPDATE jobs SET result_json=?,updated_at=? WHERE id=? AND state='succeeded' AND updated_at=?",
                        (json.dumps(result, ensure_ascii=False), db.utcnow(), job_id, job['updated_at']))
                    if changed.rowcount != 1:
                        raise HTTPException(409, 'Task changed; reload before editing')
                return result
        from .video_frames import picture_at, save_picture
        with picture_slot(), speaker_update_lock(job_id), task_reader(job_id):
            job = completed(job_id, True)
            result = copy.deepcopy(job['result'])
            video = result['video']
            if video['revision'] != body.revision:
                raise HTTPException(409, 'Sections changed; reload before editing / 分段已更新，请重新加载')
            values = video['scenes']
            index = next((i for i, s in enumerate(values) if s['id'] == body.scene_id), -1)
            if body.action != 'restore' and index < 0:
                raise HTTPException(404, 'Scene not found')
            if body.action == 'restore':
                video['scenes'] = copy.deepcopy(video.get('automatic_scenes') or video['original_scenes'])
                values = video['scenes']
            elif body.action == 'merge_next':
                if index + 1 >= len(values):
                    raise HTTPException(422, 'No next section')
                values[index]['end'] = values[index + 1]['end']
                del values[index + 1]
            elif body.action in {'split', 'boundary', 'frame'}:
                moment = body.time
                scene = values[index]
                lower = scene['start']
                upper = values[index + 1]['end'] if body.action == 'boundary' and index + 1 < len(values) else scene['end']
                if moment is None or not (lower <= moment < upper if body.action == 'frame' else lower < moment < upper):
                    raise HTTPException(422, 'Timestamp must be inside the section(s)')
                if body.action == 'split':
                    if len(values) >= 2000:
                        raise HTTPException(422, 'Maximum 2000 sections')
                    other = {**scene, 'id': 'scene_' + uuid.uuid4().hex, 'start': moment, 'frame_time': -1}
                    scene['end'] = moment
                    values.insert(index + 1, other)
                elif body.action == 'boundary':
                    if index + 1 >= len(values):
                        raise HTTPException(422, 'The final boundary cannot be moved')
                    scene['end'] = moment
                    values[index + 1]['start'] = moment
                else:
                    scene['frame_time'] = moment
            folder = config.jobs_dir / job_id / 'output/frames'
            before = {p.name for p in folder.glob('*.jpg')}
            try:
                for i, scene in enumerate(values):
                    replace = not scene['start'] <= scene['frame_time'] < scene['end'] or body.action == 'frame' and i == index
                    if replace:
                        moment = body.time if body.action == 'frame' and i == index else (scene['start'] + scene['end']) / 2
                        picture = picture_at(Path(job['request']['input_path']), moment, video.get('roi'), video.get('time_origin', 0))
                        scene.update(image=save_picture(picture, folder), frame_time=moment)
                from .video_visibility import retained_hidden
                video['export_hidden_scene_ids'] = retained_hidden(job['result']['video'], values)
                video['revision'] += 1
                with db.connect() as connection:
                    changed = connection.execute("UPDATE jobs SET result_json=?,updated_at=? WHERE id=? AND state='succeeded' AND updated_at=?",
                        (json.dumps(result, ensure_ascii=False), db.utcnow(), job_id, job['updated_at']))
                    if changed.rowcount != 1:
                        raise HTTPException(409, 'Task changed; reload before editing')
            except BaseException:
                for path in folder.glob('*.jpg'):
                    if path.name not in before:
                        path.unlink(missing_ok=True)
                raise
            retained = {s['image'] for s in values + video['original_scenes'] + (video.get('automatic_scenes') or [])}
            for path in folder.glob('*.jpg'):
                if path.name not in retained:
                    path.unlink(missing_ok=True)
            return result
