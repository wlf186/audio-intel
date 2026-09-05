from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import ssl
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
from .deployment import deployment_metadata
from .gpu import COMPUTE_DEVICES, cached_gpu_snapshot, gpu_snapshot
from .db import (
    MAX_VOICEPRINT_NOTE_CHARS,
    ReadOnlyHotwordListError,
    create_hotword_list,
    create_job,
    create_job_idempotent,
    create_voice,
    create_voiceprint_person,
    create_voiceprint_sample,
    delete_hotword_list,
    delete_job_record,
    delete_voice_record,
    delete_voiceprint_person_record,
    delete_voiceprint_sample_record,
    event_revision,
    find_voiceprint_person,
    find_idempotent_job,
    get_hotword_list,
    get_job,
    get_voice,
    get_voiceprint_person,
    get_voiceprint_sample,
    init_db,
    list_jobs,
    list_jobs_page,
    list_hotword_lists,
    list_voices,
    list_voiceprint_people,
    list_voiceprint_samples,
    list_workers,
    person_name_key,
    request_cancel,
    retry_job,
    update_job,
    update_hotword_list,
    update_voiceprint_person,
    update_voiceprint_sample,
    utcnow,
    IdempotencyConflict,
)
from .admission import AdmissionController
from .events import SnapshotHub
from .observability import estimate_for_job, queue_context, queue_for_job, stage_details
from .hotwords import (
    MAX_HOTWORD_LISTS, MAX_HOTWORD_NAME_CHARS, MAX_HOTWORD_PROMPT_CHARS,
    MAX_HOTWORD_TERM_CHARS, MAX_SELECTED_LISTS, MAX_SELECTED_TERMS,
    MAX_TERMS_PER_LIST, compile_hotword_context, parse_hotword_list_ids,
)
from .model_registry import (
    asr_models, default_asr_model, model_installation, resolve_asr_model,
    default_tts_model, resolve_tts_checkpoint, resolve_tts_model, tts_models,
)
from .utils import safe_filename
from .purge import purge_jobs
from starlette.concurrency import run_in_threadpool
from .api_docs import (
    ADMISSION_RESPONSE, API_DESCRIPTION, ASR_SERVICE_RESPONSE, ASR_VALIDATION_RESPONSE,
    AUTH_RESPONSES, BINARY_SCHEMA, CONFLICT_RESPONSE,
    IDEMPOTENCY_RESPONSES, conditional_job_responses, idempotency_replay_response,
    NOT_FOUND_RESPONSE, OPENAPI_TAGS, OPTIONAL_IDEMPOTENCY_RESPONSES, SERVICE_RESPONSE, TOO_LARGE_RESPONSE,
    TTS_CONTROL_VALIDATION_RESPONSE, TTS_SERVICE_RESPONSE, VALIDATION_RESPONSE, bilingual, problem_response, sse_response,
    enrich_openapi_schema,
)
from .api_models import (
    AdmissionProblemDetail, AuthSessionResponse, BatchDeleteResponse, CapabilitiesResponse,
    EventJobResponse, EventSnapshot, EventUpdate,
    HealthResponse, JobListResponse, JobResponse, JobResultResponse, OpenAIModelList, QueueResponse,
    OpenAISpeechRequest, OpenAITranscription, OpenAIVerboseTranscription, ProblemDetail, SystemResponse,
    TtsSequenceRequest,
    VoiceListResponse, VoiceProfileResponse, VoiceprintPeopleResponse,
    VoiceprintPersonResponse, VoiceprintSamplesResponse, VoiceprintUploadResponse,
    HotwordListResponse, HotwordListsResponse, TlsBootstrapResponse,
)


PRESET_SPEAKERS = ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"]
PRESET_SPEAKER_NATIVE_LANGUAGES = {
    "Vivian": "Chinese", "Serena": "Chinese", "Uncle_Fu": "Chinese",
    "Dylan": "Chinese", "Eric": "Chinese", "Ryan": "English", "Aiden": "English",
    "Ono_Anna": "Japanese", "Sohee": "Korean",
}
DEFAULT_ASR_MODEL_ID = str(default_asr_model()["public_id"])
DEFAULT_TTS_MODEL_ID = str(default_tts_model()["public_id"])
MAX_TTS_INSTRUCTION_CHARS = 1000
TTS_LANGUAGES = [
    "Auto", "Chinese", "English", "Japanese", "Korean", "German",
    "French", "Russian", "Portuguese", "Spanish", "Italian",
]
TTS_LANGUAGE_BY_KEY = {language.lower(): language for language in TTS_LANGUAGES}
SINGLE_TASK_ACCELERATION_DEFAULT = True
ALIGNER_LANGUAGES = [
    "Chinese", "English", "Cantonese", "French", "German", "Italian",
    "Japanese", "Korean", "Portuguese", "Russian", "Spanish",
]
ASR_LANGUAGES = ["Auto", *ALIGNER_LANGUAGES]
ASR_LANGUAGE_BY_KEY = {language.lower(): language for language in ASR_LANGUAGES}
REFERENCE_LANGUAGE_BY_KEY = {
    language.lower(): language for language in ASR_LANGUAGES
}
SERVICE_TAG = "Service / 服务"
AUTH_TAG = "Authentication / 鉴权"
ASR_TAG = "ASR / 语音识别"
TTS_TAG = "TTS / 语音合成"
VOICEPRINT_TAG = "Voiceprints / 声纹库"
JOB_TAG = "Jobs / 任务"
OPENAI_TAG = "OpenAI compatibility / OpenAI 兼容"
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~:+-]{8,128}$")
IDEMPOTENCY_KEY_DESCRIPTION = (
    "必填；8–128 个 A–Z、a–z、0–9 或 ._~:+- 字符；同一次逻辑提交重试必须复用 / "
    "Required; 8–128 A–Z, a–z, 0–9, or ._~:+- characters; reuse for retries of the same logical submission"
)
OPTIONAL_IDEMPOTENCY_KEY_DESCRIPTION = (
    "可选；若发送，须为 8–128 个 A–Z、a–z、0–9 或 ._~:+- 字符且同一次重试复用 / "
    "Optional; when sent, use 8–128 A–Z, a–z, 0–9, or ._~:+- characters and reuse it for retries"
)
IDEMPOTENCY_KEY_SCHEMA = {
    "minLength": 8, "maxLength": 128,
    "pattern": r"^[A-Za-z0-9._~:+-]{8,128}$",
    "example": "550e8400-e29b-41d4-a716-446655440000",
}
EVENT_SNAPSHOT_JOB_LIMIT = 100
EVENT_SNAPSHOT_POLL_SECONDS = 0.5
EVENT_HEARTBEAT_SECONDS = 15
ASYNC_SUBMISSION_ROUTES = {
    "/api/v1/asr/jobs": ("asr", "submit_asr", True, True),
    "/api/v1/tts/clone-references": ("asr", "analyze_tts_clone_reference", True, True),
    "/api/v1/tts/jobs": ("tts", "submit_tts", False, True),
    "/api/v1/tts/sequence-jobs": ("tts", "submit_tts_sequence", False, True),
    "/v1/audio/transcriptions": ("asr", "openai_transcription", True, False),
    "/v1/audio/speech": ("tts", "openai_speech", False, False),
}


class ApiProblem(HTTPException):
    def __init__(
        self, status_code: int, code: str, detail: str,
        headers: dict[str, str] | None = None, extras: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.extras = extras or {}


class BatchDeleteRequest(BaseModel):
    job_ids: list[str] = Field(description="待删除任务 ID；自动去重，最多 100 个 / Job IDs; deduplicated, maximum 100")
    purge: bool = Field(False, description="必须明确为 true，删除不可恢复 / Must be true; deletion is irreversible")


class VoiceprintPersonCreateRequest(BaseModel):
    name: str = Field(description="人员显示名称，规范化后必须唯一 / Display name, unique after normalization")
    note: str | None = Field(None, description=f"可选单行备注，规范化后最多 {MAX_VOICEPRINT_NOTE_CHARS} 字 / Optional single-line note")
    include_in_hotword_library: bool = Field(True, description="是否同步到系统人名热词表 / Whether to sync the name to the system hotword list")


class VoiceprintPersonUpdateRequest(BaseModel):
    name: str | None = Field(None, description="新的人员显示名称 / New display name")
    note: str | None = Field(None, description=f"新备注；null 或空白用于清除，最多 {MAX_VOICEPRINT_NOTE_CHARS} 字 / New note; null or blank clears it")
    include_in_hotword_library: bool | None = Field(None, description="是否同步到系统人名热词表 / Whether to sync the name to the system hotword list")


class AddAsrSamplesRequest(BaseModel):
    job_id: str = Field(description="已成功完成的 ASR 任务 ID / Successfully completed ASR job ID")
    segment_ids: list[int] = Field(description="同一说话人的一个或多个段落 ID / One or more segment IDs from one speaker")


class HotwordListCreateRequest(BaseModel):
    name: str = Field(description="场景词表名称 / Scene vocabulary name")
    terms: list[str] = Field(description="按优先顺序排列的热词 / Ordered hotword terms")


class HotwordListUpdateRequest(BaseModel):
    name: str | None = Field(None, description="新的场景词表名称 / New scene vocabulary name")
    terms: list[str] | None = Field(None, description="完整替换后的热词 / Complete replacement term list")


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


def _admission_authenticated(request: Request) -> bool:
    if not settings.api_key:
        return True
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token and hmac.compare_digest(token, settings.api_key):
        return True
    return _session_authenticated(request) and _same_origin(request)


def _submission_route(path: str) -> tuple[str, str, bool, bool] | None:
    route = ASYNC_SUBMISSION_ROUTES.get(path)
    if route is not None:
        return route
    if re.fullmatch(r"/api/v1/voiceprints/people/[^/]+/samples/upload", path):
        return "asr", "upload_voiceprint_sample", True, True
    return None


def _problem_response(exc: ApiProblem) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "type": "about:blank", "title": exc.detail, "status": exc.status_code,
            "code": exc.code, "detail": exc.detail, **exc.extras,
        },
        headers=exc.headers,
        media_type="application/problem+json",
    )


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


