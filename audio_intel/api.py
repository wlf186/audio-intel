from __future__ import annotations

import asyncio
import hmac
import json
import mimetypes
import os
import secrets
import shutil
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Security, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, TypeAdapter

from . import __version__
from .config import settings
from .gpu import COMPUTE_DEVICES, gpu_snapshot
from .db import (
    create_job,
    create_voice,
    create_voiceprint_person,
    create_voiceprint_sample,
    delete_job_record,
    delete_voice_record,
    delete_voiceprint_person_record,
    delete_voiceprint_sample_record,
    find_voiceprint_person,
    get_job,
    get_voice,
    get_voiceprint_person,
    get_voiceprint_sample,
    init_db,
    list_jobs,
    list_voices,
    list_voiceprint_people,
    list_voiceprint_samples,
    list_workers,
    person_name_key,
    rename_voiceprint_person,
    request_cancel,
    retry_job,
    update_job,
    update_voiceprint_sample,
    utcnow,
)
from .utils import safe_filename
from .purge import purge_jobs
from starlette.concurrency import run_in_threadpool
from .api_docs import (
    API_DESCRIPTION, AUTH_RESPONSES, BINARY_SCHEMA, CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE, OPENAPI_TAGS, SERVICE_RESPONSE, TOO_LARGE_RESPONSE,
    VALIDATION_RESPONSE, bilingual, problem_response,
)
from .api_models import (
    AuthSessionResponse, BatchDeleteResponse, CapabilitiesResponse, EventSnapshot,
    HealthResponse, JobListResponse, JobResponse, JobResultResponse, OpenAIModelList,
    OpenAITranscription, OpenAIVerboseTranscription, ProblemDetail, SystemResponse,
    VoiceListResponse, VoiceProfileResponse, VoiceprintPeopleResponse,
    VoiceprintPersonResponse, VoiceprintSamplesResponse, VoiceprintUploadResponse,
)


PRESET_SPEAKERS = ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"]
SINGLE_TASK_ACCELERATION_DEFAULT = True
ALIGNER_LANGUAGES = [
    "Chinese", "English", "Cantonese", "French", "German", "Italian",
    "Japanese", "Korean", "Portuguese", "Russian", "Spanish",
]
SERVICE_TAG = "Service / 服务"
AUTH_TAG = "Authentication / 鉴权"
ASR_TAG = "ASR / 语音识别"
TTS_TAG = "TTS / 语音合成"
VOICEPRINT_TAG = "Voiceprints / 声纹库"
JOB_TAG = "Jobs / 任务"
OPENAI_TAG = "OpenAI compatibility / OpenAI 兼容"


class BatchDeleteRequest(BaseModel):
    job_ids: list[str] = Field(description="待删除任务 ID；自动去重，最多 100 个 / Job IDs; deduplicated, maximum 100")
    purge: bool = Field(False, description="必须明确为 true，删除不可恢复 / Must be true; deletion is irreversible")


class PersonNameRequest(BaseModel):
    name: str = Field(description="人员显示名称，规范化后必须唯一 / Display name, unique after normalization")


class AddAsrSamplesRequest(BaseModel):
    job_id: str = Field(description="已成功完成的 ASR 任务 ID / Successfully completed ASR job ID")
    segment_ids: list[int] = Field(description="同一说话人的一个或多个段落 ID / One or more segment IDs from one speaker")


class SpeakerNameRequest(BaseModel):
    name: str = Field(description="任务历史中的说话人显示名称 / Speaker display name in this historical job")


SESSION_COOKIE = "audio_intel_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
bearer_auth = HTTPBearer(
    auto_error=False, scheme_name="BearerAuth",
    description="配置 AUDIO_INTEL_API_KEY 后输入密钥本身；客户端发送 Bearer token。 / Enter the API key itself when configured.",
)
session_cookie_auth = APIKeyCookie(
    name=SESSION_COOKIE, auto_error=False, scheme_name="SessionCookie",
    description="由 /api/v1/auth/session 创建的同源 HttpOnly 浏览器会话。 / Same-origin HttpOnly browser session.",
)


def _valid_bearer(authorization: str | None) -> bool:
    if not settings.api_key or not authorization or not authorization.startswith("Bearer "):
        return False
    return hmac.compare_digest(authorization[7:], settings.api_key)


def _same_origin(request: Request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return False
    parsed = urlsplit(source)
    return (parsed.scheme, parsed.netloc) == (request.url.scheme, request.url.netloc)


def _session_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    return bool(token and token in request.app.state.auth_sessions)


def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_auth),
    session_token: str | None = Security(session_cookie_auth),
) -> None:
    bearer_valid = bool(
        credentials and credentials.scheme.lower() == "bearer"
        and hmac.compare_digest(credentials.credentials, settings.api_key)
    )
    if not settings.api_key or bearer_valid:
        return
    if session_token and _session_authenticated(request):
        if request.method in SAFE_METHODS or _same_origin(request):
            return
        raise HTTPException(status_code=403, detail="Cookie-authenticated writes must be same-origin")
    raise HTTPException(status_code=401, detail="Invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})


async def save_upload(upload: UploadFile, target: Path, limit: int | None = None) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    size = 0
    try:
        with partial.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if limit is not None and size > limit:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large")
                output.write(chunk)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)
        await upload.close()
    return size


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in job.items() if key not in {"request"}}
    as_of = utcnow()
    processing_seconds = float(job.get("processing_seconds") or 0)
    if job.get("state") == "running" and job.get("started_at"):
        processing_seconds += max(
            0, (datetime.fromisoformat(as_of) - datetime.fromisoformat(job["started_at"])).total_seconds()
        )
    result["processing_seconds"] = processing_seconds
    result["processing_as_of"] = as_of
    result["request"] = job.get("request")
    request_data = job.get("request") or {}
    result_data = job.get("result") or {}
    compute_device = result_data.get("compute_device") or request_data.get("compute_device") or (
        "gpu" if job.get("kind") == "asr" else "cpu"
    )
    result["compute_device"] = compute_device
    result["compute_device_name"] = (
        result_data.get("compute_device_name")
        or request_data.get("compute_device_name")
        or ("CPU" if compute_device == "cpu" else "GPU")
    )
    result["status_url"] = f"/api/v1/jobs/{job['id']}"
    if job.get("kind") == "asr":
        result["source_url"] = f"/api/v1/jobs/{job['id']}/source"
    if job.get("state") == "succeeded":
        result["result_url"] = f"/api/v1/jobs/{job['id']}/result"
    return result


def public_voiceprint_sample(sample: dict[str, Any]) -> dict[str, Any]:
    item = {
        key: value for key, value in sample.items()
        if key not in {"audio_path", "embedding", "embedding_model", "embedding_error"}
    }
    item["tts_eligible"] = bool(
        sample.get("state") == "ready" and sample.get("audio_path") and sample.get("transcript")
    )
    item["embedding_status"] = (
        "ready" if sample.get("embedding") else "failed" if sample.get("embedding_error") else "pending"
    )
    if sample.get("audio_path"):
        item["audio_url"] = f"/api/v1/voiceprints/samples/{sample['id']}/audio"
    return item


def public_voiceprint_person(person: dict[str, Any]) -> dict[str, Any]:
    samples = [public_voiceprint_sample(sample) for sample in person.get("samples", [])]
    return {
        "id": person["id"], "name": person["name"], "created_at": person["created_at"],
        "updated_at": person["updated_at"], "sample_count": len(samples), "samples": samples,
    }


def job_or_404(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def ensure_service(kind: str) -> None:
    if kind not in settings.enabled_services:
        raise HTTPException(status_code=503, detail=f"{kind.upper()} service is disabled")


def validate_compute_device(value: str) -> tuple[str, str]:
    normalized = value.strip().lower()
    if normalized not in COMPUTE_DEVICES:
        raise HTTPException(status_code=422, detail="compute_device must be cpu or gpu")
    if normalized == "cpu":
        return normalized, "CPU"
    snapshot = gpu_snapshot(0)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="GPU compute is unavailable; select CPU or check NVIDIA runtime")
    return normalized, str(snapshot["name"])


