from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import psutil
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import document_store as store
from .document_text import FORMATS, digest, segment
from .utils import atomic_json


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segmentation_mode: Literal["auto", "length"] = "auto"
    target_section_chars: int = Field(10000, ge=1000, le=50000)


class DocumentImportResponse(BaseModel):
    id: str
    name: str
    state: Literal["queued", "running", "ready", "failed"]
    size_bytes: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    updated_at: str
    storage_bytes: int = 0


class DocumentSection(BaseModel):
    id: str
    index: int
    title: str
    start: int
    end: int
    char_count: int
    basis: str
    page_start: int | None = None
    page_end: int | None = None


class DocumentPreview(BaseModel):
    preview_revision: str
    segmentation_mode: Literal["auto", "length"]
    target_section_chars: int
    sections: list[DocumentSection]
    total_chars: int
    section_count: int
    title: str
    warnings: list[str]


class DocumentText(BaseModel):
    text: str
    start: int
    total_chars: int


class JobDocumentSection(DocumentSection):
    job_id: str
    position: int
    start_offset: int
    end_offset: int
    state: Literal["pending", "generating", "complete"]
    retries: int
    artifact: dict[str, Any] | None
    updated_at: str


class JobDocumentSections(BaseModel):
    items: list[JobDocumentSection]
    total: int
    offset: int
    limit: int


def public_import(config: Any, item: dict[str, Any]) -> dict[str, Any]:
    size = 0
    root = import_root(config, item["id"])
    for name in ("source" + item["suffix"], "parsed.json", "parse.log"):
        try:
            size += (root / name).stat().st_size
        except FileNotFoundError:
            pass
    return {**item, "storage_bytes": size}


def validate_snapshot(config: Any, job_id: str) -> None:
    root = config.jobs_dir / job_id / "input"
    try:
        manifest = json.loads((root / "document.json").read_text(encoding="utf-8"))
        text = (root / "document.txt").read_text(encoding="utf-8")
        if any(not text[p["start"]:p["end"]].strip() for p in manifest["sections"]):
            raise HTTPException(422, "Document snapshot contains blank sections; preview the import and create a new job / 请重新预览文档并创建任务")
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(409, "Document snapshot is unavailable") from exc


def capabilities(config: Any) -> dict[str, Any]:
    return {"supported": True, "contract_version": 1, "formats": sorted(s.lstrip(".") for s in FORMATS),
            "max_upload_bytes": min(config.max_document_bytes, config.max_upload_bytes),
            "max_total_chars": config.max_document_chars, "max_sections": config.max_document_sections,
            "default_target_section_chars": 10000, "min_target_section_chars": 1000, "max_target_section_chars": 50000,
            "segmentation_modes": ["auto", "length"], "audio_format": "mp3", "resume": True,
            "download_modes": ["sections", "complete"], "streaming_downloads": True, "batch_download_range": False}


def import_root(config: Any, identifier: str) -> Path:
    if not identifier or any(c not in "0123456789abcdef" for c in identifier):
        raise HTTPException(404, "Document import not found")
    return config.data_dir / "documents" / identifier