async def save_upload(upload: UploadFile, target: Path, limit: int | None = None) -> tuple[int, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    def persist() -> tuple[int, str]:
        size = 0
        digest = hashlib.sha256()
        upload.file.seek(0)
        with partial.open("wb") as output:
            while chunk := upload.file.read(4 * 1024 * 1024):
                size += len(chunk)
                if limit is not None and size > limit:
                    raise ApiProblem(413, "upload_too_large", "Uploaded file is too large")
                digest.update(chunk)
                output.write(chunk)
        os.replace(partial, target)
        return size, digest.hexdigest()
    try:
        return await run_in_threadpool(persist)
    finally:
        partial.unlink(missing_ok=True)
        await upload.close()


def validate_idempotency_key(value: str | None) -> str:
    if value is None:
        raise ApiProblem(400, "idempotency_key_required", "Idempotency-Key is required")
    key = value.strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise ApiProblem(
            400, "invalid_idempotency_key",
            "Idempotency-Key must contain 8-128 HTTP token characters",
        )
    return key


def idempotency_key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def request_fingerprint(
    request_data: dict[str, Any], file_digest: str | None = None,
    ignored_fields: set[str] | None = None,
) -> str:
    ignored = {"input_path", "reference_audio_path", *(ignored_fields or set())}
    canonical = {key: value for key, value in request_data.items() if key not in ignored}
    canonical["file_sha256"] = file_digest
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def idempotent_job(
    kind: str,
    display_name: str,
    request_data: dict[str, Any],
    job_id: str,
    operation: str,
    idempotency_key: str,
    file_digest: str | None = None,
) -> tuple[dict[str, Any], bool]:
    ignored_fields = {"effective_context", "hotword_lists", "hotword_term_count"}
    if request_data.get("model") == DEFAULT_ASR_MODEL_ID:
        ignored_fields.add("model")
    if not request_data.get("hotword_list_ids"):
        ignored_fields.add("hotword_list_ids")
    if operation == "upload_voiceprint_sample":
        ignored_fields.add("voiceprint_sample_id")
    if operation == "submit_tts_sequence":
        ignored_fields.add("voiceprint_references")
    try:
        return create_job_idempotent(
            kind, display_name, request_data, job_id, operation,
            idempotency_key_hash(idempotency_key), request_fingerprint(
                request_data, file_digest,
                ignored_fields,
            ),
        )
    except IdempotencyConflict as exc:
        shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
        raise ApiProblem(409, "idempotency_key_conflict", str(exc)) from exc


def public_job(job: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    internal = {
        "request", "queue_seq", "stage_code", "stage_current", "stage_total",
        "input_duration_seconds", "progress_basis", "stage_progress", "stage_unit",
        "progress_activity",
    }
    result = {key: value for key, value in job.items() if key not in internal}
    as_of = (
        job.get("heartbeat_at") or job.get("updated_at") or job.get("started_at")
        or job.get("finished_at") or job.get("created_at") or utcnow()
    )
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
    context = context or queue_context(include_history=job.get("state") in {"queued", "running"})
    capacities = {"asr": settings.max_queued_asr, "tts": settings.max_queued_tts}
    result["queue"] = queue_for_job(job, context, capacities)
    result["progress_detail"] = stage_details(job)
    result["estimate"] = estimate_for_job(job, context)
    result["poll_after_seconds"] = 3 if job.get("state") == "queued" else 1 if job.get("state") == "running" else None
    return result


def public_job_summary(job: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    result = public_job(job, context)
    result.pop("request", None)
    result.pop("result", None)
    return result


def public_jobs(items: list[dict[str, Any]], *, summary: bool = False) -> list[dict[str, Any]]:
    context = queue_context(include_history=any(item.get("state") in {"queued", "running"} for item in items))
    render = public_job_summary if summary else public_job
    return [render(item, context) for item in items]


def event_semantic_key(snapshot: dict[str, Any]) -> dict[str, Any]:
    ignored_job_fields = {"heartbeat_at", "processing_seconds", "processing_as_of"}
    jobs = [
        {key: value for key, value in job.items() if key not in ignored_job_fields}
        for job in snapshot.get("jobs", [])
    ]
    workers = [
        {key: value for key, value in worker.items() if key != "heartbeat_at"}
        for worker in snapshot.get("workers", [])
    ]
    return {"jobs": jobs, "workers": workers}


def event_delta(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
    previous_jobs = {item["id"]: item for item in previous.get("jobs", [])}
    current_jobs = {item["id"]: item for item in current.get("jobs", [])}
    changed = [
        item for item in current.get("jobs", [])
        if event_semantic_key({"jobs": [previous_jobs.get(item["id"], {})]})["jobs"]
        != event_semantic_key({"jobs": [item]})["jobs"]
    ]
    removed = [job_id for job_id in previous_jobs if job_id not in current_jobs]
    workers_changed = event_semantic_key({"workers": previous.get("workers", [])})["workers"] != event_semantic_key({"workers": current.get("workers", [])})["workers"]
    if not changed and not removed and not workers_changed:
        return None
    return {"jobs": changed, "removed_job_ids": removed, "workers": current.get("workers", [])}


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
        "id": person["id"], "name": person["name"], "note": person.get("note"),
        "include_in_hotword_library": bool(person.get("include_in_hotword_library", True)),
        "created_at": person["created_at"],
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
    if settings.deployment_profile == "cpu":
        raise ApiProblem(
            503, "gpu_runtime_not_installed",
            "GPU compute is unavailable because this deployment uses CPU-only inference runtimes; reinstall with --profile full to enable GPU compute",
        )
    snapshot = cached_gpu_snapshot(0, probe=gpu_snapshot)
    if snapshot is None:
        raise HTTPException(status_code=503, detail="GPU compute is unavailable; select CPU or check NVIDIA runtime")
    return normalized, str(snapshot["name"])


def _asr_model_devices(model: dict[str, Any]) -> list[dict[str, Any]]:
    installation = model_installation(settings.models_dir, model)
    installed = bool(installation["installed"] or settings.mock_mode)
    snapshot = gpu_snapshot(0)
    minimum = int(model.get("minimum_gpu_memory_mib") or 0)
    total = int(snapshot["memory_total_mib"]) if snapshot is not None else None
    gpu_runtime_installed = settings.deployment_profile == "full"
    gpu_available = bool(installed and gpu_runtime_installed and snapshot is not None and total is not None and total >= minimum)
    if not installed:
        reason_code, reason = "model_not_installed", "Model files are not installed at the pinned revision"
    elif not gpu_runtime_installed:
        reason_code, reason = "gpu_runtime_not_installed", "GPU inference runtimes are not installed in the CPU-only deployment profile"
    elif snapshot is None:
        reason_code, reason = "gpu_unavailable", "No compatible NVIDIA GPU is available"
    elif total is not None and total < minimum:
        reason_code = "insufficient_gpu_memory"
        reason = f"This model requires at least {minimum} MiB total GPU memory; detected {total} MiB"
    else:
        reason_code = reason = None
    return [
        {
            "id": "cpu", "precision": "FP32", "available": installed,
            "default": installed and not gpu_available, "quantized": False,
            "unavailable_reason_code": None if installed else "model_not_installed",
            "unavailable_reason": None if installed else "Model files are not installed at the pinned revision",
        },
        {
            "id": "gpu", "precision": "BF16", "available": gpu_available,
            "default": gpu_available, "quantized": False,
            "minimum_memory_mib": minimum, "total_memory_mib": total,
            "unavailable_reason_code": reason_code, "unavailable_reason": reason,
        },
    ]


def asr_model_capabilities() -> list[dict[str, Any]]:
    result = []
    for model in asr_models():
        installation = model_installation(settings.models_dir, model)
        result.append({
            "id": model["public_id"], "name": model["name"],
            "revision": model["revision"],
            "installed": bool(installation["installed"] or settings.mock_mode),
            "installation_state": "installed" if settings.mock_mode else installation["state"],
            "default": bool(model.get("default")),
            "compute_devices": _asr_model_devices(model),
        })
    return result


def validate_asr_model_device(identifier: str, value: str) -> tuple[dict[str, Any], str, str]:
    model = resolve_asr_model(identifier)
    if model is None:
        raise ApiProblem(422, "unknown_asr_model", "Unknown ASR model")
    normalized = value.strip().lower()
    if normalized not in COMPUTE_DEVICES:
        raise HTTPException(status_code=422, detail="compute_device must be cpu or gpu")
    installation = model_installation(settings.models_dir, model)
    if not installation["installed"] and not settings.mock_mode:
        raise ApiProblem(503, "asr_model_unavailable", "The selected ASR model is not installed at the pinned revision")
    if normalized == "cpu":
        return model, normalized, "CPU"
    if settings.deployment_profile == "cpu":
        raise ApiProblem(
            503, "gpu_runtime_not_installed",
            "GPU compute is unavailable because this deployment uses CPU-only inference runtimes; reinstall with --profile full to enable GPU compute",
        )
    snapshot = cached_gpu_snapshot(0, probe=gpu_snapshot)
    if snapshot is None:
        raise ApiProblem(503, "gpu_unavailable", "GPU compute is unavailable; select CPU or check NVIDIA runtime")
    minimum = int(model.get("minimum_gpu_memory_mib") or 0)
    total = int(snapshot["memory_total_mib"])
    if total < minimum:
        raise ApiProblem(
            503, "insufficient_gpu_memory",
            f"{model['name']} requires at least {minimum} MiB total GPU memory; detected {total} MiB",
        )
    return model, normalized, str(snapshot["name"])


def tts_model_controls(model: dict[str, Any]) -> dict[str, Any]:
    instruction_modes = ["preset", "voice_design"] if model["public_id"] == "qwen3-tts-1.7b" else []
    return {
        "instruction_voice_modes": instruction_modes,
        "instruction_required_voice_modes": ["voice_design"] if "voice_design" in instruction_modes else [],
        "max_instruction_chars": MAX_TTS_INSTRUCTION_CHARS,
        "speaking_rate_parameter": False,
        "pitch_parameter": False,
        "sampling_parameters": False,
    }


def tts_model_voice_modes(model: dict[str, Any]) -> list[str]:
    variants = model["checkpoints"]
    modes: list[str] = []
    if "custom_voice" in variants:
        modes.append("preset")
    if "base" in variants:
        modes.extend(["profile", "inline_clone", "voiceprint"])
    if "voice_design" in variants:
        modes.append("voice_design")
    return modes


def _tts_model_installations(model: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    return [
        (variant, checkpoint, model_installation(settings.models_dir, checkpoint))
        for variant, checkpoint in model["checkpoints"].items()
    ]


def _tts_model_devices(model: dict[str, Any]) -> list[dict[str, Any]]:
    installations = _tts_model_installations(model)
    installed = bool(settings.mock_mode or all(item[2]["installed"] for item in installations))
    snapshot = gpu_snapshot(0)
    minimum = int(model.get("minimum_gpu_memory_mib") or 0)
    total = int(snapshot["memory_total_mib"]) if snapshot is not None else None
    gpu_runtime_installed = settings.deployment_profile == "full"
    gpu_available = bool(installed and gpu_runtime_installed and snapshot is not None and total is not None and total >= minimum)
    if not installed:
        reason_code, reason = "model_not_installed", "Model files are not installed at the pinned revisions"
    elif not gpu_runtime_installed:
        reason_code, reason = "gpu_runtime_not_installed", "GPU inference runtimes are not installed in the CPU-only deployment profile"
    elif snapshot is None:
        reason_code, reason = "gpu_unavailable", "No compatible NVIDIA GPU is available"
    elif total is not None and total < minimum:
        reason_code = "insufficient_gpu_memory"
        reason = f"This model requires at least {minimum} MiB total GPU memory; detected {total} MiB"
    else:
        reason_code = reason = None
    return [
        {
            "id": "cpu", "precision": "FP32", "available": installed,
            "default": installed and not gpu_available, "quantized": False,
            "unavailable_reason_code": None if installed else "model_not_installed",
            "unavailable_reason": None if installed else "Model files are not installed at the pinned revisions",
        },
        {
            "id": "gpu", "precision": "BF16", "available": gpu_available,
            "default": gpu_available, "quantized": False,
            "minimum_memory_mib": minimum, "total_memory_mib": total,
            "unavailable_reason_code": reason_code, "unavailable_reason": reason,
        },
    ]


def tts_model_capabilities() -> list[dict[str, Any]]:
    result = []
    for model in tts_models():
        installations = _tts_model_installations(model)
        checkpoints = [{
            "variant": variant, "name": checkpoint["name"], "revision": checkpoint["revision"],
            "installed": bool(installation["installed"] or settings.mock_mode),
            "installation_state": "installed" if settings.mock_mode else installation["state"],
        } for variant, checkpoint, installation in installations]
        installed_count = sum(1 for item in checkpoints if item["installed"])
        aggregate_state = "installed" if installed_count == len(checkpoints) else "missing" if not installed_count else "partial"
        result.append({
            "id": model["public_id"], "name": model["name"], "default": model["default"],
            "installed": installed_count == len(checkpoints), "installation_state": aggregate_state,
            "voice_modes": tts_model_voice_modes(model), "compute_devices": _tts_model_devices(model),
            "controls": tts_model_controls(model), "checkpoints": checkpoints,
        })
    return result


def validate_tts_model_device(
    identifier: str, voice_mode: str, value: str,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    model = resolve_tts_model(identifier)
    if model is None:
        raise ApiProblem(422, "unknown_tts_model", "Unknown TTS model")
    checkpoint = resolve_tts_checkpoint(model, voice_mode)
    if checkpoint is None:
        raise ApiProblem(422, "unsupported_tts_voice_mode", "The selected TTS model does not support this voice mode")
    normalized = value.strip().lower()
    if normalized not in COMPUTE_DEVICES:
        raise HTTPException(status_code=422, detail="compute_device must be cpu or gpu")
    installation = model_installation(settings.models_dir, checkpoint)
    if not installation["installed"] and not settings.mock_mode:
        raise ApiProblem(503, "tts_model_unavailable", "The selected TTS checkpoint is not installed at the pinned revision")
    if normalized == "cpu":
        return model, checkpoint, normalized, "CPU"
    if settings.deployment_profile == "cpu":
        raise ApiProblem(
            503, "gpu_runtime_not_installed",
            "GPU compute is unavailable because this deployment uses CPU-only inference runtimes; reinstall with --profile full to enable GPU compute",
        )
    snapshot = cached_gpu_snapshot(0, probe=gpu_snapshot)
    if snapshot is None:
        raise ApiProblem(503, "gpu_unavailable", "GPU compute is unavailable; select CPU or check NVIDIA runtime")
    minimum = int(model.get("minimum_gpu_memory_mib") or 0)
    total = int(snapshot["memory_total_mib"])
    if total < minimum:
        raise ApiProblem(
            503, "insufficient_gpu_memory",
            f"{model['name']} requires at least {minimum} MiB total GPU memory; detected {total} MiB",
        )
    return model, checkpoint, normalized, str(snapshot["name"])


def validate_tts_instruction(model: dict[str, Any], voice_mode: str, value: str) -> str:
    instruction = value.strip()
    if len(instruction) > MAX_TTS_INSTRUCTION_CHARS:
        raise ApiProblem(422, "invalid_tts_instruction", f"instruct must not exceed {MAX_TTS_INSTRUCTION_CHARS} characters")
    controls = tts_model_controls(model)
    supported = voice_mode in controls["instruction_voice_modes"]
    required = voice_mode in controls["instruction_required_voice_modes"]
    if instruction and not supported:
        raise ApiProblem(422, "unsupported_tts_control", "Natural-language instructions are not supported by this model and voice mode")
    if required and not instruction:
        raise ApiProblem(422, "tts_instruction_required", "A voice-design instruction is required")
    return instruction


def public_hotword_list(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"], "name": item["name"], "kind": item.get("kind", "custom"),
        "terms": item["terms"],
        "term_count": len(item["terms"]), "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def hotword_request_data(
    context: str,
    raw_ids: str | None,
    existing_job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        ids = parse_hotword_list_ids(raw_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if existing_job is not None:
        stored = existing_job.get("request") or {}
        return {
            "hotword_list_ids": ids,
            "hotword_lists": stored.get("hotword_lists") or [],
            "hotword_term_count": int(stored.get("hotword_term_count") or 0),
            "effective_context": str(stored.get("effective_context") or context.strip()),
        }
    selected = []
    missing = []
    for item_id in ids:
        item = get_hotword_list(item_id)
        if item is None:
            missing.append(item_id)
        else:
            selected.append(item)
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown hotword list IDs: {', '.join(missing)}")
    if any(item.get("kind") == "system" and not item.get("terms") for item in selected):
        raise HTTPException(status_code=422, detail="The selected system hotword list is empty")
    try:
        effective, term_count = compile_hotword_context(context, selected)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ordered = sorted(selected, key=lambda item: (item["name_key"], item["id"]))
    return {
        "hotword_list_ids": [item["id"] for item in ordered],
        "hotword_lists": [
            {
                "id": item["id"], "name": item["name"], "terms": item["terms"],
                "updated_at": item["updated_at"],
            }
            for item in ordered
        ],
        "hotword_term_count": term_count,
        "effective_context": effective,
    }


def validate_tts_language(value: Any, field: str = "language") -> str:
    normalized = str(value or "").strip().lower()
    language = TTS_LANGUAGE_BY_KEY.get(normalized)
    if language is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be one of {', '.join(TTS_LANGUAGES)}",
        )
    return language


def validate_asr_language(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    language = ASR_LANGUAGE_BY_KEY.get(normalized)
    if language is None:
        raise HTTPException(
            status_code=422,
            detail=f"language must be one of {', '.join(ASR_LANGUAGES)}",
        )
    return language


def validate_reference_language(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    language = REFERENCE_LANGUAGE_BY_KEY.get(normalized)
    if language is None:
        raise HTTPException(
            status_code=422,
            detail=f"reference_language must be one of {', '.join(REFERENCE_LANGUAGE_BY_KEY.values())}",
        )
    return language


def compute_capabilities(default: str) -> list[dict[str, Any]]:
    gpu_runtime_installed = settings.deployment_profile == "full"
    return [
        {"id": "cpu", "precision": "FP32", "available": True, "default": default == "cpu", "quantized": False},
        {
            "id": "gpu", "precision": "BF16", "available": gpu_runtime_installed and gpu_snapshot() is not None,
            "default": default == "gpu", "quantized": False,
            "unavailable_reason_code": None if gpu_runtime_installed else "gpu_runtime_not_installed",
            "unavailable_reason": None if gpu_runtime_installed else "GPU inference runtimes are not installed in the CPU-only deployment profile",
        },
    ]


def validate_boolean(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    raise HTTPException(status_code=422, detail=f"{field} must be a boolean")


def create_app() -> FastAPI:
    settings.ensure_directories()
    init_db()
    default_device = settings.default_compute_device
    app = FastAPI(
        title="Sandevistan-Audio",
        description=API_DESCRIPTION,
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_tags=OPENAPI_TAGS,
    )
    app.state.auth_sessions = set()
    app.state.admission = AdmissionController(
        settings.data_dir,
        {"asr": settings.max_queued_asr, "tts": settings.max_queued_tts},
        settings.max_concurrent_submissions,
        settings.min_free_disk_bytes,
    )
    app.state.event_hub = SnapshotHub(lambda: {
        "jobs": public_jobs(list_jobs(limit=EVENT_SNAPSHOT_JOB_LIMIT), summary=True),
        "workers": list_workers(),
    }, poll_seconds=EVENT_SNAPSHOT_POLL_SECONDS, semantic_key=event_semantic_key,
       revision_loader=lambda: event_revision(EVENT_SNAPSHOT_JOB_LIMIT))

    @app.middleware("http")
    async def submission_admission(request: Request, call_next: Any) -> Response:
        route = _submission_route(request.url.path) if request.method == "POST" else None
        if route is None or not _admission_authenticated(request):
            return await call_next(request)
        kind, operation, has_large_upload, key_required = route
        raw_key = request.headers.get("idempotency-key")
        try:
            key = validate_idempotency_key(raw_key) if key_required or raw_key is not None else None
        except ApiProblem as exc:
            return _problem_response(exc)
        replay = await run_in_threadpool(
            find_idempotent_job, operation, idempotency_key_hash(key),
        ) if key is not None else None
        reserved = False
        started = time.monotonic()
        if replay is None:
            raw_length = request.headers.get("content-length")
            try:
                expected_bytes = max(0, int(raw_length)) if raw_length is not None else (
                    settings.max_upload_bytes if has_large_upload else 0
                )
            except ValueError:
                expected_bytes = settings.max_upload_bytes if has_large_upload else 0
            decision = await app.state.admission.reserve(kind, expected_bytes)
            if not decision.accepted:
                return _problem_response(ApiProblem(
                    429, decision.code or "queue_capacity_reached",
                    decision.detail or "Submission capacity is unavailable",
                    headers={"Retry-After": str(decision.retry_after_seconds)},
                    extras={
                        "retry_after_seconds": decision.retry_after_seconds,
                        "queue": {
                            "kind": kind, "depth": decision.queue_depth,
                            "capacity": decision.queue_capacity,
                        },
                        "storage": {
                            "free_bytes": decision.free_bytes,
                            "minimum_free_bytes": decision.minimum_free_bytes,
                        },
                    },
                ))
            reserved = True
        try:
            response = await call_next(request)
        finally:
            if reserved:
                await app.state.admission.release(kind)
        response.headers["Server-Timing"] = f"total;dur={(time.monotonic() - started) * 1000:.1f}"
        return response
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
                "docExpansion": "none", "defaultModelsExpandDepth": 1,
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
        if isinstance(exc, ApiProblem):
            return _problem_response(exc)
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

    def tls_ca_bytes() -> tuple[bytes, bytes] | None:
        path = settings.tls_ca_file
        if settings.protocol != "https" or path is None or not path.is_file():
            return None
        try:
            pem = path.read_bytes()
            der = ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))
            return pem, der
        except (OSError, UnicodeError, ValueError):
            return None

    @app.get(
        "/api/v1/tls/bootstrap", response_model=TlsBootstrapResponse, response_model_exclude_unset=True,
        tags=[SERVICE_TAG], summary="读取 HTTPS 信任引导 / Get HTTPS trust bootstrap",
        description=bilingual(
            "公开返回当前协议、可安装根证书的下载地址与 SHA-256 指纹；绝不返回私钥。",
            "Publicly return the active protocol, installable root certificate URLs, and its SHA-256 fingerprint; private keys are never exposed.",
        ),
        operation_id="getTlsBootstrap",
    )
    def tls_bootstrap() -> TlsBootstrapResponse:
        pair = tls_ca_bytes()
        if pair is None:
            return {"protocol": "https" if settings.protocol == "https" else "http", "ca_installation_available": False}
        fingerprint = hashlib.sha256(pair[1]).hexdigest().upper()
        formatted = ":".join(fingerprint[index:index + 2] for index in range(0, len(fingerprint), 2))
        return {
            "protocol": "https", "ca_installation_available": True,
            "ca_sha256_fingerprint": formatted,
            "ca_download_urls": {"cer": "/api/v1/tls/root-ca.cer", "pem": "/api/v1/tls/root-ca.pem"},
        }

    @app.get(
        "/api/v1/tls/root-ca.cer", tags=[SERVICE_TAG], summary="下载 DER 根证书 / Download DER root CA",
        description=bilingual("公开下载用于 Windows 和 iOS 安装的 DER 根证书。", "Publicly download the DER root CA for Windows and iOS installation."),
        operation_id="downloadTlsRootCaDer",
    )
    def tls_root_ca_cer() -> Response:
        pair = tls_ca_bytes()
        if pair is None:
            raise HTTPException(status_code=404, detail="TLS root CA is not available")
        return Response(pair[1], media_type="application/pkix-cert", headers={
            "Content-Disposition": 'attachment; filename="sandevistan-audio-root-ca.cer"', "Cache-Control": "no-store",
        })

    @app.get(
        "/api/v1/tls/root-ca.pem", tags=[SERVICE_TAG], summary="下载 PEM 根证书 / Download PEM root CA",
        description=bilingual("公开下载 PEM 格式根证书。", "Publicly download the root CA in PEM format."),
        operation_id="downloadTlsRootCaPem",
    )
    def tls_root_ca_pem() -> Response:
        pair = tls_ca_bytes()
        if pair is None:
            raise HTTPException(status_code=404, detail="TLS root CA is not available")
        return Response(pair[0], media_type="application/x-pem-file", headers={
            "Content-Disposition": 'attachment; filename="sandevistan-audio-root-ca.pem"', "Cache-Control": "no-store",
        })

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
        description=bilingual(
            "返回 worker、硬件、模型 revision 和本地存储路径；GPU 快照区分设备范围当前已用、当前空闲和按 total-used-free 计算的系统保留估算；始终受保护。",
            "Return workers, hardware, model revisions, and local storage paths; the GPU snapshot distinguishes device-wide used and free memory from the estimated system-reserved residual calculated as total-used-free; always protected.",
        ),
        operation_id="getSystem", responses={**AUTH_RESPONSES},
    )
    def system(_: None = Depends(require_api_key)) -> SystemResponse:
        return system_snapshot()

    @app.get(
        "/api/v1/capabilities", response_model=CapabilitiesResponse, response_model_exclude_unset=True,
        tags=[SERVICE_TAG], summary="读取服务能力 / Get service capabilities",
        description=bilingual(
            "消费方应从这里读取设备可用性、格式、上限和默认值，不要硬编码部署能力。ASR 设备能力按模型位于 `asr.models[].compute_devices`；TTS 的权威模型级能力位于 `tts.model_capabilities[]`。显存字段均为总显存口径。两个顶层 `compute_devices` 以及 `tts.controls` 只是默认 0.6B 模型的兼容视图。",
            "Read live device availability, formats, limits, and defaults here instead of hard-coding deployment capabilities. ASR device eligibility is model-scoped under `asr.models[].compute_devices`; authoritative TTS model behavior is under `tts.model_capabilities[]`. Memory fields use reported total GPU memory. Both top-level `compute_devices` fields and `tts.controls` are compatibility views for the default 0.6B models.",
        ),
        operation_id="getCapabilities", responses={**AUTH_RESPONSES},
    )
    def capabilities(_: None = Depends(require_api_key)) -> CapabilitiesResponse:
        model_capabilities = asr_model_capabilities()
        default_model = default_asr_model()
        default_capability = next(item for item in model_capabilities if item["default"])
        tts_capabilities = tts_model_capabilities()
        default_tts_capability = next(item for item in tts_capabilities if item["default"])
        return {
            "services": sorted(settings.enabled_services),
            "offline": True,
            "deployment": deployment_metadata(settings.deployment_profile),
            "asr": {
                "model": default_model["name"],
                "default_model": default_model["public_id"],
                "models": model_capabilities,
                "diarization": "CAM++ single-active-speaker",
                "speaker_count": {"min": 1, "max": 15, "default": "auto"},
                "voiceprint_library": True,
                "languages": ASR_LANGUAGES,
                "default_language": "Auto",
                "timestamp_precisions": ["segment", "word_or_character"],
                "aligner_languages": ALIGNER_LANGUAGES,
                "exports": ["json", "srt", "vtt", "txt"],
                "compute_devices": default_capability["compute_devices"],
                "single_task_acceleration": {"supported": True, "default": SINGLE_TASK_ACCELERATION_DEFAULT},
                "hotword_library": {
                    "supported": True,
                    "max_lists": MAX_HOTWORD_LISTS,
                    "max_terms_per_list": MAX_TERMS_PER_LIST,
                    "max_selected_lists": MAX_SELECTED_LISTS,
                    "max_selected_terms": MAX_SELECTED_TERMS,
                    "max_prompt_chars": MAX_HOTWORD_PROMPT_CHARS,
                    "max_name_chars": MAX_HOTWORD_NAME_CHARS,
                    "max_term_chars": MAX_HOTWORD_TERM_CHARS,
                },
            },
            "tts": {
                "models": [
                    checkpoint["name"]
                    for model in tts_capabilities for checkpoint in model["checkpoints"]
                ],
                "default_model": DEFAULT_TTS_MODEL_ID,
                "model_capabilities": tts_capabilities,
                "voice_modes": ["preset", "profile", "inline_clone", "voiceprint", "voice_design"],
                "preset_speakers": PRESET_SPEAKERS,
                "preset_speaker_native_languages": PRESET_SPEAKER_NATIVE_LANGUAGES,
                "languages": TTS_LANGUAGES,
                "default_language": "Auto",
                "formats": ["wav", "flac", "mp3"],
                "compute_devices": default_tts_capability["compute_devices"],
                "single_task_acceleration": {"supported": True, "default": SINGLE_TASK_ACCELERATION_DEFAULT},
                "sequence_jobs": {
                    "supported": True,
                    "contract_version": 1,
                    "endpoint": "/api/v1/tts/sequence-jobs",
                    "voice_modes": ["preset", "voiceprint"],
                    "artifact_mode": "per_item",
                    "format": "wav",
                    "max_items": 100,
                    "max_total_chars": settings.max_tts_chars,
                },
                "controls": default_tts_capability["controls"],
            },
            "limits": {
                "max_upload_bytes": settings.max_upload_bytes,
                "max_tts_chars": settings.max_tts_chars,
                "max_clone_reference_seconds": 15,
                "max_queued_asr": settings.max_queued_asr,
                "max_queued_tts": settings.max_queued_tts,
                "max_concurrent_submissions": settings.max_concurrent_submissions,
                "min_free_disk_bytes": settings.min_free_disk_bytes,
            },
            "events": {
                "sse": True, "global_url": "/api/v1/events",
                "per_job_url_template": "/api/v1/jobs/{job_id}/events",
                "heartbeat_seconds": EVENT_HEARTBEAT_SECONDS, "history_replay": False,
                "global_mode": "summary_delta",
            },
        }

    @app.get(
        "/api/v1/asr/hotword-lists", response_model=HotwordListsResponse,
        response_model_exclude_unset=True, tags=[ASR_TAG],
        summary="读取 ASR 热词库 / List ASR hotword lists",
        description=bilingual(
            "列出本地词表，包括只读系统词表“声纹库人名（全名）”和“声纹库人名（去姓）”。提交 ASR 时仍需通过 `hotword_list_ids` 显式选择；选择顺序不表示识别权重。",
            "List local vocabularies, including the read-only full-name and surname-free voiceprint system lists. Select every list explicitly through `hotword_list_ids`; selection order does not imply recognition weight.",
        ),
        operation_id="listAsrHotwordLists", responses={**AUTH_RESPONSES},
    )
    def hotword_lists(_: None = Depends(require_api_key)) -> dict[str, Any]:
        items = [public_hotword_list(item) for item in list_hotword_lists()]
        return {"items": items, "count": len(items)}

    @app.post(
        "/api/v1/asr/hotword-lists", status_code=201,
        response_model=HotwordListResponse, response_model_exclude_unset=True,
        tags=[ASR_TAG], summary="创建 ASR 热词词表 / Create an ASR hotword list",
        description=bilingual(
            "创建名称唯一的自定义热词词表。名称在 NFKC、空白折叠和大小写无关规范化后唯一；声纹人名系统词表的现行及旧名称均为保留名称。空词条会移除，等价重复词保留首次形式。",
            "Create a uniquely named custom hotword list. Names are unique after NFKC normalization, whitespace folding, and case folding; current and legacy voiceprint-system list names are reserved. Empty terms are removed and equivalent duplicates preserve their first display form.",
        ),
        operation_id="createAsrHotwordList",
        responses={**AUTH_RESPONSES, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def add_hotword_list(
        payload: HotwordListCreateRequest, _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        try:
            item = create_hotword_list(payload.name, payload.terms)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (sqlite3.IntegrityError, OverflowError) as exc:
            detail = "A hotword list with this name already exists" if isinstance(
                exc, sqlite3.IntegrityError
            ) else str(exc)
            raise ApiProblem(409, "hotword_list_conflict", detail) from exc
        return public_hotword_list(item)

    @app.patch(
        "/api/v1/asr/hotword-lists/{item_id}", response_model=HotwordListResponse,
        response_model_exclude_unset=True, tags=[ASR_TAG],
        summary="更新 ASR 热词词表 / Update an ASR hotword list",
        description=bilingual(
            "仅自定义词表可更新；至少提供 `name` 或 `terms`。系统词表返回 `403`。已提交任务保存不可变快照。",
            "Only custom lists can be updated; provide `name` or `terms`. System lists return `403`. Submitted jobs keep immutable snapshots.",
        ),
        operation_id="updateAsrHotwordList",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def edit_hotword_list(
        item_id: str, payload: HotwordListUpdateRequest,
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        try:
            item = update_hotword_list(item_id, name=payload.name, terms=payload.terms)
        except ReadOnlyHotwordListError as exc:
            raise ApiProblem(403, "system_hotword_list_read_only", str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise ApiProblem(409, "hotword_list_conflict", "A hotword list with this name already exists") from exc
        if item is None:
            raise HTTPException(status_code=404, detail="Hotword list not found")
        return public_hotword_list(item)

    @app.delete(
        "/api/v1/asr/hotword-lists/{item_id}", status_code=204, tags=[ASR_TAG],
        summary="删除 ASR 热词词表 / Delete an ASR hotword list",
        description=bilingual("删除自定义词表；系统词表返回 `403`。已提交任务保留不可变快照。", "Delete a custom list; system lists return `403`. Submitted jobs retain immutable snapshots."),
        operation_id="deleteAsrHotwordList",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE},
    )
    def remove_hotword_list(item_id: str, _: None = Depends(require_api_key)) -> Response:
        try:
            deleted = delete_hotword_list(item_id)
        except ReadOnlyHotwordListError as exc:
            raise ApiProblem(403, "system_hotword_list_read_only", str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Hotword list not found")
        return Response(status_code=204)

    @app.get(
        "/api/v1/queue", response_model=QueueResponse, response_model_exclude_unset=True,
        tags=[JOB_TAG], summary="读取队列容量 / Get queue capacity",
        description=bilingual(
            "返回本机 ASR/TTS 排队、准入预留和磁盘余量。结果仅用于预检，提交接口的原子准入结果具有最终效力。",
            "Return local ASR/TTS queue, admission reservations, and disk headroom. This is advisory; POST admission remains authoritative.",
        ),
        operation_id="getQueue", responses={**AUTH_RESPONSES},
    )
    async def queue_status(_: None = Depends(require_api_key)) -> QueueResponse:
        snapshot = await app.state.admission.snapshot()
        context = await run_in_threadpool(queue_context, snapshot["reserved"], False)
        items = []
        for kind in ("asr", "tts"):
            depth = snapshot["counts"][kind]
            reserved = snapshot["reserved"][kind]
            capacity = snapshot["capacities"][kind]
            accepting = (
                depth + reserved < capacity
                and snapshot["active"] < snapshot["max_concurrent"]
                and snapshot["free_bytes"] >= snapshot["minimum_free_bytes"]
            )
            items.append({
                "kind": kind, "queued": depth,
                "running": sum(
                    1 for job in context["jobs"]
                    if job["kind"] == kind and job["state"] == "running"
                ),
                "reserved": reserved, "capacity": capacity, "accepting": accepting,
                "retry_after_seconds": None if accepting else 30,
            })
        return {
            "items": items,
            "active_submissions": snapshot["active"],
            "max_concurrent_submissions": snapshot["max_concurrent"],
            "storage": {
                "free_bytes": snapshot["free_bytes"],
                "minimum_free_bytes": snapshot["minimum_free_bytes"],
            },
        }

    @app.post(
        "/api/v1/asr/jobs", status_code=202, response_model=JobResponse,
        response_model_exclude_unset=True, tags=[ASR_TAG],
        summary="提交异步 ASR 任务 / Submit asynchronous ASR job",
        description=bilingual(
            "上传音频并立即返回排队任务。`model` 默认 0.6B，也可选择 1.7B；两个模型都支持 `context` 和可选热词库。词表会在提交时生成不可变快照，留空 `hotword_list_ids` 表示不使用已保存词表。通过 `status_url` 轮询；成功后读取 `result_url`。GPU 不可用时返回 503，不回退 CPU。",
            "Upload audio and immediately receive a queued job. `model` defaults to 0.6B and may select 1.7B; both support `context` and the optional hotword library. Selected lists are snapshotted at submission, while an empty `hotword_list_ids` disables stored lists. Poll `status_url`, then read `result_url`. An unavailable GPU returns 503 and never falls back silently.",
        ),
        operation_id="submitAsrJob",
        responses={**idempotency_replay_response("JobResponse"), **AUTH_RESPONSES, **IDEMPOTENCY_RESPONSES, **ADMISSION_RESPONSE, **TOO_LARGE_RESPONSE, **ASR_VALIDATION_RESPONSE, **ASR_SERVICE_RESPONSE},
    )
    async def submit_asr(
        response: Response,
        file: UploadFile = File(..., description="待转写音频或视频 / Audio or video to transcribe"),
        model: str = Form(DEFAULT_ASR_MODEL_ID, description="ASR 模型 ID / ASR model ID", json_schema_extra={"enum": [item["public_id"] for item in asr_models()]}),
        language: str = Form("Auto", description="识别语言；Auto 可检测其他语种，但只有公开清单支持字词对齐 / Recognition language; Auto may detect other languages, while only the public list supports word alignment", json_schema_extra={"enum": ASR_LANGUAGES}),
        speaker_count: str = Form("auto", description="auto 或 1–15 / auto or an integer from 1 to 15", json_schema_extra={"enum": ["auto", *[str(value) for value in range(1, 16)]]}),
        diarize: bool = Form(True, description="启用说话人分离 / Enable speaker diarization"),
        align: bool = Form(True, description="返回支持语言的精确时间戳 / Produce precise timestamps for supported languages"),
        context: str = Form("", description="一次性识别上下文；所选词表的 Vocabulary 段会追加在其后 / One-off recognition context followed by the Vocabulary section generated from selected lists"),
        hotword_list_ids: str = Form("", description="逗号分隔的本地词表 ID，最多 8 个；留空禁用已保存词表 / Comma-separated local list IDs, maximum 8; empty disables stored lists"),
        export_formats: str = Form("json,srt,vtt,txt", description="逗号分隔：json,srt,vtt,txt / Comma-separated export formats"),
        compute_device: str = Form(default_device, description="cpu 或 gpu；省略时使用部署默认值且无静默回退 / cpu or gpu; omission uses the deployment default and there is no silent fallback", json_schema_extra={"enum": ["cpu", "gpu"]}),
        use_voiceprint_library: bool = Form(True, description="用声纹库匹配并命名说话人 / Match and label speakers from the voiceprint library"),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="启用质量中性的单任务自动批处理 / Enable quality-neutral single-job auto-batching"),
        idempotency_key: str = Header(..., alias="Idempotency-Key", description=IDEMPOTENCY_KEY_DESCRIPTION, json_schema_extra=IDEMPOTENCY_KEY_SCHEMA),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("asr")
        idempotency_key = validate_idempotency_key(idempotency_key)
        language = validate_asr_language(language)
        selected_model, compute_device, compute_device_name = await run_in_threadpool(
            validate_asr_model_device, model, compute_device,
        )
        existing = await run_in_threadpool(
            find_idempotent_job, "submit_asr", idempotency_key_hash(idempotency_key),
        )
        hotword_data = await run_in_threadpool(
            hotword_request_data, context, hotword_list_ids, existing,
        )
        job_id = uuid.uuid4().hex
        original_name = safe_filename(file.filename or "audio.bin")
        input_path = settings.jobs_dir / job_id / "input" / original_name
        size, file_digest = await save_upload(file, input_path, settings.max_upload_bytes)
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
            "model": selected_model["public_id"],
            "language": language, "speaker_count": speaker_value, "diarize": diarize,
            "align": align, "context": context, "export_formats": formats, "compute_device": compute_device,
            "compute_device_name": compute_device_name, "use_voiceprint_library": use_voiceprint_library,
            "accelerate_single_task": accelerate_single_task,
            **hotword_data,
        }
        job, replayed = idempotent_job(
            "asr", original_name, request_data, job_id, "submit_asr",
            idempotency_key, file_digest,
        )
        if replayed:
            shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
            response.status_code = 200
            response.headers["Idempotency-Replayed"] = "true"
        return public_job(job)

    @app.post(
        "/api/v1/tts/clone-references", status_code=202, response_model=JobResponse,
        response_model_exclude_unset=True, tags=[TTS_TAG],
        summary="自动分析 TTS 克隆参考 / Analyze a TTS clone reference",
        description=bilingual(
            "上传单人参考音频并创建可见的 ASR 任务，自动识别参考语种、逐字文本和时间戳。可选择 0.6B 或 1.7B，但该用途不启用热词库。成功后把任务 ID 作为 `reference_job_id` 提交给 TTS。",
            "Upload clean single-speaker reference audio and create a visible ASR job that detects its language, exact transcript, and timestamps. Either 0.6B or 1.7B may be selected, but this workflow does not use the hotword library. Pass the successful job ID to TTS as `reference_job_id`.",
        ),
        operation_id="analyzeTtsCloneReference",
        responses={**idempotency_replay_response("JobResponse"), **AUTH_RESPONSES, **IDEMPOTENCY_RESPONSES, **ADMISSION_RESPONSE, **TOO_LARGE_RESPONSE, **ASR_VALIDATION_RESPONSE, **ASR_SERVICE_RESPONSE},
    )
    async def analyze_tts_clone_reference(
        response: Response,
        file: UploadFile = File(..., description="单人干净参考音频或录音容器 / Clean single-speaker audio or recording container"),
        model: str = Form(DEFAULT_ASR_MODEL_ID, description="参考转写使用的 ASR 模型 / ASR model for reference transcription", json_schema_extra={"enum": [item["public_id"] for item in asr_models()]}),
        compute_device: str = Form(default_device, description="cpu 或 gpu；省略时使用部署默认值且无静默回退 / cpu or gpu; omission uses the deployment default and there is no silent fallback", json_schema_extra={"enum": ["cpu", "gpu"]}),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="单任务自动批处理 / Single-job auto-batching"),
        idempotency_key: str = Header(..., alias="Idempotency-Key", description=IDEMPOTENCY_KEY_DESCRIPTION, json_schema_extra=IDEMPOTENCY_KEY_SCHEMA),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("tts")
        ensure_service("asr")
        idempotency_key = validate_idempotency_key(idempotency_key)
        selected_model, compute_device, compute_device_name = await run_in_threadpool(
            validate_asr_model_device, model, compute_device,
        )
        job_id = uuid.uuid4().hex
        original_name = safe_filename(file.filename or "clone-reference.bin")
        input_path = settings.jobs_dir / job_id / "input" / original_name
        size, file_digest = await save_upload(file, input_path, settings.max_upload_bytes)
        request_data = {
            "purpose": "tts_clone_reference", "input_path": str(input_path),
            "original_name": original_name, "size_bytes": size, "language": "Auto",
            "model": selected_model["public_id"],
            "speaker_count": 1, "diarize": False, "align": True, "context": "",
            "export_formats": ["json", "txt"], "compute_device": compute_device,
            "compute_device_name": compute_device_name, "use_voiceprint_library": False,
            "accelerate_single_task": accelerate_single_task,
        }
        job, replayed = idempotent_job(
            "asr", f"TTS 克隆参考分析 · {original_name}", request_data, job_id,
            "analyze_tts_clone_reference", idempotency_key, file_digest,
        )
        if replayed:
            shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
            response.status_code = 200
            response.headers["Idempotency-Replayed"] = "true"
        return public_job(job)

    @app.post(
        "/api/v1/tts/jobs", status_code=202, response_model=JobResponse,
        response_model_exclude_unset=True, tags=[TTS_TAG],
        summary="提交异步 TTS 任务 / Submit asynchronous TTS job",
        description=bilingual(
            "`model` 默认 0.6B，也可选择 1.7B。`preset` 使用 CustomVoice；三种克隆来源使用 Base；仅 1.7B 提供 `voice_design`。自然语言 `instruct` 只支持 1.7B 的 preset 和 voice_design，后者必须提供。",
            "`model` defaults to 0.6B and may select 1.7B. `preset` uses CustomVoice, the three clone sources use Base, and only 1.7B provides `voice_design`. Natural-language `instruct` is supported only by 1.7B preset and voice_design, and is required for the latter.",
        ),
        operation_id="submitTtsJob",
        responses={**idempotency_replay_response("JobResponse"), **AUTH_RESPONSES, **IDEMPOTENCY_RESPONSES, **ADMISSION_RESPONSE, **TOO_LARGE_RESPONSE, **TTS_CONTROL_VALIDATION_RESPONSE, **TTS_SERVICE_RESPONSE},
    )
    async def submit_tts(
        response: Response,
        text: str = Form(..., description="需要合成的文本 / Text to synthesize", json_schema_extra={"minLength": 1, "maxLength": settings.max_tts_chars}),
        model: str = Form(DEFAULT_TTS_MODEL_ID, description="TTS 模型 ID / TTS model ID", json_schema_extra={"enum": [item["public_id"] for item in tts_models()]}),
        language: str = Form("Auto", description="输出文本语种；已知时应显式指定 / Target text language; specify it when known", json_schema_extra={"enum": TTS_LANGUAGES}),
        voice_mode: str = Form("preset", description="preset、profile、inline_clone、voiceprint 或 voice_design", json_schema_extra={"enum": ["preset", "profile", "inline_clone", "voiceprint", "voice_design"]}),
        speaker: str | None = Form(None, description="preset 模式的官方音色 / Official speaker for preset mode"),
        voice_profile_id: str | None = Form(None, description="profile 模式的声音档案 ID / Voice profile ID for profile mode"),
        voiceprint_sample_id: str | None = Form(None, description="voiceprint 模式的具体可用样本 ID / Concrete eligible sample ID for voiceprint mode"),
        reference_audio: UploadFile | None = File(None, description="inline_clone 模式参考音频 / Reference audio for inline_clone"),
        reference_text: str | None = Form(None, description="必须与参考音频逐字一致 / Must exactly match the reference audio"),
        reference_job_id: str | None = Form(None, description="自动参考分析成功后的 ASR 任务 ID / Successful automatic reference-analysis ASR job ID"),
        reference_language: str | None = Form(None, description="参考音频语种；省略时使用分析结果 / Reference audio language; defaults to the analysis result", json_schema_extra={"enum": ALIGNER_LANGUAGES}),
        instruct: str = Form(
            "",
            description="1.7B preset 的可选自然语言控制，voice_design 必填；其它组合必须为空 / Optional natural-language control for 1.7B preset, required for voice_design, and empty for other combinations",
            json_schema_extra={"maxLength": MAX_TTS_INSTRUCTION_CHARS},
        ),
        response_format: str = Form("wav", description="wav、flac 或 mp3", json_schema_extra={"enum": ["wav", "flac", "mp3"]}),
        display_name: str = Form("语音合成", description="任务显示名称 / Job display name"),
        compute_device: str = Form(default_device, description="cpu 或 gpu；省略时使用部署默认值且无静默回退 / cpu or gpu; omission uses the deployment default and there is no silent fallback", json_schema_extra={"enum": ["cpu", "gpu"]}),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="启用质量中性的单任务自动批处理 / Enable quality-neutral single-job auto-batching"),
        idempotency_key: str = Header(..., alias="Idempotency-Key", description=IDEMPOTENCY_KEY_DESCRIPTION, json_schema_extra=IDEMPOTENCY_KEY_SCHEMA),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("tts")
        idempotency_key = validate_idempotency_key(idempotency_key)
        language = validate_tts_language(language)
        clean_text = text.strip()
        if not clean_text or len(clean_text) > settings.max_tts_chars:
            raise HTTPException(status_code=422, detail=f"Text must contain 1-{settings.max_tts_chars} characters")
        if voice_mode not in {"preset", "profile", "inline_clone", "voiceprint", "voice_design"}:
            raise HTTPException(status_code=422, detail="voice_mode must be preset, profile, inline_clone, voiceprint or voice_design")
        if response_format not in {"wav", "flac", "mp3"}:
            raise HTTPException(status_code=422, detail="response_format must be wav, flac or mp3")
        selected_model, _, compute_device, compute_device_name = await run_in_threadpool(
            validate_tts_model_device, model, voice_mode, compute_device,
        )
        instruct = validate_tts_instruction(selected_model, voice_mode, instruct)
        request_data: dict[str, Any] = {
            "text": clean_text, "model": selected_model["public_id"],
            "language": language, "voice_mode": voice_mode,
            "speaker": speaker, "voice_profile_id": voice_profile_id, "reference_text": reference_text,
            "instruct": instruct, "response_format": response_format, "compute_device": compute_device,
            "compute_device_name": compute_device_name,
            "accelerate_single_task": accelerate_single_task,
        }
        job_id = uuid.uuid4().hex
        reference_digest: str | None = None
        if voice_mode == "preset":
            if speaker not in PRESET_SPEAKERS:
                raise HTTPException(status_code=422, detail="Unknown preset speaker")
        elif voice_mode == "voice_design":
            pass
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
            if reference_job_id and reference_audio is not None:
                raise HTTPException(status_code=422, detail="Use reference_job_id or reference_audio, not both")
            if reference_job_id:
                reference_job = get_job(reference_job_id)
                if reference_job is None:
                    raise HTTPException(status_code=422, detail="Clone reference analysis job not found")
                if (
                    reference_job.get("kind") != "asr"
                    or (reference_job.get("request") or {}).get("purpose") != "tts_clone_reference"
                    or reference_job.get("state") != "succeeded"
                ):
                    raise HTTPException(status_code=422, detail="reference_job_id must identify a successful clone reference analysis")
                reference_result = reference_job.get("result") or {}
                artifact = next(
                    (item for item in reference_result.get("artifacts", []) if item.get("name") == "reference.wav"),
                    None,
                )
                source = Path(str((artifact or {}).get("path", ""))).resolve()
                source_root = (settings.jobs_dir / reference_job_id).resolve()
                if source_root not in source.parents or not source.is_file():
                    raise HTTPException(status_code=422, detail="Clone reference audio is unavailable")
                detected_text = str(reference_result.get("text") or "").strip()
                resolved_text = str(reference_text or detected_text).strip()
                if not resolved_text:
                    raise HTTPException(status_code=422, detail="Clone reference analysis returned no transcript")
                detected_language = REFERENCE_LANGUAGE_BY_KEY.get(
                    str(reference_result.get("language") or "Auto").strip().lower(), "Auto",
                )
                resolved_reference_language = (
                    validate_reference_language(reference_language)
                    if reference_language is not None else detected_language
                )
                target = settings.jobs_dir / job_id / "input" / "analyzed-reference.wav"
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                words = [
                    {"text": word.get("text", ""), "start": word["start"], "end": word["end"]}
                    for segment in reference_result.get("segments", [])
                    for word in segment.get("words", [])
                ] if resolved_text == detected_text else []
                request_data.update({
                    "reference_job_id": reference_job_id, "reference_audio_path": str(target),
                    "reference_text": resolved_text, "reference_language": resolved_reference_language,
                    "reference_words": words, "reference_duration": reference_result.get("duration"),
                })
            else:
                if reference_audio is None or not (reference_text or "").strip():
                    raise HTTPException(status_code=422, detail="Inline cloning requires reference_job_id or reference_audio with reference_text")
                filename = safe_filename(reference_audio.filename or "reference.wav")
                target = settings.jobs_dir / job_id / "input" / filename
                _, reference_digest = await save_upload(reference_audio, target, 100 * 1024 * 1024)
                request_data.update({
                    "reference_audio_path": str(target), "reference_text": reference_text.strip(),
                    "reference_language": validate_reference_language(reference_language)
                    if reference_language is not None else language,
                })
        job, replayed = idempotent_job(
            "tts", safe_filename(display_name, "tts"), request_data, job_id,
            "submit_tts", idempotency_key, reference_digest,
        )
        if replayed:
            shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
            response.status_code = 200
            response.headers["Idempotency-Replayed"] = "true"
        return public_job(job)

    @app.post(
        "/api/v1/tts/sequence-jobs", status_code=202, response_model=JobResponse,
        response_model_exclude_unset=True, tags=[TTS_TAG],
        summary="提交结构化多段 TTS 任务 / Submit a structured multi-item TTS job",
        description=bilingual(
            "按输入顺序为每一项生成独立 WAV。整批必须使用同一模型、设备、语言和 preset 或 voiceprint 模式；preset 项可使用不同官方音色和指令，voiceprint 项只能引用声纹库中的可用样本。",
            "Generate one ordered WAV artifact per item. A batch shares one model, device, language, and preset or voiceprint mode; preset items may use different official speakers and instructions, while voiceprint items reference eligible library samples.",
        ),
        operation_id="submitTtsSequenceJob",
        responses={**idempotency_replay_response("JobResponse"), **AUTH_RESPONSES, **IDEMPOTENCY_RESPONSES, **ADMISSION_RESPONSE, **TTS_CONTROL_VALIDATION_RESPONSE, **TTS_SERVICE_RESPONSE},
    )
    async def submit_tts_sequence(
        response: Response,
        payload: TtsSequenceRequest,
        idempotency_key: str = Header(..., alias="Idempotency-Key", description=IDEMPOTENCY_KEY_DESCRIPTION, json_schema_extra=IDEMPOTENCY_KEY_SCHEMA),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("tts")
        idempotency_key = validate_idempotency_key(idempotency_key)
        language = validate_tts_language(payload.language)
        compute_device = payload.compute_device.value if payload.compute_device else default_device
        selected_model, _, compute_device, compute_device_name = await run_in_threadpool(
            validate_tts_model_device, payload.model, payload.voice_mode, compute_device,
        )
        ids = [item.id for item in payload.items]
        if len(ids) != len(set(ids)):
            raise ApiProblem(422, "duplicate_tts_sequence_item", "Sequence item IDs must be unique")
        cleaned: list[dict[str, Any]] = []
        total_chars = 0
        for item in payload.items:
            text = item.text.strip()
            if not text:
                raise ApiProblem(422, "invalid_tts_sequence_text", "Every sequence item must contain text")
            total_chars += len(text)
            if payload.voice_mode == "preset":
                if item.speaker not in PRESET_SPEAKERS:
                    raise ApiProblem(422, "unknown_tts_speaker", f"Unknown preset speaker for item {item.id}")
                if item.voiceprint_sample_id:
                    raise ApiProblem(422, "invalid_tts_sequence_item", "preset items must not contain voiceprint_sample_id")
                cleaned.append({
                    "id": item.id, "text": text, "speaker": item.speaker,
                    "instruct": validate_tts_instruction(selected_model, "preset", item.instruct),
                })
            else:
                if item.speaker or item.instruct:
                    raise ApiProblem(422, "invalid_tts_sequence_item", "voiceprint items must not contain speaker or instruct")
                if not item.voiceprint_sample_id:
                    raise ApiProblem(422, "invalid_tts_sequence_item", "voiceprint items require voiceprint_sample_id")
                cleaned.append({
                    "id": item.id, "text": text,
                    "voiceprint_sample_id": item.voiceprint_sample_id,
                })
        if total_chars > settings.max_tts_chars:
            raise ApiProblem(
                422, "tts_sequence_too_large",
                f"Sequence text must not exceed {settings.max_tts_chars} characters in total",
            )

        job_id = uuid.uuid4().hex
        job_root = settings.jobs_dir / job_id
        reference_snapshots: dict[str, dict[str, Any]] = {}
        digest_parts: list[str] = []
        try:
            if payload.voice_mode == "voiceprint":
                for item in cleaned:
                    sample_id = str(item["voiceprint_sample_id"])
                    if sample_id in reference_snapshots:
                        continue
                    sample = get_voiceprint_sample(sample_id)
                    person = get_voiceprint_person(sample["person_id"]) if sample else None
                    if (
                        sample is None or person is None or sample.get("state") != "ready"
                        or not sample.get("audio_path") or not sample.get("transcript")
                    ):
                        raise ApiProblem(422, "voiceprint_sample_unavailable", f"Voiceprint sample {sample_id} is not ready for TTS cloning")
                    source = Path(str(sample["audio_path"])).resolve()
                    if (
                        not source.is_file()
                        or (
                            settings.voiceprints_dir.resolve() not in source.parents
                            and settings.voices_dir.resolve() not in source.parents
                        )
                    ):
                        raise ApiProblem(422, "voiceprint_sample_unavailable", f"Voiceprint sample {sample_id} audio is unavailable")
                    target = job_root / "input" / f"voiceprint-{len(reference_snapshots):03d}.wav"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                    audio_digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    snapshot = {
                        "voiceprint_person_id": person["id"],
                        "voiceprint_person_name": person["name"],
                        "voiceprint_sample_id": sample_id,
                        "reference_audio_path": str(target),
                        "reference_text": sample["transcript"],
                        "reference_words": sample.get("words") or [],
                        "reference_language": sample.get("language") or language,
                        "reference_duration": sample.get("duration"),
                    }
                    reference_snapshots[sample_id] = snapshot
                    digest_parts.append(json.dumps({
                        "sample_id": sample_id,
                        "audio_sha256": audio_digest,
                        "transcript": sample["transcript"],
                        "updated_at": sample.get("updated_at"),
                    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            reference_digest = hashlib.sha256("\n".join(sorted(digest_parts)).encode()).hexdigest() if digest_parts else None
            request_data = {
                "purpose": "tts_sequence",
                "sequence_contract_version": 1,
                "model": selected_model["public_id"],
                "language": language,
                "voice_mode": payload.voice_mode,
                "sequence_items": cleaned,
                "voiceprint_references": reference_snapshots,
                "response_format": "wav",
                "compute_device": compute_device,
                "compute_device_name": compute_device_name,
                "accelerate_single_task": payload.accelerate_single_task,
            }
            job, replayed = idempotent_job(
                "tts", safe_filename(payload.display_name, "tts-sequence"), request_data, job_id,
                "submit_tts_sequence", idempotency_key, reference_digest,
            )
        except Exception:
            shutil.rmtree(job_root, ignore_errors=True)
            raise
        if replayed:
            shutil.rmtree(job_root, ignore_errors=True)
            response.status_code = 200
            response.headers["Idempotency-Replayed"] = "true"
        return public_job(job)

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
        description=bilingual("人员响应包含名字、可选备注、“加入热词库”开关及全部样本；TTS 只应选择可用样本。", "Each person includes its name, optional note, hotword-library switch, and samples; TTS clients must select an eligible sample."),
        operation_id="listVoiceprintPeople", responses={**AUTH_RESPONSES},
    )
    def voiceprint_people(_: None = Depends(require_api_key)) -> VoiceprintPeopleResponse:
        return {"items": [public_voiceprint_person(item) for item in list_voiceprint_people()]}

    @app.post(
        "/api/v1/voiceprints/people", status_code=201, response_model=VoiceprintPersonResponse,
        response_model_exclude_unset=True, tags=[VOICEPRINT_TAG],
        summary="创建声纹人员 / Create voiceprint person",
        description=bilingual("名字必填且规范化后唯一；备注选填、最多 20 字；人名热词同步默认开启。", "The normalized name is required and unique; the optional note is limited to 20 characters; name-hotword synchronization defaults on."),
        operation_id="createVoiceprintPerson",
        responses={**AUTH_RESPONSES, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def add_voiceprint_person(
        payload: VoiceprintPersonCreateRequest, _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        try:
            return public_voiceprint_person(create_voiceprint_person(
                payload.name,
                note=payload.note,
                include_in_hotword_library=payload.include_in_hotword_library,
            ))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A voiceprint person with this name already exists") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch(
        "/api/v1/voiceprints/people/{person_id}", response_model=VoiceprintPersonResponse,
        response_model_exclude_unset=True, tags=[VOICEPRINT_TAG],
        summary="更新声纹人员 / Update voiceprint person",
        description=bilingual("更新名字、备注或人名热词开关；已完成任务中的匹配标签与备注是历史快照，不会被改写。", "Update the name, note, or name-hotword switch; completed-job labels and notes are historical snapshots and are not rewritten."),
        operation_id="updateVoiceprintPerson",
        responses={**AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE, **VALIDATION_RESPONSE},
    )
    def edit_voiceprint_person(
        person_id: str, payload: VoiceprintPersonUpdateRequest,
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        fields = payload.model_fields_set
        if not fields:
            raise HTTPException(status_code=422, detail="At least one person field must be provided")
        values: dict[str, Any] = {}
        if "name" in fields:
            values["name"] = payload.name
        if "note" in fields:
            values["note"] = payload.note
        if "include_in_hotword_library" in fields:
            values["include_in_hotword_library"] = payload.include_in_hotword_library
        try:
            person = update_voiceprint_person(person_id, **values)
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
            "立即返回 `pending` 样本和可见 ASR 入库任务。可选择 0.6B 或 1.7B，但声纹入库不使用热词库。任务成功后重新查询人员列表，样本才可能用于 TTS。",
            "Immediately return a pending sample and a visible ASR import job. Either 0.6B or 1.7B may be selected, but voiceprint imports do not use the hotword library. Refresh the people list after success before using the sample for TTS.",
        ),
        operation_id="uploadVoiceprintSample",
        responses={**idempotency_replay_response("VoiceprintUploadResponse"), **AUTH_RESPONSES, **IDEMPOTENCY_RESPONSES, **ADMISSION_RESPONSE, **NOT_FOUND_RESPONSE, **TOO_LARGE_RESPONSE, **ASR_VALIDATION_RESPONSE, **ASR_SERVICE_RESPONSE},
    )
    async def upload_voiceprint_sample(
        response: Response,
        person_id: str,
        file: UploadFile = File(..., description="单人干净音频或浏览器录音容器 / Clean single-speaker audio or browser recording container"),
        model: str = Form(DEFAULT_ASR_MODEL_ID, description="样本转写使用的 ASR 模型 / ASR model for sample transcription", json_schema_extra={"enum": [item["public_id"] for item in asr_models()]}),
        language: str = Form("Auto", description="转写语言；显式值限公开对齐语种 / Transcription language; explicit values are limited to public alignment languages", json_schema_extra={"enum": ASR_LANGUAGES}),
        compute_device: str = Form(default_device, description="cpu 或 gpu；省略时使用部署默认值且无静默回退 / cpu or gpu; omission uses the deployment default and there is no silent fallback", json_schema_extra={"enum": ["cpu", "gpu"]}),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="单任务自动批处理 / Single-job auto-batching"),
        idempotency_key: str = Header(..., alias="Idempotency-Key", description=IDEMPOTENCY_KEY_DESCRIPTION, json_schema_extra=IDEMPOTENCY_KEY_SCHEMA),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("asr")
        person = get_voiceprint_person(person_id)
        if person is None:
            raise HTTPException(status_code=404, detail="Voiceprint person not found")
        idempotency_key = validate_idempotency_key(idempotency_key)
        language = validate_asr_language(language)
        selected_model, compute_device, compute_device_name = await run_in_threadpool(
            validate_asr_model_device, model, compute_device,
        )
        job_id = uuid.uuid4().hex
        sample_id = "sample_" + uuid.uuid4().hex[:16]
        original_name = safe_filename(file.filename or "voiceprint-audio.bin")
        input_path = settings.jobs_dir / job_id / "input" / original_name
        size, file_digest = await save_upload(file, input_path, settings.max_upload_bytes)
        request_data = {
            "purpose": "voiceprint_import", "voiceprint_sample_id": sample_id,
            "voiceprint_person_id": person["id"],
            "input_path": str(input_path), "original_name": original_name, "size_bytes": size,
            "model": selected_model["public_id"],
            "language": language, "speaker_count": 1, "diarize": False, "align": True,
            "context": "", "export_formats": ["json", "txt"], "compute_device": compute_device,
            "compute_device_name": compute_device_name, "use_voiceprint_library": False,
            "accelerate_single_task": accelerate_single_task,
        }
        job, replayed = idempotent_job(
            "asr", f"声纹样本入库 · {person['name']}", request_data, job_id,
            "upload_voiceprint_sample", idempotency_key, file_digest,
        )
        if replayed:
            shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
            existing_sample = get_voiceprint_sample(
                str((job.get("request") or {}).get("voiceprint_sample_id") or "")
            )
            if existing_sample is None:
                raise ApiProblem(409, "idempotency_replay_unavailable", "The original voiceprint sample is unavailable")
            response.status_code = 200
            response.headers["Idempotency-Replayed"] = "true"
            return {"sample": public_voiceprint_sample(existing_sample), "job": public_job(job)}
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
            "按创建时间稳定倒序分页。`count` 是本页数量，`total` 是筛选后的总数；队列实际消费按任务类型分别 FIFO。",
            "Stably paginated newest-first. `count` is the page count and `total` is the filtered total; ASR and TTS workers consume separate FIFO queues.",
        ),
        operation_id="listJobs", responses={**AUTH_RESPONSES, **VALIDATION_RESPONSE},
    )
    def jobs(
        kind: str | None = Query(None, description="可选 asr 或 tts / Optional asr or tts"),
        state: str | None = Query(None, description="queued、running、succeeded、failed 或 cancelled"),
        q: str | None = Query(None, max_length=128, description="任务 ID 或显示名称的字面子串 / Literal substring of job ID or display name"),
        limit: int = Query(100, ge=1, le=500, description="每页 1–500 / Page size from 1 to 500"),
        offset: int = Query(0, ge=0, description="分页偏移 / Page offset"),
        _: None = Depends(require_api_key),
    ) -> JobListResponse:
        rows, total = list_jobs_page(kind, state, q, limit, offset)
        items = public_jobs(rows, summary=True)
        return {
            "items": items, "count": len(items), "total": total,
            "limit": limit, "offset": offset, "has_more": offset + len(items) < total,
        }

    @app.get(
        "/api/v1/jobs/{job_id}", response_model=JobResponse, response_model_exclude_unset=True,
        tags=[JOB_TAG], summary="查询任务状态与进度 / Get job status and progress",
        description=bilingual(
            "可靠轮询入口。`queue.position` 返回同类任务中的排队位置。`progress` 是单调的最佳整体进度；`progress_detail.basis=estimated` 表示含估算，`activity` 提供当前推理调用的模型活动。`estimate` 是基于本机历史样本的区间估计；成功后出现 `result_url`。支持 `If-None-Match`。",
            "Canonical polling endpoint. `queue.position` is the position within the same job kind. `progress` is monotonic and best-effort; `progress_detail.basis=estimated` marks an estimate, while `activity` describes the current model call. `estimate` is a range learned from local history. `result_url` appears after success. Supports `If-None-Match`.",
        ),
        operation_id="getJob", responses={**conditional_job_responses(), **AUTH_RESPONSES, **NOT_FOUND_RESPONSE},
    )
    def job_status(
        job_id: str, response: Response,
        if_none_match: str | None = Header(
            None, alias="If-None-Match",
            description="上次响应的 ETag；匹配时返回 304 / ETag from the previous response; returns 304 when unchanged",
            json_schema_extra={"example": '"0123456789abcdef01234567"'},
        ),
        _: None = Depends(require_api_key),
    ) -> Any:
        job = job_or_404(job_id)
        context = queue_context(include_history=job.get("state") in {"queued", "running"})
        marker = "|".join(str(job.get(key) or "") for key in (
            "updated_at", "state", "stage", "progress", "attempts",
        ))
        marker += "|" + json.dumps([
            (item["id"], item["state"], item.get("updated_at"), item.get("progress"))
            for item in context["jobs"] if item["kind"] == job["kind"]
        ], separators=(",", ":"))
        etag = f'"{hashlib.sha256(marker.encode()).hexdigest()[:24]}"'
        if if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        response.headers.update({"ETag": etag, "Cache-Control": "no-cache"})
        return public_job(job, context)

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
        description=bilingual("排队任务原子取消；运行任务保持 `state=running` 并进入 `stage=cancelling`，完整进程树退出后才进入终态 `state=cancelled`。", "Queued jobs cancel atomically. A running job keeps `state=running` with `stage=cancelling`, and reaches terminal `state=cancelled` only after the complete process tree exits."),
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
            200: {"description": "完整媒体 / Full media", "headers": {"Accept-Ranges": {"schema": {"type": "string", "example": "bytes"}}, "Content-Length": {"schema": {"type": "integer"}}}, "content": {"audio/*": {"schema": BINARY_SCHEMA}, "video/*": {"schema": BINARY_SCHEMA}}},
            206: {"description": "部分媒体 / Partial media", "headers": {"Accept-Ranges": {"schema": {"type": "string", "example": "bytes"}}, "Content-Length": {"schema": {"type": "integer"}}, "Content-Range": {"schema": {"type": "string", "example": "bytes 0-1048575/4194304"}}}, "content": {"application/octet-stream": {"schema": BINARY_SCHEMA}}},
            416: {**problem_response("Range 超出文件范围 / Requested range is not satisfiable", 416), "headers": {"Content-Range": {"schema": {"type": "string", "example": "bytes */4194304"}}}},
            **AUTH_RESPONSES, **NOT_FOUND_RESPONSE, **CONFLICT_RESPONSE,
        },
    )
    def job_source(
        job_id: str,
        download: bool = Query(False, description="作为附件下载 / Download as an attachment"),
        range_header: str | None = Header(
            None, alias="Range",
            description="可选单段字节范围 / Optional single byte range",
            json_schema_extra={"example": "bytes=0-1048575"},
        ),
        _: None = Depends(require_api_key),
    ) -> FileResponse:
        del range_header  # Starlette FileResponse reads Range directly from the ASGI request scope.
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
        summary="订阅任务摘要和 worker 增量 / Stream job-summary and worker deltas",
        description=bilingual(
            f"连接后发送最近最多 {EVENT_SNAPSHOT_JOB_LIMIT} 个任务摘要的 `snapshot`；之后仅在业务状态变化时发送 `update`（含变更摘要与 removed_job_ids）。空闲约 {EVENT_HEARTBEAT_SECONDS} 秒发送轻量 `heartbeat`。完整 request/result 仅由单任务接口返回；无历史重放。",
            f"Sends an initial `snapshot` of up to {EVENT_SNAPSHOT_JOB_LIMIT} job summaries, followed by `update` events only for semantic changes (changed summaries plus removed_job_ids). A lightweight `heartbeat` is sent about every {EVENT_HEARTBEAT_SECONDS} idle seconds. Full request/result remain per-job only; history is not replayed.",
        ),
        operation_id="streamEvents",
        responses={
            200: {
                **sse_response("snapshot", "EventSnapshot", '{"jobs":[],"workers":[]}'),
                "x-event-data-schemas": {
                    "snapshot": {"$ref": "#/components/schemas/EventSnapshot"},
                    "update": {"$ref": "#/components/schemas/EventUpdate"},
                    "heartbeat": {"type": "object", "maxProperties": 0},
                },
            },
            **AUTH_RESPONSES,
        },
    )
    async def events(_: None = Depends(require_api_key)) -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            queue = await app.state.event_hub.subscribe()
            previous: dict[str, Any] | None = None
            heartbeat_deadline = time.monotonic() + EVENT_HEARTBEAT_SECONDS
            try:
                while True:
                    timeout = max(0, heartbeat_deadline - time.monotonic())
                    try:
                        snapshot = await asyncio.wait_for(queue.get(), timeout=timeout)
                        if previous is None:
                            yield f"event: snapshot\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                            previous = snapshot
                            heartbeat_deadline = time.monotonic() + EVENT_HEARTBEAT_SECONDS
                            continue
                        update = event_delta(previous, snapshot)
                        previous = snapshot
                        if update is not None:
                            yield f"event: update\ndata: {json.dumps(update, ensure_ascii=False)}\n\n"
                            heartbeat_deadline = time.monotonic() + EVENT_HEARTBEAT_SECONDS
                    except asyncio.TimeoutError:
                        yield "event: heartbeat\ndata: {}\n\n"
                        heartbeat_deadline = time.monotonic() + EVENT_HEARTBEAT_SECONDS
            finally:
                await app.state.event_hub.unsubscribe(queue)
        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(
        "/api/v1/jobs/{job_id}/events", response_class=StreamingResponse, tags=[JOB_TAG],
        summary="订阅单个任务 / Stream one job",
        description=bilingual(
            "立即发送当前任务快照，变化时继续发送；终态后关闭。断线重连后以首个快照校准，不提供历史重放。",
            "Emit the current job immediately, then changes; close after a terminal state. Reconnect using the first snapshot; history is not replayed.",
        ),
        operation_id="streamJobEvents",
        responses={
            200: sse_response("job", "EventJobResponse", '{"id":"JOB_ID","kind":"asr","state":"running","progress":0.5}'),
            **AUTH_RESPONSES, **NOT_FOUND_RESPONSE,
        },
    )
    async def job_events(job_id: str, _: None = Depends(require_api_key)) -> StreamingResponse:
        job_or_404(job_id)
        async def stream() -> AsyncIterator[str]:
            queue = await app.state.event_hub.subscribe()
            last = ""
            heartbeat_deadline = time.monotonic() + EVENT_HEARTBEAT_SECONDS
            try:
                while True:
                    current = get_job(job_id)
                    if current is None:
                        return
                    item = public_job(current)
                    encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                    if encoded != last:
                        yield f"event: job\ndata: {json.dumps(item, ensure_ascii=False)}\n\n"
                        last = encoded
                        heartbeat_deadline = time.monotonic() + EVENT_HEARTBEAT_SECONDS
                    if current["state"] in {"succeeded", "failed", "cancelled"}:
                        return
                    try:
                        await asyncio.wait_for(
                            queue.get(), timeout=max(0, heartbeat_deadline - time.monotonic()),
                        )
                    except asyncio.TimeoutError:
                        yield "event: heartbeat\ndata: {}\n\n"
                        heartbeat_deadline = time.monotonic() + EVENT_HEARTBEAT_SECONDS
            finally:
                await app.state.event_hub.unsubscribe(queue)
        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
            AdmissionProblemDetail | ProblemDetail | EventJobResponse | EventSnapshot | EventUpdate
            | OpenAITranscription | OpenAIVerboseTranscription
        ).json_schema(ref_template="#/components/schemas/{model}")
        schema.setdefault("components", {}).setdefault("schemas", {}).update(extra.get("$defs", {}))
        schema["servers"] = [{"url": "/", "description": "当前本地服务 / Current local service"}]
        app.openapi_schema = enrich_openapi_schema(schema)
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
    hardware["gpu"] = cached_gpu_snapshot(0, probe=gpu_snapshot)
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
        "deployment": deployment_metadata(settings.deployment_profile),
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
    default_device = settings.default_compute_device
    @app.get(
        "/v1/models", response_model=OpenAIModelList, response_model_exclude_unset=True,
        tags=[OPENAI_TAG], summary="列出兼容模型 / List compatible models",
        description=bilingual("只列出当前启用服务对应的本地模型别名。", "List local model aliases for currently enabled services only."),
        operation_id="listOpenAIModels", responses={**AUTH_RESPONSES},
    )
    def openai_models(_: None = Depends(require_api_key)) -> OpenAIModelList:
        data = []
        if "asr" in settings.enabled_services:
            data.extend(
                {"id": item["public_id"], "object": "model", "owned_by": "local"}
                for item in asr_models()
            )
        if "tts" in settings.enabled_services:
            data.extend(
                {"id": item["public_id"], "object": "model", "owned_by": "local"}
                for item in tts_models()
            )
        return {"object": "list", "data": data}

    @app.post(
        "/v1/audio/transcriptions", response_class=Response, tags=[OPENAI_TAG],
        summary="同步兼容转写 / Create synchronous compatible transcription",
        description=bilingual(
            "兼容式同步入口，会等待内部任务完成。支持 0.6B/1.7B、一次性 `prompt` 和本地热词词表；词表内容在提交时保存快照。长音频、进度查询、取消和可靠恢复应使用 `/api/v1/asr/jobs`。响应头 `X-Job-ID` 可关联历史任务。",
            "Synchronous compatibility endpoint that waits for the internal job. It supports 0.6B/1.7B, a one-off `prompt`, and snapshotted local hotword lists. Use `/api/v1/asr/jobs` for long audio, progress, cancellation, and recovery. `X-Job-ID` links the response to job history.",
        ),
        operation_id="createOpenAITranscription",
        responses={
            200: {
                "description": "由 response_format 决定 / Selected by response_format",
                "headers": {
                    "X-Job-ID": {"schema": {"type": "string"}},
                    "Idempotency-Replayed": {
                        "description": "仅在可选幂等键重放时为 true / Present as true only for an optional-key replay",
                        "schema": {"type": "string", "enum": ["true"]},
                    },
                },
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
            **AUTH_RESPONSES, **OPTIONAL_IDEMPOTENCY_RESPONSES, **ADMISSION_RESPONSE,
            **NOT_FOUND_RESPONSE, **TOO_LARGE_RESPONSE, **ASR_VALIDATION_RESPONSE, **ASR_SERVICE_RESPONSE,
        },
    )
    async def openai_transcription(
        file: UploadFile = File(..., description="待转写音频或视频 / Audio or video to transcribe"),
        model: str = Form(DEFAULT_ASR_MODEL_ID, description="ASR 模型 ID / ASR model ID", json_schema_extra={"enum": [item["public_id"] for item in asr_models()]}),
        prompt: str = Form("", description="一次性识别上下文；所选词表的 Vocabulary 段会追加在其后 / One-off recognition context followed by the Vocabulary section generated from selected lists"),
        hotword_list_ids: str = Form("", description="逗号分隔的本地词表 ID，最多 8 个；留空禁用已保存词表 / Comma-separated local list IDs, maximum 8; empty disables stored lists"),
        language: str = Form("Auto", description="识别语言；显式值限公开对齐语种 / Recognition language; explicit values are limited to public alignment languages", json_schema_extra={"enum": ASR_LANGUAGES}),
        response_format: str = Form("json", description="json、verbose_json、text、srt 或 vtt"),
        diarize: bool = Form(True, description="启用说话人分离 / Enable diarization"),
        speaker_count: str = Form("auto", description="auto 或 1–15 / auto or 1–15"),
        compute_device: str = Form(default_device, description="cpu 或 gpu；省略时使用部署默认值且无静默回退 / cpu or gpu; omission uses the deployment default and there is no silent fallback", json_schema_extra={"enum": ["cpu", "gpu"]}),
        use_voiceprint_library: bool = Form(True, description="匹配声纹库 / Match voiceprint library"),
        accelerate_single_task: bool = Form(SINGLE_TASK_ACCELERATION_DEFAULT, description="单任务自动批处理 / Single-job auto-batching"),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key", description=OPTIONAL_IDEMPOTENCY_KEY_DESCRIPTION, json_schema_extra=IDEMPOTENCY_KEY_SCHEMA),
        _: None = Depends(require_api_key),
    ) -> Response:
        ensure_service("asr")
        language = validate_asr_language(language)
        selected_model = resolve_asr_model(model)
        if selected_model is None:
            raise HTTPException(status_code=404, detail="Unknown transcription model")
        selected_model, compute_device, compute_device_name = await run_in_threadpool(
            validate_asr_model_device, selected_model["public_id"], compute_device,
        )
        existing = None
        if idempotency_key is not None:
            idempotency_key = validate_idempotency_key(idempotency_key)
            existing = await run_in_threadpool(
                find_idempotent_job, "openai_transcription", idempotency_key_hash(idempotency_key),
            )
        hotword_data = await run_in_threadpool(
            hotword_request_data, prompt, hotword_list_ids, existing,
        )
        try:
            speakers = None if speaker_count == "auto" else int(speaker_count)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="speaker_count must be auto or an integer") from exc
        if speakers is not None and not 1 <= speakers <= 15:
            raise HTTPException(status_code=422, detail="speaker_count must be between 1 and 15")
        job_id = uuid.uuid4().hex
        name = safe_filename(file.filename or "audio.bin")
        target = settings.jobs_dir / job_id / "input" / name
        size, file_digest = await save_upload(file, target, settings.max_upload_bytes)
        request_data = {
            "input_path": str(target), "original_name": name, "size_bytes": size, "language": language,
            "model": selected_model["public_id"],
            "speaker_count": speakers, "diarize": diarize, "align": True, "context": prompt,
            "export_formats": ["json", "srt", "vtt", "txt"], "compute_device": compute_device,
            "compute_device_name": compute_device_name, "use_voiceprint_library": use_voiceprint_library,
            "accelerate_single_task": accelerate_single_task,
            **hotword_data,
        }
        replayed = False
        if idempotency_key is not None:
            job, replayed = idempotent_job(
                "asr", name, request_data, job_id, "openai_transcription",
                idempotency_key, file_digest,
            )
            if replayed:
                shutil.rmtree(settings.jobs_dir / job_id, ignore_errors=True)
        else:
            job = create_job("asr", name, request_data, job_id)
        submitted_job_id = job["id"]
        job = await wait_for_job(submitted_job_id)
        if job["state"] != "succeeded":
            raise HTTPException(status_code=500, detail=job.get("error_message") or "Transcription failed")
        result = job.get("result") or {}
        response_headers = {"X-Job-ID": submitted_job_id}
        if replayed:
            response_headers["Idempotency-Replayed"] = "true"
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
            "等待内部 TTS 任务完成并直接返回音频。`model` 省略时使用 0.6B；1.7B 官方预置音色可选填 `instructions`，其它组合必须留空。`voice` 支持官方预置音色或 `voice_` 声音档案，不支持 VoiceDesign 或直接传声纹样本 ID。需要进度、取消、VoiceDesign 或精确声纹样本时使用 `/api/v1/tts/jobs`。",
            "Wait for an internal TTS job and return audio. Omitting `model` uses 0.6B; an official 1.7B preset may include `instructions`, which must be empty for every other combination. `voice` accepts an official preset or a `voice_` profile, not VoiceDesign or a voiceprint sample ID. Use `/api/v1/tts/jobs` for progress, cancellation, VoiceDesign, or an exact voiceprint sample.",
        ),
        operation_id="createOpenAISpeech",
        responses={
            200: {
                "description": "生成音频 / Generated audio",
                "headers": {
                    "X-Job-ID": {"schema": {"type": "string"}},
                    "Idempotency-Replayed": {
                        "description": "仅在可选幂等键重放时为 true / Present as true only for an optional-key replay",
                        "schema": {"type": "string", "enum": ["true"]},
                    },
                },
                "content": {
                    "audio/wav": {"schema": BINARY_SCHEMA},
                    "audio/flac": {"schema": BINARY_SCHEMA},
                    "audio/mpeg": {"schema": BINARY_SCHEMA},
                },
            },
            500: problem_response("内部合成任务失败 / Internal speech job failed", 500),
            504: problem_response("兼容接口等待超时 / Compatibility wait timed out", 504),
            **AUTH_RESPONSES, **OPTIONAL_IDEMPOTENCY_RESPONSES, **ADMISSION_RESPONSE,
            **TTS_CONTROL_VALIDATION_RESPONSE, **TTS_SERVICE_RESPONSE,
        },
    )
    async def openai_speech(
        payload: OpenAISpeechRequest = Body(...),
        idempotency_key: str | None = Header(None, alias="Idempotency-Key", description=OPTIONAL_IDEMPOTENCY_KEY_DESCRIPTION, json_schema_extra=IDEMPOTENCY_KEY_SCHEMA),
        _: None = Depends(require_api_key),
    ) -> FileResponse:
        ensure_service("tts")
        accelerate_single_task = payload.accelerate_single_task
        text = payload.input.strip()
        if not text:
            raise HTTPException(status_code=422, detail="input is required")
        if payload.response_format not in {"wav", "flac", "mp3"}:
            raise HTTPException(status_code=422, detail="response_format must be wav, flac or mp3")
        unsupported_controls = {
            "speed", "pitch", "temperature", "top_k", "top_p", "repetition_penalty",
        }.intersection((payload.model_extra or {}).keys())
        if unsupported_controls:
            raise ApiProblem(
                422, "unsupported_tts_control",
                f"Unsupported TTS controls: {', '.join(sorted(unsupported_controls))}",
            )
        language = validate_tts_language(payload.language)
        voice = payload.voice
        voice_mode = "profile" if voice.startswith("voice_") else "preset"
        selected_model, _, compute_device, compute_device_name = await run_in_threadpool(
            validate_tts_model_device, payload.model, voice_mode, payload.compute_device or default_device,
        )
        instructions = validate_tts_instruction(selected_model, voice_mode, payload.instructions)
        request_data: dict[str, Any] = {
            "text": text, "model": selected_model["public_id"],
            "language": language, "instruct": instructions,
            "response_format": payload.response_format, "compute_device": compute_device,
            "compute_device_name": compute_device_name,
            "accelerate_single_task": accelerate_single_task,
        }
        if voice.startswith("voice_"):
            profile = get_voice(voice)
            if profile is None:
                raise HTTPException(status_code=422, detail="Voice profile not found")
            request_data.update({
                "voice_mode": "profile", "voice_profile_id": voice,
                "reference_audio_path": profile["ref_audio_path"], "reference_text": profile["ref_text"],
                "reference_language": profile.get("language") or language,
                "reference_words": profile.get("words") or [], "reference_duration": profile.get("duration"),
            })
        else:
            if voice not in PRESET_SPEAKERS:
                raise HTTPException(status_code=422, detail="Unknown voice")
            request_data.update({"voice_mode": "preset", "speaker": voice})
        replayed = False
        if idempotency_key is not None:
            idempotency_key = validate_idempotency_key(idempotency_key)
            job, replayed = idempotent_job(
                "tts", "speech", request_data, uuid.uuid4().hex, "openai_speech",
                idempotency_key,
            )
        else:
            job = create_job("tts", "speech", request_data)
        finished = await wait_for_job(job["id"])
        if finished["state"] != "succeeded":
            raise HTTPException(status_code=500, detail=finished.get("error_message") or "Speech synthesis failed")
        artifact = (finished.get("result") or {}).get("artifacts", [])[0]
        headers = {"X-Job-ID": job["id"]}
        if replayed:
            headers["Idempotency-Replayed"] = "true"
        return FileResponse(artifact["path"], media_type=artifact["mime_type"], headers=headers)


app = create_app()
