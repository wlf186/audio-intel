from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import settings
from .gpu import COMPUTE_DEVICES, gpu_snapshot
from .db import (
    create_job,
    create_voice,
    delete_voice_record,
    get_job,
    get_voice,
    init_db,
    list_jobs,
    list_voices,
    list_workers,
    request_cancel,
    retry_job,
    update_job,
    utcnow,
)
from .utils import safe_filename
from .purge import purge_jobs


PRESET_SPEAKERS = ["Vivian", "Serena", "Uncle_Fu", "Dylan", "Eric", "Ryan", "Aiden", "Ono_Anna", "Sohee"]
ALIGNER_LANGUAGES = [
    "Chinese", "English", "Cantonese", "French", "German", "Italian",
    "Japanese", "Korean", "Portuguese", "Russian", "Spanish",
]


class BatchDeleteRequest(BaseModel):
    job_ids: list[str]
    purge: bool = False


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_key:
        return
    expected = f"Bearer {settings.api_key}"
    if authorization != expected:
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


def create_app() -> FastAPI:
    settings.ensure_directories()
    init_db()
    app = FastAPI(
        title="Sandevistan-Audio",
        description="Completely local ASR, speaker diarization, forced alignment and TTS service.",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )

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

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return system_snapshot()

    @app.get("/api/v1/capabilities")
    def capabilities(_: None = Depends(require_api_key)) -> dict[str, Any]:
        return {
            "services": sorted(settings.enabled_services),
            "offline": True,
            "asr": {
                "model": "Qwen3-ASR-0.6B",
                "diarization": "CAM++ single-active-speaker",
                "timestamp_precisions": ["segment", "word_or_character"],
                "aligner_languages": ALIGNER_LANGUAGES,
                "exports": ["json", "srt", "vtt", "txt"],
                "compute_devices": compute_capabilities("gpu"),
            },
            "tts": {
                "models": ["Qwen3-TTS-12Hz-0.6B-Base", "Qwen3-TTS-12Hz-0.6B-CustomVoice"],
                "voice_modes": ["preset", "profile", "inline_clone"],
                "preset_speakers": PRESET_SPEAKERS,
                "formats": ["wav", "flac", "mp3"],
                "compute_devices": compute_capabilities("cpu"),
            },
            "limits": {"max_upload_bytes": settings.max_upload_bytes, "max_tts_chars": settings.max_tts_chars},
        }

    @app.post("/api/v1/asr/jobs", status_code=202)
    async def submit_asr(
        file: UploadFile = File(...),
        language: str = Form("Auto"),
        speaker_count: str = Form("auto"),
        diarize: bool = Form(True),
        align: bool = Form(True),
        context: str = Form(""),
        export_formats: str = Form("json,srt,vtt,txt"),
        compute_device: str = Form("gpu"),
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
            raise HTTPException(status_code=422, detail="speaker_count must be between 1 and 15")
        formats = [item.strip().lower() for item in export_formats.split(",") if item.strip()]
        if not formats or any(item not in {"json", "srt", "vtt", "txt"} for item in formats):
            raise HTTPException(status_code=422, detail="Unsupported export format")
        request_data = {
            "input_path": str(input_path), "original_name": original_name, "size_bytes": size,
            "language": language, "speaker_count": speaker_value, "diarize": diarize,
            "align": align, "context": context, "export_formats": formats, "compute_device": compute_device,
            "compute_device_name": compute_device_name,
        }
        return public_job(create_job("asr", original_name, request_data, job_id))

    @app.post("/api/v1/tts/jobs", status_code=202)
    async def submit_tts(
        text: str = Form(...),
        language: str = Form("Chinese"),
        voice_mode: str = Form("preset"),
        speaker: str | None = Form(None),
        voice_profile_id: str | None = Form(None),
        reference_audio: UploadFile | None = File(None),
        reference_text: str | None = Form(None),
        instruct: str = Form(""),
        response_format: str = Form("wav"),
        display_name: str = Form("语音合成"),
        compute_device: str = Form("cpu"),
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("tts")
        compute_device, compute_device_name = validate_compute_device(compute_device)
        clean_text = text.strip()
        if not clean_text or len(clean_text) > settings.max_tts_chars:
            raise HTTPException(status_code=422, detail=f"Text must contain 1-{settings.max_tts_chars} characters")
        if voice_mode not in {"preset", "profile", "inline_clone"}:
            raise HTTPException(status_code=422, detail="voice_mode must be preset, profile or inline_clone")
        if response_format not in {"wav", "flac", "mp3"}:
            raise HTTPException(status_code=422, detail="response_format must be wav, flac or mp3")
        request_data: dict[str, Any] = {
            "text": clean_text, "language": language, "voice_mode": voice_mode,
            "speaker": speaker, "voice_profile_id": voice_profile_id, "reference_text": reference_text,
            "instruct": instruct, "response_format": response_format, "compute_device": compute_device,
            "compute_device_name": compute_device_name,
        }
        job_id = uuid.uuid4().hex
        if voice_mode == "preset":
            if speaker not in PRESET_SPEAKERS:
                raise HTTPException(status_code=422, detail="Unknown preset speaker")
        elif voice_mode == "profile":
            voice = get_voice(voice_profile_id or "")
            if voice is None:
                raise HTTPException(status_code=422, detail="Voice profile not found")
            request_data.update({"reference_audio_path": voice["ref_audio_path"], "reference_text": voice["ref_text"]})
        else:
            if reference_audio is None or not (reference_text or "").strip():
                raise HTTPException(status_code=422, detail="Inline cloning requires reference_audio and reference_text")
            filename = safe_filename(reference_audio.filename or "reference.wav")
            target = settings.jobs_dir / job_id / "input" / filename
            await save_upload(reference_audio, target, 100 * 1024 * 1024)
            request_data["reference_audio_path"] = str(target)
        return public_job(create_job("tts", safe_filename(display_name, "tts"), request_data, job_id))

    @app.get("/api/v1/tts/voices")
    def voices(_: None = Depends(require_api_key)) -> dict[str, Any]:
        return {"items": list_voices(), "preset_speakers": PRESET_SPEAKERS}

    @app.post("/api/v1/tts/voices", status_code=201)
    async def save_voice(
        name: str = Form(...), language: str = Form("Chinese"), ref_text: str = Form(...),
        ref_audio: UploadFile = File(...), _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        ensure_service("tts")
        if not name.strip() or not ref_text.strip():
            raise HTTPException(status_code=422, detail="Voice name and accurate reference text are required")
        voice_dir = settings.voices_dir / uuid.uuid4().hex
        target = voice_dir / safe_filename(ref_audio.filename or "reference.wav")
        await save_upload(ref_audio, target, 100 * 1024 * 1024)
        return create_voice(name.strip(), language, str(target), ref_text.strip())

    @app.delete("/api/v1/tts/voices/{voice_id}", status_code=204)
    def remove_voice(voice_id: str, purge: bool = Query(False), _: None = Depends(require_api_key)) -> Response:
        voice = get_voice(voice_id)
        if voice is None:
            raise HTTPException(status_code=404, detail="Voice profile not found")
        if not purge:
            raise HTTPException(status_code=409, detail="Set purge=true to permanently delete this voice profile")
        ref_path = Path(voice["ref_audio_path"])
        if settings.voices_dir in ref_path.resolve().parents:
            shutil.rmtree(ref_path.parent, ignore_errors=True)
        delete_voice_record(voice_id)
        return Response(status_code=204)

    @app.get("/api/v1/jobs")
    def jobs(
        kind: str | None = Query(None), state: str | None = Query(None), limit: int = Query(100),
        offset: int = Query(0), _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        items = [public_job(item) for item in list_jobs(kind, state, limit, offset)]
        return {"items": items, "count": len(items), "limit": limit, "offset": offset}

    @app.get("/api/v1/jobs/{job_id}")
    def job_status(job_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
        return public_job(job_or_404(job_id))

    @app.post("/api/v1/jobs/batch-delete")
    def batch_delete_jobs(payload: BatchDeleteRequest, _: None = Depends(require_api_key)) -> dict[str, Any]:
        if not payload.purge:
            raise HTTPException(status_code=409, detail="Set purge=true to permanently delete input, output and history")
        job_ids = [item.strip() for item in payload.job_ids if item.strip()]
        if not job_ids:
            raise HTTPException(status_code=422, detail="job_ids must contain at least one task ID")
        if len(set(job_ids)) > 100:
            raise HTTPException(status_code=422, detail="A maximum of 100 task IDs can be deleted at once")
        return purge_jobs(job_ids)

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
        job = request_cancel(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return public_job(job)

    @app.post("/api/v1/jobs/{job_id}/retry")
    def retry(job_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
        try:
            job = retry_job(job_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return public_job(job)

    @app.delete("/api/v1/jobs/{job_id}", status_code=204)
    def purge_job(job_id: str, purge: bool = Query(False), _: None = Depends(require_api_key)) -> Response:
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

    @app.get("/api/v1/jobs/{job_id}/result")
    def job_result(job_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
        job = job_or_404(job_id)
        if job["state"] != "succeeded":
            raise HTTPException(status_code=409, detail="Job has not completed successfully")
        return job.get("result") or {}

    @app.get("/api/v1/jobs/{job_id}/source")
    def job_source(
        job_id: str, download: bool = Query(False), _: None = Depends(require_api_key),
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

    @app.get("/api/v1/jobs/{job_id}/artifacts/{name}")
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

    @app.patch("/api/v1/jobs/{job_id}/speakers/{speaker_id}")
    def rename_speaker(
        job_id: str, speaker_id: str, payload: dict[str, str] = Body(...), _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        job = job_or_404(job_id)
        if job["kind"] != "asr" or job["state"] != "succeeded":
            raise HTTPException(status_code=409, detail="Only completed ASR speakers can be renamed")
        name = payload.get("name", "").strip()[:80]
        if not name:
            raise HTTPException(status_code=422, detail="Speaker name is required")
        result = job.get("result") or {}
        found = False
        for speaker in result.get("speakers", []):
            if speaker.get("id") == speaker_id:
                speaker["label"] = name
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

    @app.get("/api/v1/events")
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
    model_specs = [
        ("Qwen3-ASR-0.6B", "CPU FP32 / GPU BF16"),
        ("Qwen3-ForcedAligner-0.6B", "CPU FP32 / GPU BF16"),
        ("FSMN-VAD", "CPU · FP32"),
        ("CAM++", "CPU · FP32"),
        ("Qwen3-TTS-12Hz-0.6B-Base", "CPU FP32 / GPU BF16"),
        ("Qwen3-TTS-12Hz-0.6B-CustomVoice", "CPU FP32 / GPU BF16"),
    ]
    models = [
        {"name": name, "device": device, "installed": (settings.models_dir / name / ".complete").is_file(), "path": str(settings.models_dir / name)}
        for name, device in model_specs
    ]
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
    @app.get("/v1/models")
    def openai_models(_: None = Depends(require_api_key)) -> dict[str, Any]:
        data = []
        if "asr" in settings.enabled_services:
            data.append({"id": "qwen3-asr-0.6b", "object": "model", "owned_by": "local"})
        if "tts" in settings.enabled_services:
            data.append({"id": "qwen3-tts-0.6b", "object": "model", "owned_by": "local"})
        return {"object": "list", "data": data}

    @app.post("/v1/audio/transcriptions")
    async def openai_transcription(
        file: UploadFile = File(...), model: str = Form("qwen3-asr-0.6b"),
        language: str = Form("Auto"), response_format: str = Form("json"),
        diarize: bool = Form(True), speaker_count: str = Form("auto"),
        compute_device: str = Form("gpu"),
        _: None = Depends(require_api_key),
    ) -> Response:
        ensure_service("asr")
        compute_device, compute_device_name = validate_compute_device(compute_device)
        if model not in {"qwen3-asr-0.6b", "Qwen/Qwen3-ASR-0.6B"}:
            raise HTTPException(status_code=404, detail="Unknown transcription model")
        job_id = uuid.uuid4().hex
        name = safe_filename(file.filename or "audio.bin")
        target = settings.jobs_dir / job_id / "input" / name
        size = await save_upload(file, target, settings.max_upload_bytes)
        speakers = None if speaker_count == "auto" else int(speaker_count)
        request_data = {
            "input_path": str(target), "original_name": name, "size_bytes": size, "language": language,
            "speaker_count": speakers, "diarize": diarize, "align": True, "context": "",
            "export_formats": ["json", "srt", "vtt", "txt"], "compute_device": compute_device,
            "compute_device_name": compute_device_name,
        }
        create_job("asr", name, request_data, job_id)
        job = await wait_for_job(job_id)
        if job["state"] != "succeeded":
            raise HTTPException(status_code=500, detail=job.get("error_message") or "Transcription failed")
        result = job.get("result") or {}
        if response_format == "text":
            return PlainTextResponse(result.get("text", ""))
        if response_format in {"srt", "vtt"}:
            artifact = next((item for item in result.get("artifacts", []) if item["name"].endswith(f".{response_format}")), None)
            if artifact:
                return FileResponse(artifact["path"], media_type=artifact["mime_type"])
        if response_format == "verbose_json":
            return JSONResponse({"task": "transcribe", "language": result.get("language"), "duration": result.get("duration"), "text": result.get("text", ""), "segments": result.get("segments", [])})
        return JSONResponse({"text": result.get("text", "")}, headers={"X-Job-ID": job_id})

    @app.post("/v1/audio/speech")
    async def openai_speech(payload: dict[str, Any] = Body(...), _: None = Depends(require_api_key)) -> FileResponse:
        ensure_service("tts")
        compute_device, compute_device_name = validate_compute_device(str(payload.get("compute_device", "cpu")))
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