def compute_capabilities(default: str) -> list[dict[str, Any]]:
    return [
        {"id": "cpu", "precision": "FP32", "available": True, "default": default == "cpu", "quantized": False},
        {"id": "gpu", "precision": "BF16", "available": gpu_snapshot() is not None, "default": default == "gpu", "quantized": False},
    ]


def validate_boolean(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HTTPException(status_code=422, detail=f"{field} must be a boolean")


def create_app() -> FastAPI:
    settings.ensure_directories()
    init_db()
    app = FastAPI(
        title="Sandevistan-Audio",
        description=API_DESCRIPTION,
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_tags=OPENAPI_TAGS,
    )
    app.state.auth_sessions = set()
    docs_assets = settings.frontend_dir / "docs-assets"
    app.mount("/docs-assets", StaticFiles(directory=docs_assets, check_dir=False), name="docs-assets")

    @app.get("/docs", include_in_schema=False)
    def local_api_docs() -> Response:
        required = [docs_assets / "swagger-ui.css", docs_assets / "swagger-ui-bundle.js"]
        if not all(path.is_file() for path in required):
            return HTMLResponse(
                "<h1>API documentation assets are not built</h1>"
                "<p>Run <code>./service.sh setup api</code> or <code>service.cmd setup api</code>.</p>",
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        response = get_swagger_ui_html(
            openapi_url=app.openapi_url or "/openapi.json",
            title="Sandevistan-Audio · API Docs",
            swagger_js_url="/docs-assets/swagger-ui-bundle.js",
            swagger_css_url="/docs-assets/swagger-ui.css",
            swagger_favicon_url="/sandevistan-audio.svg",
            swagger_ui_parameters={
                "deepLinking": True, "displayRequestDuration": True, "filter": True,
                "persistAuthorization": False, "showExtensions": True,
                "showCommonExtensions": True, "validatorUrl": None,
            },
        )
        response.headers.update({
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "font-src 'self'; connect-src 'self'; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'"
            ),
        })
        return response

    @app.exception_handler(HTTPException)
    async def http_problem(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "about:blank",
                "title": detail,
                "status": exc.status_code,
                "code": f"http_{exc.status_code}",
                "detail": detail,
            },
            headers=exc.headers,
            media_type="application/problem+json",
        )

    @app.get(
        "/api/v1/health", response_model=HealthResponse, response_model_exclude_unset=True,
        tags=[SERVICE_TAG], summary="公开健康检查 / Public health probe",
        description=bilingual(
            "唯一公开且不返回详细系统信息的运行探针。",
            "The only public operational probe; it exposes no detailed system data.",
        ),
        operation_id="getHealth",
    )
    def health() -> HealthResponse:
        return {"status": "ok", "version": __version__, "offline": True}

    @app.get(
        "/api/v1/auth/session", response_model=AuthSessionResponse, response_model_exclude_unset=True,
        tags=[AUTH_TAG], summary="检查鉴权会话 / Inspect authentication session",
        description=bilingual("检查是否需要密钥以及当前请求是否已认证。", "Check whether a key is required and whether this client is authenticated."),
        operation_id="getAuthSession",
    )
    def auth_session(request: Request, authorization: str | None = Header(default=None, description="可选 Bearer 密钥 / Optional Bearer key")) -> AuthSessionResponse:
        required = bool(settings.api_key)
        authenticated = not required or _valid_bearer(authorization) or _session_authenticated(request)
        return {"required": required, "authenticated": authenticated}

    @app.post(
        "/api/v1/auth/session", status_code=204, tags=[AUTH_TAG],
        summary="创建浏览器会话 / Create browser session",
        description=bilingual(
            "用 Bearer 密钥换取内存中的 HttpOnly 同源 Cookie；服务重启后失效。API 客户端通常应直接使用 Bearer。",
            "Exchange a Bearer key for an in-memory same-origin HttpOnly cookie. It expires on restart; API clients should normally keep using Bearer.",
        ),
        operation_id="createAuthSession", responses={**AUTH_RESPONSES},
    )
    def create_auth_session(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Security(bearer_auth),
    ) -> Response:
        if not settings.api_key:
            return Response(status_code=204)
        if not credentials or credentials.scheme.lower() != "bearer" or not hmac.compare_digest(
            credentials.credentials, settings.api_key
        ):
            raise HTTPException(status_code=401, detail="Invalid or missing API key", headers={"WWW-Authenticate": "Bearer"})
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            request.app.state.auth_sessions.discard(previous)
        token = secrets.token_urlsafe(32)
        request.app.state.auth_sessions.add(token)
        response = Response(status_code=204)
        response.set_cookie(
            SESSION_COOKIE, token, httponly=True, secure=request.url.scheme == "https",
            samesite="strict", path="/",
        )
        return response

    @app.delete(
        "/api/v1/auth/session", status_code=204, tags=[AUTH_TAG],
        summary="注销浏览器会话 / Delete browser session",
        description=bilingual("删除当前同源会话 Cookie。", "Delete the current same-origin session cookie."),
        operation_id="deleteAuthSession",
    )
    def delete_auth_session(
        request: Request, _session_token: str | None = Security(session_cookie_auth),
    ) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            request.app.state.auth_sessions.discard(token)
        response = Response(status_code=204)
        response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
        return response

    @app.get(
        "/api/v1/system", response_model=SystemResponse, response_model_exclude_unset=True,
        tags=[SERVICE_TAG], summary="读取详细系统状态 / Get detailed system status",
        description=bilingual("返回 worker、硬件、模型 revision 和本地存储路径；始终受保护。", "Return workers, hardware, model revisions, and local storage paths; always protected."),
        operation_id="getSystem", responses={**AUTH_RESPONSES},
    )
    def system(_: None = Depends(require_api_key)) -> SystemResponse:
        return system_snapshot()

    @app.get(
        "/api/v1/capabilities", response_model=CapabilitiesResponse, response_model_exclude_unset=True,
        tags=[SERVICE_TAG], summary="读取服务能力 / Get service capabilities",
        description=bilingual("消费方应从这里读取设备可用性、格式、上限和默认值，不要硬编码部署能力。", "Read live device availability, formats, limits, and defaults here instead of hard-coding deployment capabilities."),
        operation_id="getCapabilities", responses={**AUTH_RESPONSES},
    )
    def capabilities(_: None = Depends(require_api_key)) -> CapabilitiesResponse:
        return {
            "services": sorted(settings.enabled_services),
            "offline": True,
            "asr": {
                "model": "Qwen3-ASR-0.6B",
                "diarization": "CAM++ single-active-speaker",
                "speaker_count": {"min": 1, "max": 15, "default": "auto"},
                "voiceprint_library": True,
                "timestamp_precisions": ["segment", "word_or_character"],
                "aligner_languages": ALIGNER_LANGUAGES,
                "exports": ["json", "srt", "vtt", "txt"],
                "compute_devices": compute_capabilities("gpu"),
                "single_task_acceleration": {"supported": True, "default": SINGLE_TASK_ACCELERATION_DEFAULT},
            },
            "tts": {
                "models": ["Qwen3-TTS-12Hz-0.6B-Base", "Qwen3-TTS-12Hz-0.6B-CustomVoice"],
                "voice_modes": ["preset", "profile", "inline_clone", "voiceprint"],
                "preset_speakers": PRESET_SPEAKERS,
                "formats": ["wav", "flac", "mp3"],
                "compute_devices": compute_capabilities("cpu"),
                "single_task_acceleration": {"supported": True, "default": SINGLE_TASK_ACCELERATION_DEFAULT},
            },
            "limits": {
                "max_upload_bytes": settings.max_upload_bytes,
                "max_tts_chars": settings.max_tts_chars,
                "max_clone_reference_seconds": 15,
            },
        }

    @app.post(
        "/api/v1/asr/jobs", status_code=202, response_model=JobResponse,
        response_model_exclude_unset=True, tags=[ASR_TAG],
        summary="提交异步 ASR 任务 / Submit asynchronous ASR job",
        description=bilingual(
            "上传音频并立即返回排队任务。通过 `status_url` 轮询；成功后读取 `result_url`。GPU 不可用时返回 503，不回退 CPU。",
            "Upload audio and immediately receive a queued job. Poll `status_url`, then read `result_url`. An unavailable GPU returns 503 and never falls back silently.",
        ),
        operation_id="submitAsrJob",
        responses={**AUTH_RESPONSES, **TOO_LARGE_RESPONSE, **VALIDATION_RESPONSE, **SERVICE_RESPONSE},
    )
    async def submit_asr(
        file: UploadFile = File(..., description="待转写音频或视频 / Audio or video to transcribe"),
        language: str = Form("Auto", description="识别语言；Auto 自动检测 / Recognition language; Auto detects"),
        speaker_count: str = Form("auto", description="auto 或 1–15 / auto or an integer from 1 to 15"),
        diarize: bool = Form(True, description="启用说话人分离 / Enable speaker diarization"),
        align: bool = Form(True, description="返回支持语言的精确时间戳 / Produce precise timestamps for supported languages"),
        context: str = Form("", description="识别上下文提示 / Recognition context hint"),
        export_formats: str = Form("json,srt,vtt,txt", description="逗号分隔：json,srt,vtt,txt / Comma-separated export formats"),
        compute_device: str = Form("gpu", description="cpu 或 gpu；无静默回退 / cpu or gpu; no silent fallback"),
        use_voiceprint_library: bool = Form(True, description="用声纹库匹配并命名说话人 / Match and label speakers from the voiceprint library"),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="启用质量中性的单任务自动批处理 / Enable quality-neutral single-job auto-batching"),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("asr")
        compute_device, compute_device_name = validate_compute_device(compute_device)
        job_id = uuid.uuid4().hex
        original_name = safe_filename(file.filename or "audio.bin")
        input_path = settings.jobs_dir / job_id / "input" / original_name
        size = await save_upload(file, input_path, settings.max_upload_bytes)
        try:
            speaker_value: int | None = None if speaker_count == "auto" else int(speaker_count)
        except ValueError as exc:
            shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
            raise HTTPException(status_code=422, detail="speaker_count must be auto or an integer") from exc
        if speaker_value is not None and not 1 <= speaker_value <= 15:
            shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
            raise HTTPException(status_code=422, detail="speaker_count must be between 1 and 15")
        formats = [item.strip().lower() for item in export_formats.split(",") if item.strip()]
        if not formats or any(item not in {"json", "srt", "vtt", "txt"} for item in formats):
            raise HTTPException(status_code=422, detail="Unsupported export format")
        request_data = {
            "input_path": str(input_path), "original_name": original_name, "size_bytes": size,
            "language": language, "speaker_count": speaker_value, "diarize": diarize,
            "align": align, "context": context, "export_formats": formats, "compute_device": compute_device,
            "compute_device_name": compute_device_name, "use_voiceprint_library": use_voiceprint_library,
            "accelerate_single_task": accelerate_single_task,
        }
        return public_job(create_job("asr", original_name, request_data, job_id))

    @app.post(
        "/api/v1/tts/jobs", status_code=202, response_model=JobResponse,
        response_model_exclude_unset=True, tags=[TTS_TAG],
        summary="提交异步 TTS 任务 / Submit asynchronous TTS job",
        description=bilingual(
            "四种模式：`preset` 传 `speaker`；`profile` 传 `voice_profile_id`；`inline_clone` 上传 `reference_audio` 并传逐字准确的 `reference_text`；`voiceprint` 传具体且可用于 TTS 的 `voiceprint_sample_id`。",
            "Four modes: `preset` uses `speaker`; `profile` uses `voice_profile_id`; `inline_clone` requires `reference_audio` plus an exact `reference_text`; `voiceprint` requires a concrete TTS-eligible `voiceprint_sample_id`.",
        ),
        operation_id="submitTtsJob",
        responses={**AUTH_RESPONSES, **TOO_LARGE_RESPONSE, **VALIDATION_RESPONSE, **SERVICE_RESPONSE},
    )
    async def submit_tts(
        text: str = Form(..., description="需要合成的文本 / Text to synthesize"),
        language: str = Form("Chinese", description="生成语言 / Synthesis language"),
        voice_mode: str = Form("preset", description="preset、profile、inline_clone 或 voiceprint"),
        speaker: str | None = Form(None, description="preset 模式的官方音色 / Official speaker for preset mode"),
        voice_profile_id: str | None = Form(None, description="profile 模式的声音档案 ID / Voice profile ID for profile mode"),
        voiceprint_sample_id: str | None = Form(None, description="voiceprint 模式的具体可用样本 ID / Concrete eligible sample ID for voiceprint mode"),
        reference_audio: UploadFile | None = File(None, description="inline_clone 模式参考音频 / Reference audio for inline_clone"),
        reference_text: str | None = Form(None, description="必须与参考音频逐字一致 / Must exactly match the reference audio"),
        instruct: str = Form("", description="预置音色风格指令 / Style instruction for preset voice"),
        response_format: str = Form("wav", description="wav、flac 或 mp3"),
        display_name: str = Form("语音合成", description="任务显示名称 / Job display name"),
        compute_device: str = Form("cpu", description="cpu 或 gpu；无静默回退 / cpu or gpu; no silent fallback"),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="启用质量中性的单任务自动批处理 / Enable quality-neutral single-job auto-batching"),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("tts")
        compute_device, compute_device_name = validate_compute_device(compute_device)
        clean_text = text.strip()
        if not clean_text or len(clean_text) > settings.max_tts_chars:
            raise HTTPException(status_code=422, detail=f"Text must contain 1-{settings.max_tts_chars} characters")
        if voice_mode not in {"preset", "profile", "inline_clone", "voiceprint"}:
            raise HTTPException(status_code=422, detail="voice_mode must be preset, profile, inline_clone or voiceprint")
        if response_format not in {"wav", "flac", "mp3"}:
            raise HTTPException(status_code=422, detail="response_format must be wav, flac or mp3")
        request_data: dict[str, Any] = {
            "text": clean_text, "language": language, "voice_mode": voice_mode,
            "speaker": speaker, "voice_profile_id": voice_profile_id, "reference_text": reference_text,
            "instruct": instruct, "response_format": response_format, "compute_device": compute_device,
            "compute_device_name": compute_device_name,
            "accelerate_single_task": accelerate_single_task,
        }
        job_id = uuid.uuid4().hex
        if voice_mode == "preset":
            if speaker not in PRESET_SPEAKERS:
                raise HTTPException(status_code=422, detail="Unknown preset speaker")
        elif voice_mode == "profile":
            voice = get_voice(voice_profile_id or "")
            if voice is None:
                raise HTTPException(status_code=422, detail="Voice profile not found")
            source = Path(voice["ref_audio_path"]).resolve()
            target = settings.jobs_dir / job_id / "input" / "voice-reference.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            request_data.update({
                "reference_audio_path": str(target), "reference_text": voice["ref_text"],
                "reference_words": voice.get("words") or [], "reference_duration": voice.get("duration"),
                "reference_language": voice.get("language") or language,
            })
        elif voice_mode == "voiceprint":
            sample = get_voiceprint_sample(voiceprint_sample_id or "")
            person = get_voiceprint_person(sample["person_id"]) if sample else None
            if (
                sample is None or person is None or sample.get("state") != "ready"
                or not sample.get("audio_path") or not sample.get("transcript")
            ):
                raise HTTPException(status_code=422, detail="Voiceprint sample is not ready for TTS cloning")
            source = Path(sample["audio_path"]).resolve()
            if settings.voiceprints_dir.resolve() not in source.parents and settings.voices_dir.resolve() not in source.parents:
                raise HTTPException(status_code=422, detail="Voiceprint sample audio is unavailable")
            target = settings.jobs_dir / job_id / "input" / "voiceprint-reference.wav"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            request_data.update({
                "voiceprint_person_id": person["id"], "voiceprint_person_name": person["name"],
                "voiceprint_sample_id": sample["id"], "reference_audio_path": str(target),
                "reference_text": sample["transcript"], "reference_words": sample.get("words") or [],
                "reference_language": sample.get("language") or language,
                "reference_duration": sample.get("duration"),
            })
        else:
            if reference_audio is None or not (reference_text or "").strip():
                raise HTTPException(status_code=422, detail="Inline cloning requires reference_audio and reference_text")
            filename = safe_filename(reference_audio.filename or "reference.wav")
            target = settings.jobs_dir / job_id / "input" / filename
            await save_upload(reference_audio, target, 100 * 1024 * 1024)
            request_data["reference_audio_path"] = str(target)
        return public_job(create_job("tts", safe_filename(display_name, "tts"), request_data, job_id))

    @app.get(
        "/api/v1/tts/voices", response_model=VoiceListResponse, response_model_exclude_unset=True,
        tags=[TTS_TAG], summary="列出声音档案与预置音色 / List voice profiles and presets",
        description=bilingual("返回可用于 `profile` 模式的档案，以及 `preset` 模式官方音色。", "Return profiles usable by `profile` mode and official speakers for `preset` mode."),
        operation_id="listTtsVoices", responses={**AUTH_RESPONSES},
    )
    def voices(_: None = Depends(require_api_key)) -> VoiceListResponse:
        return {"items": list_voices(), "preset_speakers": PRESET_SPEAKERS}

    @app.post(
        "/api/v1/tts/voices", status_code=201, response_model=VoiceProfileResponse,
        response_model_exclude_unset=True, tags=[TTS_TAG],
        summary="创建声音档案 / Create voice profile",
        description=bilingual("保存本地参考音频和逐字准确文本，同时建立可复用声音档案。", "Store local reference audio and its exact transcript as a reusable voice profile."),
        operation_id="createTtsVoice",
        responses={**AUTH_RESPONSES, **TOO_LARGE_RESPONSE, **VALIDATION_RESPONSE, **SERVICE_RESPONSE},
    )
    async def save_voice(
        name: str = Form(..., description="声音档案名称 / Voice profile name"),
        language: str = Form("Chinese", description="参考音频语言 / Reference language"),
        ref_text: str = Form(..., description="与参考音频逐字一致的文本 / Transcript exactly matching the reference audio"),
        ref_audio: UploadFile = File(..., description="本地参考音频 / Local reference audio"),
        _: None = Depends(require_api_key),
    ) -> VoiceProfileResponse:
        ensure_service("tts")
        if not name.strip() or not ref_text.strip():
            raise HTTPException(status_code=422, detail="Voice name and accurate reference text are required")
        voice_dir = settings.voices_dir / uuid.uuid4().hex
        target = voice_dir / safe_filename(ref_audio.filename or "reference.wav")
        await save_upload(ref_audio, target, 100 * 1024 * 1024)
        return create_voice(name.strip(), language, str(target), ref_text.strip())

    @app.delete(
        "/api/v1/tts/voices/{voice_id}", status_code=204, tags=[TTS_TAG],
        summary="永久删除声音档案 / Permanently delete voice profile",
        description=bilingual("必须传 `purge=true`；同时删除其本地样本，不可恢复。", "Requires `purge=true`; local samples are also deleted and cannot be recovered."),
        operation_id="deleteTtsVoice",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def remove_voice(
        voice_id: str,
        purge: bool = Query(False, description="必须为 true / Must be true"),
        _: None = Depends(require_api_key),
    ) -> Response:
        person = get_voiceprint_person(voice_id)
        if person is None:
            raise HTTPException(status_code=404, detail="Voice profile not found")
        if not purge:
            raise HTTPException(status_code=409, detail="Set purge=true to permanently delete this voice profile")
        for sample in list_voiceprint_samples(person["id"]):
            path = Path(sample["audio_path"]).resolve() if sample.get("audio_path") else None
            if path and (settings.voices_dir.resolve() in path.parents or settings.voiceprints_dir.resolve() in path.parents):
                path.unlink(missing_ok=True)
        delete_voice_record(person["id"])
        return Response(status_code=204)

    @app.get(
        "/api/v1/voiceprints/people", response_model=VoiceprintPeopleResponse,
        response_model_exclude_unset=True, tags=[VOICEPRINT_TAG],
        summary="列出声纹人员和样本 / List voiceprint people and samples",
        description=bilingual("人员响应内嵌全部样本；TTS 只应选择 `state=ready` 且 `tts_eligible=true` 的样本。", "Each person embeds all samples; TTS clients must select a sample with `state=ready` and `tts_eligible=true`."),
        operation_id="listVoiceprintPeople", responses={**AUTH_RESPONSES},
    )
    def voiceprint_people(_: None = Depends(require_api_key)) -> VoiceprintPeopleResponse:
        return {"items": [public_voiceprint_person(item) for item in list_voiceprint_people()]}

    @app.post(
        "/api/v1/voiceprints/people", status_code=201, response_model=VoiceprintPersonResponse,
        response_model_exclude_unset=True, tags=[VOICEPRINT_TAG],
        summary="创建声纹人员 / Create voiceprint person",
        description=bilingual("名称经过 Unicode 和空白规范化后必须唯一。", "The name must be unique after Unicode and whitespace normalization."),
        operation_id="createVoiceprintPerson",
        responses={**AUTH_RESPONSES, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def add_voiceprint_person(
        payload: PersonNameRequest, _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        try:
            return public_voiceprint_person(create_voiceprint_person(payload.name))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A voiceprint person with this name already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch(
        "/api/v1/voiceprints/people/{person_id}", response_model=VoiceprintPersonResponse,
        response_model_exclude_unset=True, tags=[VOICEPRINT_TAG],
        summary="重命名声纹人员 / Rename voiceprint person",
        description=bilingual("只更新声纹库名称；已完成任务中的说话人名称是历史快照，不会被改写。", "Only the library name changes; speaker names in completed jobs are historical snapshots and are not rewritten."),
        operation_id="updateVoiceprintPerson",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def edit_voiceprint_person(
        person_id: str, payload: PersonNameRequest, _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        try:
            person = rename_voiceprint_person(person_id, payload.name)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A voiceprint person with this name already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if person is None:
            raise HTTPException(status_code=404, detail="Voiceprint person not found")
        person["samples"] = list_voiceprint_samples(person_id)
        return public_voiceprint_person(person)

    def ensure_sample_jobs_inactive(samples: list[dict[str, Any]]) -> None:
        for sample in samples:
            source_job_id = sample.get("source_job_id")
            source_job = get_job(source_job_id) if source_job_id else None
            if source_job and (source_job.get("request") or {}).get("purpose") == "voiceprint_import" and source_job["state"] in {"queued", "running"}:
                raise HTTPException(status_code=409, detail="Cancel the active voiceprint import task before deleting its sample")

    def remove_voiceprint_audio(sample: dict[str, Any]) -> None:
        if not sample.get("audio_path"):
            return
        path = Path(sample["audio_path"]).resolve()
        roots = {settings.voiceprints_dir.resolve(), settings.voices_dir.resolve()}
        if any(root in path.parents for root in roots):
            path.unlink(missing_ok=True)

    @app.delete(
        "/api/v1/voiceprints/people/{person_id}", status_code=204, tags=[VOICEPRINT_TAG],
        summary="永久删除声纹人员 / Permanently delete voiceprint person",
        description=bilingual("要求 `purge=true`。若样本入库任务仍在排队或运行，先取消任务。", "Requires `purge=true`. Cancel any queued or running sample-import job first."),
        operation_id="deleteVoiceprintPerson",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def remove_voiceprint_person(
        person_id: str, purge: bool = Query(False, description="必须为 true / Must be true"), _: None = Depends(require_api_key),
    ) -> Response:
        person = get_voiceprint_person(person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="Voiceprint person not found")
        if not purge:
            raise HTTPException(status_code=409, detail="Set purge=true to permanently delete this person")
        samples = list_voiceprint_samples(person["id"])
        ensure_sample_jobs_inactive(samples)
        for sample in samples:
            remove_voiceprint_audio(sample)
        delete_voiceprint_person_record(person["id"])
        shutil.rmtree(settings.voiceprints_dir / person["id"], ignore_errors=True)
        return Response(status_code=204)

    @app.post(
        "/api/v1/voiceprints/people/{person_id}/samples/from-asr", status_code=201,
        response_model=VoiceprintSamplesResponse, response_model_exclude_unset=True,
        tags=[VOICEPRINT_TAG], summary="从 ASR 段落创建声纹样本 / Create samples from ASR segments",
        description=bilingual(
            "来源任务必须成功，所选段落必须属于同一说话人且未曾入库。每个段落独立保存为本地 WAV 样本。",
            "The source job must have succeeded; selected segments must belong to one speaker and not already be imported. Each segment becomes a separate local WAV sample.",
        ),
        operation_id="createVoiceprintSamplesFromAsr",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    async def add_voiceprint_samples_from_asr(
        person_id: str, payload: AddAsrSamplesRequest, _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        person = get_voiceprint_person(person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="Voiceprint person not found")
        job = job_or_404(payload.job_id)
        if job["kind"] != "asr" or job["state"] != "succeeded":
            raise HTTPException(status_code=409, detail="Only completed ASR segments can be added")
        segment_ids = list(dict.fromkeys(payload.segment_ids))
        if not segment_ids:
            raise HTTPException(status_code=422, detail="Select at least one ASR segment")
        segments_by_id = {int(item["id"]): item for item in (job.get("result") or {}).get("segments", [])}
        try:
            segments = [segments_by_id[item] for item in segment_ids]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="ASR segment not found") from exc
        speaker_ids = {item.get("speaker") for item in segments}
        if len(speaker_ids) != 1:
            raise HTTPException(status_code=422, detail="Selected segments must belong to the same speaker")
        existing_sources = {
            (sample.get("source_job_id"), sample.get("source_segment_id"))
            for sample in list_voiceprint_samples()
        }
        if any((job["id"], item["id"]) in existing_sources for item in segments):
            raise HTTPException(status_code=409, detail="One or more selected segments already exist in the voiceprint library")
        source = Path((job.get("request") or {}).get("input_path", "")).resolve()
        input_root = (settings.jobs_dir / job["id"] / "input").resolve()
        if input_root not in source.parents or not source.is_file():
            raise HTTPException(status_code=404, detail="Source recording is unavailable")
        from .media import extract_audio_clip

        created: list[dict[str, Any]] = []
        paths: list[Path] = []
        try:
            for segment in segments:
                sample_id = "sample_" + uuid.uuid4().hex[:16]
                path = settings.voiceprints_dir / person["id"] / f"{sample_id}.wav"
                duration = await run_in_threadpool(
                    extract_audio_clip, source, path, float(segment["start"]), float(segment["end"]),
                )
                paths.append(path)
                words = [
                    {
                        "text": word.get("text", ""),
                        "start": round(max(0.0, float(word["start"]) - float(segment["start"])), 3),
                        "end": round(max(0.0, float(word["end"]) - float(segment["start"])), 3),
                    }
                    for word in segment.get("words", [])
                ]
                sample = create_voiceprint_sample(
                    person["id"], sample_id=sample_id, state="ready",
                    language=(job.get("result") or {}).get("language") or "Auto",
                    audio_path=str(path), transcript=str(segment.get("text", "")), words=words,
                    duration=duration, source_job_id=job["id"], source_segment_id=int(segment["id"]),
                    source_speaker_id=str(segment.get("speaker") or "Speaker_0"),
                )
                created.append(sample)
        except Exception:
            for sample in created:
                delete_voiceprint_sample_record(sample["id"])
            for path in paths:
                path.unlink(missing_ok=True)
            raise
        return {"items": [public_voiceprint_sample(item) for item in created]}

    @app.post(
        "/api/v1/voiceprints/people/{person_id}/samples/upload", status_code=202,
        response_model=VoiceprintUploadResponse, response_model_exclude_unset=True,
        tags=[VOICEPRINT_TAG], summary="上传并转写声纹样本 / Upload and transcribe voiceprint sample",
        description=bilingual(
            "立即返回 `pending` 样本和可见 ASR 入库任务。任务成功后重新查询人员列表，样本才可能用于 TTS。",
            "Immediately return a pending sample and a visible ASR import job. Refresh the people list after success before using the sample for TTS.",
        ),
        operation_id="uploadVoiceprintSample",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **TOO_LARGE_RESPONSE, **VALIDATION_RESPONSE, **SERVICE_RESPONSE},
    )
    async def upload_voiceprint_sample(
        person_id: str,
        file: UploadFile = File(..., description="单人干净音频或浏览器录音容器 / Clean single-speaker audio or browser recording container"),
        language: str = Form("Auto", description="转写语言 / Transcription language"),
        compute_device: str = Form("gpu", description="cpu 或 gpu；无静默回退 / cpu or gpu; no silent fallback"),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("asr")
        person = get_voiceprint_person(person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="Voiceprint person not found")
        compute_device, compute_device_name = validate_compute_device(compute_device)
        job_id = uuid.uuid4().hex
        sample_id = "sample_" + uuid.uuid4().hex[:16]
        original_name = safe_filename(file.filename or "voiceprint-audio.bin")
        input_path = settings.jobs_dir / job_id / "input" / original_name
        size = await save_upload(file, input_path, settings.max_upload_bytes)
        request_data = {
            "purpose": "voiceprint_import", "voiceprint_sample_id": sample_id,
            "input_path": str(input_path), "original_name": original_name, "size_bytes": size,
            "language": language, "speaker_count": 1, "diarize": False, "align": True,
            "context": "", "export_formats": ["json", "txt"], "compute_device": compute_device,
            "compute_device_name": compute_device_name, "use_voiceprint_library": False,
        }
        job = create_job("asr", f"声纹样本入库 · {person['name']}", request_data, job_id)
        try:
            sample = create_voiceprint_sample(
                person["id"], sample_id=sample_id, state="pending", language=language,
                source_job_id=job_id,
            )
        except Exception:
            delete_job_record(job_id)
            shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
            raise
        return {"sample": public_voiceprint_sample(sample), "job": public_job(job)}

    @app.delete(
        "/api/v1/voiceprints/people/{person_id}/samples/{sample_id}", status_code=204,
        tags=[VOICEPRINT_TAG], summary="永久删除声纹样本 / Permanently delete voiceprint sample",
        description=bilingual("要求 `purge=true`；活动入库任务必须先取消并结束。", "Requires `purge=true`; an active import job must be cancelled and reach a terminal state first."),
        operation_id="deleteVoiceprintSample",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def remove_voiceprint_sample(
        person_id: str, sample_id: str, purge: bool = Query(False, description="必须为 true / Must be true"),
        _: None = Depends(require_api_key),
    ) -> Response:
        sample = get_voiceprint_sample(sample_id)
        if sample is None or sample["person_id"] != person_id:
            raise HTTPException(status_code=404, detail="Voiceprint sample not found")
        if not purge:
            raise HTTPException(status_code=409, detail="Set purge=true to permanently delete this sample")
        ensure_sample_jobs_inactive([sample])
        remove_voiceprint_audio(sample)
        delete_voiceprint_sample_record(sample_id)
        return Response(status_code=204)

    @app.get(
        "/api/v1/voiceprints/samples/{sample_id}/audio", response_class=FileResponse,
        tags=[VOICEPRINT_TAG], summary="读取声纹样本音频 / Get voiceprint sample audio",
        description=bilingual("返回受保护的本地 WAV；不会暴露任意文件路径。", "Return protected local WAV audio without exposing arbitrary filesystem paths."),
        operation_id="getVoiceprintSampleAudio",
        responses={
            200: {"description": "WAV 音频 / WAV audio", "content": {"audio/wav": {"schema": BINARY_SCHEMA}}},
            **AUTH_RESPONSES, **NOT_FOUND_RESPONSE,
        },
    )
    def voiceprint_sample_audio(sample_id: str, _: None = Depends(require_api_key)) -> FileResponse:
        sample = get_voiceprint_sample(sample_id)
        if sample is None or not sample.get("audio_path"):
            raise HTTPException(status_code=404, detail="Voiceprint sample audio is unavailable")
        path = Path(sample["audio_path"]).resolve()
        roots = {settings.voiceprints_dir.resolve(), settings.voices_dir.resolve()}
        if not any(root in path.parents for root in roots) or not path.is_file():
            raise HTTPException(status_code=404, detail="Voiceprint sample audio is unavailable")
        return FileResponse(path, media_type="audio/wav")

    @app.get(
        "/api/v1/jobs", response_model=JobListResponse, response_model_exclude_unset=True,
        tags=[JOB_TAG], summary="列出任务 / List jobs",
        description=bilingual(
            "按创建时间倒序分页。`count` 仅是本页数量；队列实际消费按任务类型分别以创建时间 FIFO。",
            "Paginated newest-first. `count` is only the page size; ASR and TTS workers consume their separate queues FIFO by creation time.",
        ),
        operation_id="listJobs", responses={**AUTH_RESPONSES, **VALIDATION_RESPONSE},
    )
    def jobs(
        kind: str | None = Query(None, description="可选 asr 或 tts / Optional asr or tts"),
        state: str | None = Query(None, description="queued、running、succeeded、failed 或 cancelled"),
        limit: int = Query(100, ge=1, le=500, description="每页 1–500 / Page size from 1 to 500"),
        offset: int = Query(0, ge=0, description="分页偏移 / Page offset"),
        _: None = Depends(require_api_key),
    ) -> JobListResponse:
        items = [public_job(item) for item in list_jobs(kind, state, limit, offset)]
        return {"items": items, "count": len(items), "limit": limit, "offset": offset}

    @app.get(
        "/api/v1/jobs/{job_id}", response_model=JobResponse, response_model_exclude_unset=True,
        tags=[JOB_TAG], summary="查询任务状态与进度 / Get job status and progress",
        description=bilingual(
            "可靠轮询入口。`progress` 为 0–1 阶段检查点，`stage` 是当前阶段；成功后出现 `result_url`。不返回排队序号。",
            "Canonical polling endpoint. `progress` is a 0–1 stage checkpoint and `stage` names the current phase. `result_url` appears after success. Queue position is not returned.",
        ),
        operation_id="getJob", responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE},
    )
    def job_status(job_id: str, _: None = Depends(require_api_key)) -> JobResponse:
        return public_job(job_or_404(job_id))

    @app.post(
        "/api/v1/jobs/batch-delete", response_model=BatchDeleteResponse,
        response_model_exclude_unset=True, tags=[JOB_TAG],
        summary="批量永久删除任务 / Permanently delete jobs in a batch",
        description=bilingual("最多 100 个 ID。逐项返回删除或失败结果；运行中任务不会被删除。", "Accepts up to 100 IDs and returns per-item outcomes. Running jobs are never deleted."),
        operation_id="batchDeleteJobs",
        responses={**AUTH_RESPONSES, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def batch_delete_jobs(payload: BatchDeleteRequest, _: None = Depends(require_api_key)) -> dict[str, Any]:
        if not payload.purge:
            raise HTTPException(status_code=409, detail="Set purge=true to permanently delete input, output and history")
        job_ids = [item.strip() for item in payload.job_ids if item.strip()]
        if not job_ids:
            raise HTTPException(status_code=422, detail="job_ids must contain at least one task ID")
        if len(set(job_ids)) > 100:
            raise HTTPException(status_code=422, detail="A maximum of 100 task IDs can be deleted at once")
        return purge_jobs(job_ids)

    @app.post(
        "/api/v1/jobs/{job_id}/cancel", response_model=JobResponse,
        response_model_exclude_unset=True, tags=[JOB_TAG],
        summary="取消任务 / Cancel job",
        description=bilingual("排队任务原子取消；运行任务先进入 `cancelling`，完整进程树退出后才进入 `cancelled`。", "Queued jobs cancel atomically. Running jobs enter `cancelling` and become `cancelled` only after the complete process tree exits."),
        operation_id="cancelJob", responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE},
    )
    def cancel_job(job_id: str, _: None = Depends(require_api_key)) -> JobResponse:
        job = request_cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        request_data = job.get("request") or {}
        if request_data.get("purpose") == "voiceprint_import" and job["state"] == "cancelled":
            update_voiceprint_sample(
                request_data.get("voiceprint_sample_id", ""), state="failed",
                error_message="Voiceprint import task was cancelled",
            )
        return public_job(job)

    @app.post(
        "/api/v1/jobs/{job_id}/retry", response_model=JobResponse,
        response_model_exclude_unset=True, tags=[JOB_TAG],
        summary="重试终态任务 / Retry terminal job",
        description=bilingual("仅失败或取消任务可重新排队；累计处理耗时会保留。", "Only failed or cancelled jobs can be queued again; accumulated processing time is retained."),
        operation_id="retryJob",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE},
    )
    def retry(job_id: str, _: None = Depends(require_api_key)) -> JobResponse:
        try:
            job = retry_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        request_data = job.get("request") or {}
        if request_data.get("purpose") == "voiceprint_import":
            update_voiceprint_sample(
                request_data.get("voiceprint_sample_id", ""), state="pending",
                error_message=None, embedding_error=None,
            )
        return public_job(job)

    @app.delete(
        "/api/v1/jobs/{job_id}", status_code=204, tags=[JOB_TAG],
        summary="永久删除单个任务 / Permanently delete one job",
        description=bilingual("要求 `purge=true`，同时清理输入、输出、临时文件和数据库记录。运行中任务会被拒绝。", "Requires `purge=true` and removes input, output, temporary files, and the database row. Running jobs are rejected."),
        operation_id="deleteJob",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def purge_job(
        job_id: str, purge: bool = Query(False, description="必须为 true / Must be true"),
        _: None = Depends(require_api_key),
    ) -> Response:
        if not purge:
            raise HTTPException(status_code=409, detail="Set purge=true to permanently delete input, output and history")
        result = purge_jobs([job_id])
        if result["failed"]:
            failure = result["failed"][0]
            status = 404 if failure["code"] == "not_found" else 409 if failure["code"] == "running" else 500
            raise HTTPException(status_code=status, detail=failure["message"])
        if not result["database_compacted"]:
            raise HTTPException(status_code=500, detail=result["maintenance_error"] or "Database compaction failed")
        return Response(status_code=204)

    @app.get(
        "/api/v1/jobs/{job_id}/result", response_model=JobResultResponse,
        response_model_exclude_unset=True, tags=[JOB_TAG],
        summary="读取成功任务结果 / Get successful job result",
        description=bilingual("只在任务 `succeeded` 后可用；此前返回 409。结果中的 artifact URL 用于受保护下载。", "Available only after `succeeded`; earlier calls return 409. Use artifact URLs for protected downloads."),
        operation_id="getJobResult",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE},
    )
    def job_result(job_id: str, _: None = Depends(require_api_key)) -> JobResultResponse:
        job = job_or_404(job_id)
        if job["state"] != "succeeded":
            raise HTTPException(status_code=409, detail="Job has not completed successfully")
        return job.get("result") or {}

    @app.get(
        "/api/v1/jobs/{job_id}/source", response_class=FileResponse, tags=[JOB_TAG],
        summary="读取 ASR 原始音源 / Get original ASR source",
        description=bilingual("仅 ASR 任务可用，支持 `Range` 和 `206 Partial Content`；`download=true` 强制附件下载。", "ASR only. Supports Range and `206 Partial Content`; `download=true` forces attachment download."),
        operation_id="getJobSource",
        responses={
            200: {"description": "完整媒体 / Full media", "content": {"audio/*": {"schema": BINARY_SCHEMA}, "video/*": {"schema": BINARY_SCHEMA}}},
            206: {"description": "部分媒体 / Partial media", "headers": {"Content-Range": {"schema": {"type": "string"}}}, "content": {"application/octet-stream": {"schema": BINARY_SCHEMA}}},
            416: problem_response("Range 超出文件范围 / Requested range is not satisfiable", 416),
            **AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE,
        },
    )
    def job_source(
        job_id: str, download: bool = Query(False, description="作为附件下载 / Download as an attachment"), _: None = Depends(require_api_key),
    ) -> FileResponse:
        job = job_or_404(job_id)
        if job["kind"] != "asr":
            raise HTTPException(status_code=409, detail="Only ASR jobs have a source recording")
        input_root = (settings.jobs_dir / job_id / "input").resolve()
        raw_path = (job.get("request") or {}).get("input_path")
        if not raw_path:
            raise HTTPException(status_code=404, detail="Source recording is missing")
        path = Path(raw_path).resolve()
        if input_root not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Source recording is missing")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=mime, filename=path.name if download else None)

    @app.get(
        "/api/v1/jobs/{job_id}/artifacts/{name}", response_class=FileResponse,
        tags=[JOB_TAG], summary="下载任务产物 / Download job artifact",
        description=bilingual("名称必须来自成功任务结果的 `artifacts`，文件始终受鉴权和路径包含检查保护。", "The name must come from a successful result's `artifacts`; authentication and path-containment checks always apply."),
        operation_id="getJobArtifact",
        responses={
            200: {"description": "音频、JSON 或字幕文件 / Audio, JSON, or subtitle file", "content": {"application/octet-stream": {"schema": BINARY_SCHEMA}}},
            **AUTH_RESPONSES, **NOT_FOUND_RESPONSE,
        },
    )
    def artifact(job_id: str, name: str, _: None = Depends(require_api_key)) -> FileResponse:
        job = job_or_404(job_id)
        artifacts = {item["name"]: item for item in (job.get("result") or {}).get("artifacts", [])}
        item = artifacts.get(name)
        if item is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        path = Path(item["path"]).resolve()
        if settings.jobs_dir not in path.parents or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact file is missing")
        return FileResponse(path, media_type=item.get("mime_type") or mimetypes.guess_type(path.name)[0], filename=path.name)

    @app.patch(
        "/api/v1/jobs/{job_id}/speakers/{speaker_id}", response_model=JobResultResponse,
        response_model_exclude_unset=True, tags=[JOB_TAG],
        summary="重命名 ASR 说话人 / Rename ASR speaker",
        description=bilingual("只修改已成功 ASR 任务的历史结果和导出文件，不修改声纹库。请求 JSON 为 `{\"name\": \"显示名称\"}`。", "Update a completed ASR result and its exports only; the voiceprint library is unchanged. JSON body: `{\"name\": \"Display name\"}`."),
        operation_id="updateJobSpeaker",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def rename_speaker(
        job_id: str, speaker_id: str, payload: SpeakerNameRequest, _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        job = job_or_404(job_id)
        if job["kind"] != "asr" or job["state"] != "succeeded":
            raise HTTPException(status_code=409, detail="Only completed ASR speakers can be renamed")
        name = payload.name.strip()[:80]
        if not name:
            raise HTTPException(status_code=422, detail="Speaker name is required")
        result = job.get("result") or {}
        found = False
        for speaker in result.get("speakers", []):
            if speaker.get("id") == speaker_id:
                speaker["label"] = name
                speaker["label_source"] = "manual"
                found = True
        if not found:
            raise HTTPException(status_code=404, detail="Speaker not found")
        labels = {item["id"]: item["label"] for item in result.get("speakers", [])}
        for segment in result.get("segments", []):
            if segment.get("speaker") in labels:
                segment["speaker_label"] = labels[segment["speaker"]]
        from asr.pipeline import write_asr_exports
        result = write_asr_exports(job_id, result, job["request"].get("export_formats", ["json", "srt", "vtt", "txt"]))
        update_job(job_id, result_json=result)
        return result

    @app.get(
        "/api/v1/events", response_class=StreamingResponse, tags=[JOB_TAG],
        summary="订阅任务和 worker 快照 / Stream job and worker snapshots",
        description=bilingual("SSE 在最近 25 个任务或 worker 发生变化时发送 `snapshot`；约每 2 秒检查，空闲时发送注释保活。无事件 ID、断点续传或历史重放。", "SSE emits `snapshot` when the latest 25 jobs or workers change, checks about every two seconds, and sends comment keepalives while idle. There are no event IDs, resume tokens, or replay."),
        operation_id="streamEvents",
        responses={
            200: {
                "description": "Server-Sent Events",
                "content": {"text/event-stream": {
                    "schema": {"type": "string"},
                    "example": "event: snapshot\\ndata: {\"jobs\":[],\"workers\":[]}\\n\\n",
                }},
                "x-event-data-schema": {"$ref": "#/components/schemas/EventSnapshot"},
            },
            **AUTH_RESPONSES,
        },
    )
    async def events(_: None = Depends(require_api_key)) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            last = ""
            while True:
                payload = json.dumps({"jobs": list_jobs(limit=25), "workers": list_workers()}, ensure_ascii=False)
                if payload != last:
                    yield f"event: snapshot\ndata: {payload}\n\n"
                    last = payload
                else:
                    yield ": keepalive\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    add_openai_routes(app)

    assets = settings.frontend_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/sandevistan-audio.svg", include_in_schema=False)
    def brand_mark() -> Response:
        logo = settings.frontend_dir / "sandevistan-audio.svg"
        if logo.is_file():
            return FileResponse(logo, media_type="image/svg+xml")
        raise HTTPException(status_code=404, detail="Brand mark not found")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:
        if full_path.startswith(("api/", "v1/")):
            raise HTTPException(status_code=404, detail="Not found")
        index = settings.frontend_dir / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse("<h1>Sandevistan-Audio</h1><p>Frontend is not built. Run service.sh or service.cmd setup api.</p>", status_code=503)

    default_openapi = app.openapi

    def local_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = default_openapi()
        extra = TypeAdapter(
            ProblemDetail | EventSnapshot | OpenAITranscription | OpenAIVerboseTranscription
        ).json_schema(ref_template="#/components/schemas/{model}")
        schema.setdefault("components", {}).setdefault("schemas", {}).update(extra.get("$defs", {}))
        schema["servers"] = [{"url": "/", "description": "当前本地服务 / Current local service"}]
        app.openapi_schema = schema
        return schema

    app.openapi = local_openapi  # type: ignore[method-assign]

    return app


def system_snapshot() -> dict[str, Any]:
    workers = list_workers()
    try:
        import psutil

        memory = psutil.virtual_memory()
        # psutil's Windows extension requires str rather than a PathLike object.
        disk = psutil.disk_usage(str(settings.root))
        hardware: dict[str, Any] = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_used": memory.used,
            "memory_total": memory.total,
            "disk_used": disk.used,
            "disk_total": disk.total,
        }
    except ImportError:
        hardware = {}
    hardware["gpu"] = gpu_snapshot()
    from .model_registry import model_installation, model_manifest
    models = []
    for model in model_manifest():
        installation = model_installation(settings.models_dir, model)
        models.append({
            "name": model["name"], "device": model["device"],
            "installed": installation["installed"], "state": installation["state"],
            "revision": installation["revision"], "actual_revision": installation["actual_revision"],
            "missing_files": installation["missing_files"],
            "path": str(settings.models_dir / model["name"]),
        })
    return {
        "status": "ok", "version": __version__, "offline": True,
        "bind": f"{settings.host}:{settings.port}", "services": sorted(settings.enabled_services),
        "workers": workers, "hardware": hardware, "models": models,
        "storage": {key: str(value) for key, value in {
            "models": settings.models_dir, "data": settings.data_dir, "temp": settings.temp_dir,
            "cache": settings.cache_dir, "logs": settings.log_dir,
        }.items()},
    }


async def wait_for_job(job_id: str, timeout: float = 24 * 3600) -> dict[str, Any]:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        job = job_or_404(job_id)
        if job["state"] in {"succeeded", "failed", "cancelled"}:
            return job
        await asyncio.sleep(0.75)
    raise HTTPException(status_code=504, detail="Job did not finish before the compatibility timeout")


def add_openai_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/models", response_model=OpenAIModelList, response_model_exclude_unset=True,
        tags=[OPENAI_TAG], summary="列出兼容模型 / List compatible models",
        description=bilingual("只列出当前启用服务对应的本地模型别名。", "List local model aliases for currently enabled services only."),
        operation_id="listOpenAIModels", responses={**AUTH_RESPONSES},
    )
    def openai_models(_: None = Depends(require_api_key)) -> OpenAIModelList:
        data = []
        if "asr" in settings.enabled_services:
            data.append({"id": "qwen3-asr-0.6b", "object": "model", "owned_by": "local"})
        if "tts" in settings.enabled_services:
            data.append({"id": "qwen3-tts-0.6b", "object": "model", "owned_by": "local"})
        return {"object": "list", "data": data}

    @app.post(
        "/v1/audio/transcriptions", response_class=Response, tags=[OPENAI_TAG],
        summary="同步兼容转写 / Create synchronous compatible transcription",
        description=bilingual(
            "兼容式同步入口，会等待内部任务完成。长音频、进度查询、取消和可靠恢复应使用 `/api/v1/asr/jobs`。响应头 `X-Job-ID` 可关联历史任务。",
            "Synchronous compatibility endpoint that waits for the internal job. Use `/api/v1/asr/jobs` for long audio, progress, cancellation, and recovery. `X-Job-ID` links the response to job history.",
        ),
        operation_id="createOpenAITranscription",
        responses={
            200: {
                "description": "由 response_format 决定 / Selected by response_format",
                "headers": {"X-Job-ID": {"schema": {"type": "string"}}},
                "content": {
                    "application/json": {"schema": {"oneOf": [
                        {"$ref": "#/components/schemas/OpenAITranscription"},
                        {"$ref": "#/components/schemas/OpenAIVerboseTranscription"},
                    ]}},
                    "text/plain": {"schema": {"type": "string"}},
                    "application/x-subrip": {"schema": {"type": "string"}},
                    "text/vtt": {"schema": {"type": "string"}},
                },
            },
            500: problem_response("内部转写任务失败 / Internal transcription job failed", 500),
            504: problem_response("兼容接口等待超时 / Compatibility wait timed out", 504),
            **AUTH_RESPONSES, **TOO_LARGE_RESPONSE, **VALIDATION_RESPONSE, **SERVICE_RESPONSE,
        },
    )
    async def openai_transcription(
        file: UploadFile = File(..., description="待转写音频或视频 / Audio or video to transcribe"),
        model: str = Form("qwen3-asr-0.6b", description="qwen3-asr-0.6b"),
        language: str = Form("Auto", description="识别语言 / Recognition language"),
        response_format: str = Form("json", description="json、verbose_json、text、srt 或 vtt"),
        diarize: bool = Form(True, description="启用说话人分离 / Enable diarization"),
        speaker_count: str = Form("auto", description="auto 或 1–15 / auto or 1–15"),
        compute_device: str = Form("gpu", description="cpu 或 gpu；无静默回退 / cpu or gpu; no silent fallback"),
        use_voiceprint_library: bool = Form(True, description="匹配声纹库 / Match voiceprint library"),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="单任务自动批处理 / Single-job auto-batching"),
        _: None = Depends(require_api_key),
    ) -> Response:
        ensure_service("asr")
        compute_device, compute_device_name = validate_compute_device(compute_device)
        if model not in {"qwen3-asr-0.6b", "Qwen/Qwen3-ASR-0.6B"}:
            raise HTTPException(status_code=404, detail="Unknown transcription model")
        try:
            speakers = None if speaker_count == "auto" else int(speaker_count)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="speaker_count must be auto or an integer") from exc
        if speakers is not None and not 1 <= speakers <= 15:
            raise HTTPException(status_code=422, detail="speaker_count must be between 1 and 15")
        job_id = uuid.uuid4().hex
        name = safe_filename(file.filename or "audio.bin")
        target = settings.jobs_dir / job_id / "input" / name
        size = await save_upload(file, target, settings.max_upload_bytes)
        request_data = {
            "input_path": str(target), "original_name": name, "size_bytes": size, "language": language,
            "speaker_count": speakers, "diarize": diarize, "align": True, "context": "",
            "export_formats": ["json", "srt", "vtt", "txt"], "compute_device": compute_device,
            "compute_device_name": compute_device_name, "use_voiceprint_library": use_voiceprint_library,
            "accelerate_single_task": accelerate_single_task,
        }
        create_job("asr", name, request_data, job_id)
        job = await wait_for_job(job_id)
        if job["state"] != "succeeded":
            raise HTTPException(status_code=500, detail=job.get("error_message") or "Transcription failed")
        result = job.get("result") or {}
        response_headers = {"X-Job-ID": job_id}
        if response_format == "text":
            return PlainTextResponse(result.get("text", ""), headers=response_headers)
        if response_format in {"srt", "vtt"}:
            artifact = next((item for item in result.get("artifacts", []) if item["name"].endswith(f".{response_format}")), None)
            if artifact:
                return FileResponse(artifact["path"], media_type=artifact["mime_type"], headers=response_headers)
        if response_format == "verbose_json":
            return JSONResponse({"task": "transcribe", "language": result.get("language"), "duration": result.get("duration"), "text": result.get("text", ""), "segments": result.get("segments", [])}, headers=response_headers)
        return JSONResponse({"text": result.get("text", "")}, headers=response_headers)

    @app.post(
        "/v1/audio/speech", response_class=FileResponse, tags=[OPENAI_TAG],
        summary="同步兼容语音合成 / Create synchronous compatible speech",
        description=bilingual(
            "等待内部 TTS 任务完成并直接返回音频。`voice` 支持官方预置音色或 `voice_` 声音档案，不支持直接传声纹样本 ID。需要进度、取消或精确声纹样本时使用 `/api/v1/tts/jobs`。",
            "Wait for an internal TTS job and return audio. `voice` accepts an official preset or a `voice_` profile, not a voiceprint sample ID. Use `/api/v1/tts/jobs` for progress, cancellation, or an exact voiceprint sample.",
        ),
        operation_id="createOpenAISpeech",
        responses={
            200: {
                "description": "生成音频 / Generated audio",
                "headers": {"X-Job-ID": {"schema": {"type": "string"}}},
                "content": {
                    "audio/wav": {"schema": BINARY_SCHEMA},
                    "audio/flac": {"schema": BINARY_SCHEMA},
                    "audio/mpeg": {"schema": BINARY_SCHEMA},
                },
            },
            500: problem_response("内部合成任务失败 / Internal speech job failed", 500),
            504: problem_response("兼容接口等待超时 / Compatibility wait timed out", 504),
            **AUTH_RESPONSES, **VALIDATION_RESPONSE, **SERVICE_RESPONSE,
        },
    )
    async def openai_speech(payload: dict[str, Any] = Body(...), _: None = Depends(require_api_key)) -> FileResponse:
        ensure_service("tts")
        compute_device, compute_device_name = validate_compute_device(str(payload.get("compute_device", "cpu")))
        accelerate_single_task = validate_boolean(
            payload.get("accelerate_single_task", SINGLE_TASK_ACCELERATION_DEFAULT), "accelerate_single_task",
        )
        if payload.get("model", "qwen3-tts-0.6b") not in {"qwen3-tts-0.6b", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice"}:
            raise HTTPException(status_code=404, detail="Unknown speech model")
        text = str(payload.get("input", "")).strip()
        if not text:
            raise HTTPException(status_code=422, detail="input is required")
        voice = str(payload.get("voice", "Vivian"))
        request_data: dict[str, Any] = {
            "text": text, "language": payload.get("language", "Auto"), "instruct": payload.get("instructions", ""),
            "response_format": payload.get("response_format", "wav"), "compute_device": compute_device,
            "compute_device_name": compute_device_name,
            "accelerate_single_task": accelerate_single_task,
        }
        if voice.startswith("voice_"):
            profile = get_voice(voice)
            if profile is None:
                raise HTTPException(status_code=422, detail="Voice profile not found")
            request_data.update({"voice_mode": "profile", "voice_profile_id": voice, "reference_audio_path": profile["ref_audio_path"], "reference_text": profile["ref_text"]})
        else:
            if voice not in PRESET_SPEAKERS:
                raise HTTPException(status_code=422, detail="Unknown voice")
            request_data.update({"voice_mode": "preset", "speaker": voice})
        job = create_job("tts", "speech", request_data)
        finished = await wait_for_job(job["id"])
        if finished["state"] != "succeeded":
            raise HTTPException(status_code=500, detail=finished.get("error_message") or "Speech synthesis failed")
        artifact = (finished.get("result") or {}).get("artifacts", [])[0]
        return FileResponse(artifact["path"], media_type=artifact["mime_type"], headers={"X-Job-ID": job["id"]})


app = create_app()