def load_document(config: Any, identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    record = store.get_import(identifier)
    if record is None:
        raise HTTPException(404, "Document import not found")
    if record["state"] != "ready":
        raise HTTPException(409, "Document parsing is not ready")
    try:
        parsed = json.loads((import_root(config, identifier) / "parsed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Parsed document is unavailable; upload it again") from exc
    return record, parsed


def prepare(config: Any, identifier: str, revision: str, section_ids: list[str], mode: str, target: int) -> dict[str, Any]:
    record, parsed = load_document(config, identifier)
    try:
        preview = segment(parsed, mode, target, config.max_document_sections)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if preview["preview_revision"] != revision:
        raise HTTPException(409, "Segmentation preview changed; review the document again")
    if not section_ids or len(section_ids) != len(set(section_ids)):
        raise HTTPException(422, "Choose unique document section IDs")
    chosen = set(section_ids)
    sections = [p for p in preview["sections"] if p["id"] in chosen]
    if len(sections) != len(chosen):
        raise HTTPException(422, "Unknown document section ID")
    total = sum(p["char_count"] for p in sections)
    if not 0 < total <= config.max_document_chars:
        raise HTTPException(422, "Document text exceeds the configured limit")
    return {"record": record, "parsed": parsed, "sections": sections, "request": {
        "contract_version": 1, "import_id": identifier, "preview_revision": revision,
        "segmentation_mode": mode, "target_section_chars": target, "section_ids": [p["id"] for p in sections],
        "total_chars": total, "section_count": len(sections), "title": parsed["title"], "text_hash": parsed["text_hash"],
    }}


def snapshot(config: Any, job_id: str, prepared: dict[str, Any]) -> None:
    with store.import_files():
        root = config.jobs_dir / job_id / "input"
        root.mkdir(parents=True, exist_ok=True)
        (root / "document.txt").write_text(prepared["parsed"]["text"], encoding="utf-8")
        atomic_json(root / "document.json", {"contract_version": 1, "sections": prepared["sections"]})
        record = prepared["record"]
        shutil.copy2(import_root(config, record["id"]) / ("source" + record["suffix"]), root / ("document-source" + record["suffix"]))


def _stop_process(identity: dict[str, Any] | None) -> None:
    if not identity:
        return
    try:
        process = psutil.Process(identity["pid"])
        if abs(process.create_time() - identity["created_at"]) > .01 or process.exe() != identity["executable"]:
            return
        from .worker import _terminate_process_tree, _terminate_remaining
        remaining = _terminate_process_tree(process.pid)
        while remaining:
            remaining = _terminate_remaining(remaining)
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        pass


class ImportManager:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def run(self) -> None:
        for item in await asyncio.to_thread(store.imports):
            if item["state"] == "running":
                await asyncio.to_thread(_stop_process, item.get("process"))
                store.update_import(item["id"], state="queued", process_json=None)
        while True:
            pending = await asyncio.to_thread(store.pending_import)
            if pending:
                await self.parse(pending)
            else:
                await asyncio.sleep(.25)

    async def parse(self, item: dict[str, Any]) -> None:
        root = import_root(self.config, item["id"])
        identity = None
        process = None
        try:
            with (root / "parse.log").open("wb") as log:
                process = subprocess.Popen([sys.executable, "-m", "audio_intel.document_store", str(root / ("source" + item["suffix"])),
                    "--max-chars", str(self.config.max_document_chars), "--archive-limit", str(self.config.document_archive_bytes)],
                    cwd=self.config.root, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt")
                probe = psutil.Process(process.pid)
                identity = {"pid": process.pid, "created_at": probe.create_time(), "executable": probe.exe()}
                store.update_import(item["id"], state="running", process_json=json.dumps(identity))
                start = asyncio.get_running_loop().time()
                while process.poll() is None:
                    if asyncio.get_running_loop().time() - start > self.config.document_parse_seconds:
                        raise RuntimeError("Document parsing timed out / 文档解析超时")
                    try:
                        rss = sum(p.memory_info().rss for p in [probe, *probe.children(recursive=True)])
                        if rss > self.config.document_parse_memory_bytes:
                            raise RuntimeError("Document parser memory limit exceeded / 文档解析超过内存限制")
                    except psutil.NoSuchProcess:
                        pass
                    await asyncio.sleep(.1)
            if process.returncode:
                with (root / "parse.log").open("rb") as log:
                    log.seek(max(0, log.seek(0, 2) - 2000))
                    raise RuntimeError(log.read().decode("utf-8", errors="replace"))
            parsed = json.loads((root / "parsed.json").read_text(encoding="utf-8"))
            if parsed["title"] == "source":
                parsed["title"] = Path(item["name"]).stem
                atomic_json(root / "parsed.json", parsed)
            metadata = {"title": parsed["title"], "total_chars": len(parsed["text"]), "warnings": parsed["warnings"]}
            store.update_import(item["id"], state="ready", metadata_json=json.dumps(metadata), error=None, process_json=None)
        except asyncio.CancelledError:
            await asyncio.to_thread(_stop_process, identity)
            store.update_import(item["id"], state="queued", process_json=None)
            raise
        except Exception as exc:
            await asyncio.to_thread(_stop_process, identity)
            store.update_import(item["id"], state="failed", error=str(exc)[-2000:], process_json=None)
        finally:
            await asyncio.to_thread(_stop_process, identity)
            if process is not None:
                await asyncio.to_thread(process.wait)
            (root / "parsed.json.partial").unlink(missing_ok=True)


def register(app: FastAPI, api: Any) -> None:
    config = api.settings
    manager = ImportManager(config)
    prior_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with prior_lifespan(application):
            task = asyncio.create_task(manager.run())
            try:
                yield
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    app.router.lifespan_context = lifespan
    auth = Depends(api.require_api_key)
    responses = {**api.AUTH_RESPONSES, **api.VALIDATION_RESPONSE, **api.CONFLICT_RESPONSE, **api.NOT_FOUND_RESPONSE}

    # Same-key uploads serialize without retaining unbounded locks after completion.
    upload_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @app.post("/api/v1/tts/document-imports", status_code=202, tags=[api.TTS_TAG], response_model=DocumentImportResponse,
              responses={**responses, **api.IDEMPOTENCY_RESPONSES, **api.ADMISSION_RESPONSE, **api.TOO_LARGE_RESPONSE},
              openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
                  "type": "object", "required": ["file"], "additionalProperties": False,
                  "properties": {"file": {"type": "string", "format": "binary"}}}}}}},
              summary="导入文档 / Import a document", operation_id="importTtsDocument")
    async def upload(response: Response, request: Request,
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key"), _: None = auth):
        from .document_upload import receive_document
        from .db import connect, utcnow
        api.ensure_service("tts")
        key = api.idempotency_key_hash(api.validate_idempotency_key(idempotency_key))
        lock, users = upload_locks.get(key, (asyncio.Lock(), 0))
        upload_locks[key] = lock, users + 1
        try:
            async with lock:
                with connect() as db:
                    existing = db.execute("SELECT id,fingerprint FROM document_imports WHERE key_hash=?", (key,)).fetchone()
                limit = min(config.max_document_bytes, config.max_upload_bytes)
                if existing:
                    name, _, _, sha = await receive_document(request, None, limit)
                    if digest([name, sha]) != existing["fingerprint"]:
                        raise HTTPException(409, "Idempotency-Key was already used with a different document")
                    item = store.get_import(existing["id"])
                    if item is None:
                        raise HTTPException(409, "Document import was deleted; upload with a new key")
                    response.status_code = 200
                    response.headers["Idempotency-Replayed"] = "true"
                    return public_import(config, item)
                decision = await app.state.admission.reserve_upload(limit)
                if not decision.accepted:
                    raise api.admission_problem(decision, "tts")
                identifier = uuid.uuid4().hex
                root = import_root(config, identifier)
                try:
                    name, suffix, size, sha = await receive_document(request, root / "source.partial", limit)
                    (root / "source.partial").replace(root / ("source" + suffix))
                    with connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        db.execute("""INSERT INTO document_imports(id,name,suffix,state,key_hash,fingerprint,size_bytes,created_at,updated_at)
                            VALUES(?,?,?,'queued',?,?,?,?,?)""", (identifier, name, suffix, key, digest([name, sha]), size, utcnow(), utcnow()))
                        db.execute("COMMIT")
                    return public_import(config, store.get_import(identifier))
                except BaseException:
                    shutil.rmtree(root, ignore_errors=True)
                    raise
                finally:
                    import anyio
                    with anyio.CancelScope(shield=True):
                        await app.state.admission.release_upload(decision.reserved_bytes)
        finally:
            _, users = upload_locks[key]
            if users == 1:
                del upload_locks[key]
            else:
                upload_locks[key] = lock, users - 1

    @app.post("/api/v1/tts/document-imports/{identifier}/retry", status_code=202, tags=[api.TTS_TAG],
              response_model=DocumentImportResponse, responses={**responses, **api.ADMISSION_RESPONSE},
              summary="重新解析失败文档 / Retry failed document parsing", operation_id="retryTtsDocumentImport")
    async def retry_import(identifier: str, _: None = auth):
        from .db import connect, utcnow
        api.ensure_service("tts")
        item = store.get_import(identifier)
        if item is None:
            raise HTTPException(404, "Document import not found")
        if item["state"] != "failed":
            raise HTTPException(409, "Only failed document imports can be retried")
        decision = await app.state.admission.reserve_upload(0)
        if not decision.accepted:
            raise api.admission_problem(decision, "tts")
        try:
            with store.import_files(), connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if not (import_root(config, identifier) / ("source" + item["suffix"])).is_file():
                    raise HTTPException(409, "Document source is missing; upload the file again")
                changed = db.execute("UPDATE document_imports SET state='queued',error=NULL,process_json=NULL,updated_at=? WHERE id=? AND state='failed'", (utcnow(), identifier)).rowcount
                if not changed:
                    raise HTTPException(409, "Document import is no longer failed")
                db.execute("COMMIT")
            return public_import(config, store.get_import(identifier))
        finally:
            import anyio
            with anyio.CancelScope(shield=True):
                await app.state.admission.release_upload()

    @app.get("/api/v1/tts/document-imports", tags=[api.TTS_TAG], response_model=list[DocumentImportResponse], responses=responses)
    def list_imports(offset: int = Query(0, ge=0), limit: int | None = Query(None, ge=1, le=100), _: None = auth):
        return [public_import(config, item) for item in store.imports(offset, limit)]

    @app.get("/api/v1/tts/document-imports/{identifier}", tags=[api.TTS_TAG], response_model=DocumentImportResponse, responses=responses)
    def get_import(identifier: str, _: None = auth):
        item = store.get_import(identifier)
        if item is None:
            raise HTTPException(404, "Document import not found")
        return public_import(config, item)

    @app.delete("/api/v1/tts/document-imports/{identifier}", status_code=204, tags=[api.TTS_TAG], responses=responses)
    def delete_import(identifier: str, _: None = auth):
        from .db import connect
        with store.import_files(deleting=True), connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT state FROM document_imports WHERE id=?", (identifier,)).fetchone()
            if item is None:
                raise HTTPException(404, "Document import not found")
            if item["state"] in {"running", "queued"}:
                raise HTTPException(409, "Document import is active")
            root = import_root(config, identifier)
            if root.exists():
                shutil.rmtree(root)
            db.execute("DELETE FROM document_imports WHERE id=?", (identifier,))
            db.execute("COMMIT")
        return Response(status_code=204)

    @app.post("/api/v1/tts/document-imports/{identifier}/preview", tags=[api.TTS_TAG], response_model=DocumentPreview, responses=responses)
    def preview(identifier: str, payload: PreviewRequest, _: None = auth) -> dict[str, Any]:
        _, document = load_document(config, identifier)
        try:
            result = segment(document, payload.segmentation_mode, payload.target_section_chars, config.max_document_sections)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {**result, "title": document["title"], "warnings": document["warnings"]}

    @app.get("/api/v1/tts/document-imports/{identifier}/text", tags=[api.TTS_TAG], response_model=DocumentText, responses=responses)
    def text(identifier: str, start: int = Query(0, ge=0), limit: int = Query(4000, ge=1, le=20000), _: None = auth) -> dict[str, Any]:
        _, document = load_document(config, identifier)
        return {"text": document["text"][start:start + limit], "start": start, "total_chars": len(document["text"])}

    @app.get("/api/v1/jobs/{job_id}/document/sections", tags=[api.TTS_TAG], response_model=JobDocumentSections, responses=responses)
    def job_sections(job_id: str, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100), _: None = auth) -> dict[str, Any]:
        job = api.job_or_404(job_id)
        if job["request"].get("purpose") != "tts_document":
            raise HTTPException(404, "Not a document job")
        checkpoints = {p["id"]: p for p in store.sections(job_id)}
        try:
            manifest = json.loads((config.jobs_dir / job_id / "input/document.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(409, "Document snapshot is unavailable") from exc
        items = []
        for position, section in enumerate(manifest["sections"], 1):
            checkpoint = checkpoints.get(section["id"], {})
            items.append({**section, "job_id": job_id, "position": position,
                "start_offset": section["start"], "end_offset": section["end"],
                "state": checkpoint.get("state", "pending"), "retries": checkpoint.get("retries", 0),
                "artifact": checkpoint.get("artifact"), "updated_at": checkpoint.get("updated_at", job["created_at"])})
        return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit}

    @app.get("/api/v1/jobs/{job_id}/document/download", tags=[api.TTS_TAG], response_class=StreamingResponse,
              responses={**responses, **api.ADMISSION_RESPONSE, 200: {"description": "Streaming ZIP64 or MP3; no Range or stored export", "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}, "audio/mpeg": {"schema": {"type": "string", "format": "binary"}}}}})
    async def download(job_id: str, mode: Literal["sections", "complete"], _: None = auth):
        from .document_download import lease, zip_stream, mp3_stream, validate_mp3
        job = await run_in_threadpool(api.job_or_404, job_id)
        if job["request"].get("purpose") != "tts_document" or job["state"] != "succeeded":
            raise HTTPException(409, "Document synthesis has not completed")
        guard = lease(job_id, config.max_document_downloads)
        try:
            guard.__enter__()
        except RuntimeError as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After": "5"}) from exc
        try:
            files = []
            for artifact in job["result"]["artifacts"]:
                path = Path(artifact["path"]).resolve()
                if (config.jobs_dir / job_id / "output").resolve() not in path.parents or not path.is_file() or path.stat().st_size != artifact["size_bytes"]:
                    raise HTTPException(404, "Document audio is missing or changed")
                files.append((artifact["name"], path))
            if mode == "complete":
                try:
                    await run_in_threadpool(validate_mp3, files)
                except (ValueError, OSError) as exc:
                    raise HTTPException(409, "Document audio is invalid or incompatible") from exc
            iterator = zip_stream(files) if mode == "sections" else mp3_stream(files)

            def next_chunk():
                return next(iterator, None)

            async def body():
                try:
                    while True:
                        chunk = await run_in_threadpool(next_chunk)
                        if chunk is None:
                            break
                        yield chunk
                finally:
                    # A shield ensures cancellation cannot skip generator/handle cleanup.
                    import anyio
                    with anyio.CancelScope(shield=True):
                        await run_in_threadpool(iterator.close)
                        guard.__exit__(None, None, None)
            filename = api.safe_filename(job["display_name"]) + (".zip" if mode == "sections" else ".mp3")
            return StreamingResponse(body(), media_type="application/zip" if mode == "sections" else "audio/mpeg",
                headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename), "Cache-Control": "no-store", "Accept-Ranges": "none", "X-Accel-Buffering": "no"})
        except BaseException:
            guard.__exit__(None, None, None)
            raise


def enrich_document_docs(schema: dict[str, Any]) -> None:
    descriptions = {
        ("/api/v1/tts/document-imports/{identifier}/retry", "post"): ("重试失败解析，复用源文件并检查准入，不要求幂等键。", "Retry a failed import using its persisted source and the same admission limits. Returns 202 or 409 when no longer failed; no Idempotency-Key is required."),
        ("/api/v1/tts/document-imports", "post"): ("上传文档并异步解析，不占用 GPU。需要 Idempotency-Key，首次返回 202，同内容重放返回 200。", "Upload EPUB, TXT, text PDF, Markdown, DOCX, XLSX or PPTX for asynchronous offline parsing without GPU use. Idempotency-Key is required: first acceptance is 202, identical replay is 200, conflict is 409. Poll the import, then request a segmentation preview."),
        ("/api/v1/tts/document-imports", "get"): ("列出导入状态，不包含正文。", "List document import metadata and parsing states without full text."),
        ("/api/v1/tts/document-imports/{identifier}", "get"): ("查询解析状态与警告。", "Poll queued, running, ready or failed parsing status and extraction warnings."),
        ("/api/v1/tts/document-imports/{identifier}", "delete"): ("删除非活动导入及其文件，已提交任务保留独立快照。", "Delete an inactive import and its files. Submitted jobs keep independent source snapshots; active imports return 409."),
        ("/api/v1/tts/document-imports/{identifier}/preview", "post"): ("按结构或目标字数切分，返回版本及分段偏移，不包含全文。", "Choose auto or length segmentation with a 1000–50000 character target. Returns immutable preview revision, section IDs, offsets, character counts and boundary basis. Concatenating sections reproduces canonical spoken text. Review extraction warnings and forced boundaries before submission."),
        ("/api/v1/tts/document-imports/{identifier}/text", "get"): ("按偏移读取正文，每次最多 20000 字符。", "Lazily read canonical text by character offset, at most 20000 characters per response."),
        ("/api/v1/tts/document-jobs", "post"): ("提交文档分段 TTS，共享现有队列及音色能力。完成段持久化为 MP3，重试时保留。", "Submit document_import_id, preview_revision and unique section_ids with the preview segmentation settings. One shared model, language, device and voice configuration; existing voice capabilities apply. Reject undeclared fields. MP3 only; completed sections persist across manual retry. Requires Idempotency-Key (202/200/409) and normal queue/disk admission (429). Full text is snapshotted on disk rather than request JSON."),
        ("/api/v1/jobs/{job_id}/document/sections", "get"): ("分页查看分段检查点及完成状态。", "Read paginated section checkpoints, including pending/generating/complete state, retry count and completed artifact metadata."),
        ("/api/v1/jobs/{job_id}/document/download", "get"): ("即时流式下载分段 ZIP 或完整 MP3，不生成缓存文件，不支持 Range。", "Stream ZIP_STORED ZIP64 sections or packet-remuxed complete MP3. No re-synthesis, re-encoding or stored export. Batch downloads do not support Range; restart interrupted batch downloads from the beginning. Individual MP3 artifacts support Range. Bounded backpressure, download concurrency admission (429), disconnect cleanup and purge exclusion apply."),
    }
    for (path, method), (zh, en) in descriptions.items():
        operation = schema["paths"][path][method]
        operation["description"] = zh + "\n\n**English:** " + en
        operation.setdefault("summary", zh + " / " + en.split(".", 1)[0])

    upload = schema["paths"]["/api/v1/tts/document-imports"]["post"]
    for parameter in upload.get("parameters", []):
        if parameter["name"] == "Idempotency-Key":
            parameter["required"] = True
            parameter["schema"] = {"type": "string", "minLength": 8, "maxLength": 128}
    upload["responses"]["200"] = {"description": "Identical import replay / 相同导入重放", "content": {
        "application/json": {"schema": {"$ref": "#/components/schemas/DocumentImportResponse"}}}}
