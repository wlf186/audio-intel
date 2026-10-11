"""Authenticated library transfer routes and a single offline background manager."""
from __future__ import annotations

import asyncio
import json
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from . import db as database, library_store as store, library_transfer as transfer
from .document_upload import receive_document
from .library_models import LibraryApplyRequest, LibraryImportResponse, LibraryModeRequest, LibraryPreview

TAG = "Library / 资料库"


def public(config: Any, item: dict[str, Any]) -> dict[str, Any]:
    result = {key: item.get(key) for key in ("id", "name", "state", "size_bytes", "created_at", "updated_at", "mode", "error", "preview", "result")}
    if transfer.backup_path(config, item["id"]).is_file():
        result["backup_url"] = f"/api/v1/library/imports/{item['id']}/backup"
    return result


def expire(config: Any) -> None:
    with database.connect() as db:
        items = [dict(row) for row in db.execute("SELECT id,updated_at FROM library_imports WHERE state IN ('ready','failed')")]
    for item in items:
        if time.time() - datetime.fromisoformat(item["updated_at"]).timestamp() > 86400:
            transfer.cleanup(config, item["id"], False)
            task_root = store.root(config, item["id"])
            if not (task_root / "files.json").exists():
                shutil.rmtree(task_root, ignore_errors=True)
                store.update(item["id"], state="expired", preview_json=None)


def register(app: FastAPI, api: Any) -> None:
    config = api.settings
    app.state.library_changing = False
    app.state.library_requests = 0
    prior_lifespan = app.router.lifespan_context

    async def manager():
        await asyncio.to_thread(transfer.recover, config)
        last_cleanup = 0.0
        while True:
            with database.connect() as db:
                item = db.execute("SELECT id,state FROM library_imports WHERE state IN ('queued','applying') ORDER BY (state='applying') DESC,created_at LIMIT 1").fetchone()
            if item:
                function = transfer.apply if item["state"] == "applying" else transfer.validate
                task = asyncio.create_task(asyncio.to_thread(function, config, item["id"]))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task  # Do not strand a file transaction on graceful shutdown.
                    raise
                finally:
                    if item["state"] == "applying":
                        app.state.library_changing = False
            else:
                if time.monotonic() - last_cleanup > 60:
                    await asyncio.to_thread(expire, config)
                    last_cleanup = time.monotonic()
                await asyncio.sleep(.25)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with prior_lifespan(application):
            task = asyncio.create_task(manager())
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
    responses = {**api.AUTH_RESPONSES, **api.CONFLICT_RESPONSE, **api.VALIDATION_RESPONSE, **api.NOT_FOUND_RESPONSE}
    upload_locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @app.get("/api/v1/library/export", tags=[TAG], operation_id="exportLibrary", responses={**responses, **api.TOO_LARGE_RESPONSE,
              200: {"description": "完整资料库 ZIP / Complete library ZIP", "content": {"application/zip": {"schema": api.BINARY_SCHEMA}}}},
              summary="导出完整资料库 / Export complete library",
              description="Includes people, stable samples/audio and custom hotwords. Pending enrollment returns 409; missing ready audio is never silently skipped. System name lists are regenerated on import. / 包含人员、稳定样本音频和自定义热词；入库未完成或可用音频缺失返回 409。")
    async def export(_: None = auth):
        if app.state.library_changing:
            raise HTTPException(409, "Library is being updated; retry shortly")
        stack, manifest, files = await asyncio.to_thread(transfer.prepare_export, config)
        filename = "audio-intel-library-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
        return StreamingResponse(transfer.export_stream(stack, manifest, files), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
                                 background=BackgroundTask(stack.close))

    @app.post("/api/v1/library/imports", status_code=202, response_model=LibraryImportResponse, tags=[TAG], operation_id="importLibrary",
              summary="上传完整资料包 / Upload complete library archive",
              description="Requires Idempotency-Key. First acceptance 202, same-file replay 200, conflict 409. Validation runs offline without GPU; poll the receipt, then preview and apply. / 需要幂等键；上传后后台校验，须预览并选择合并或替换后应用。",
              responses={**responses, **api.IDEMPOTENCY_RESPONSES, **api.ADMISSION_RESPONSE, **api.TOO_LARGE_RESPONSE},
              openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {"type": "object", "required": ["file"],
                  "additionalProperties": False, "properties": {"file": {"type": "string", "format": "binary"}}}}}}})
    async def upload(response: Response, request: Request, idempotency_key: str | None = Header(None, alias="Idempotency-Key", json_schema_extra={"minLength": 8, "maxLength": 128}), _: None = auth):
        key = api.idempotency_key_hash(api.validate_idempotency_key(idempotency_key))
        lock, users = upload_locks.get(key, (asyncio.Lock(), 0))
        upload_locks[key] = lock, users + 1
        try:
            async with lock:
                with database.connect() as db:
                    existing = db.execute("SELECT id,fingerprint FROM library_imports WHERE key_hash=?", (key,)).fetchone()
                if existing:
                    _, _, _, sha = await receive_document(request, None, config.max_upload_bytes, formats={".zip"})
                    if sha != existing["fingerprint"]:
                        raise HTTPException(409, "Idempotency-Key was used with a different archive")
                    item = store.get(existing["id"])
                    if item["state"] == "expired":
                        raise HTTPException(409, "Archive expired or was removed; upload with a new key")
                    response.status_code = 200
                    response.headers["Idempotency-Replayed"] = "true"
                    return public(config, item)
                try:
                    expected = max(0, int(request.headers.get("content-length", str(config.max_upload_bytes))))
                except ValueError:
                    raise HTTPException(400, "Invalid Content-Length")
                decision = await app.state.admission.reserve_upload(expected)
                if not decision.accepted:
                    raise api.admission_problem(decision, "asr")
                identifier = uuid.uuid4().hex
                task_root = store.root(config, identifier)
                try:
                    name, _, size, sha = await receive_document(request, task_root / "source.partial", config.max_upload_bytes, formats={".zip"})
                    (task_root / "source.partial").replace(task_root / "source.zip")
                    with database.connect() as db:
                        db.execute("INSERT INTO library_imports(id,name,state,key_hash,fingerprint,size_bytes,created_at,updated_at) VALUES(?,?,'queued',?,?,?,?,?)",
                                   (identifier, name, key, sha, size, database.utcnow(), database.utcnow()))
                    return public(config, store.get(identifier))
                except BaseException:
                    shutil.rmtree(task_root, ignore_errors=True)
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

    @app.get("/api/v1/library/imports", response_model=list[LibraryImportResponse], tags=[TAG], operation_id="listLibraryImports",
              summary="查询资料导入 / List library imports", description="Recent persisted receipts; reloads can resume monitoring. / 最近的持久化导入记录，刷新页面后可继续查看。", responses=responses)
    def imports(limit: int = Query(20, ge=1, le=100), _: None = auth):
        with database.connect() as db:
            identifiers = [row[0] for row in db.execute("SELECT id FROM library_imports ORDER BY created_at DESC LIMIT ?", (limit,))]
        return [public(config, store.get(identifier)) for identifier in identifiers]

    @app.get("/api/v1/library/imports/{identifier}", response_model=LibraryImportResponse, tags=[TAG], operation_id="getLibraryImport",
              summary="查询导入状态 / Get library import", description="Queued, validating, ready, applying, completed, failed or expired. / 返回校验、预览、应用状态和错误。", responses=responses)
    def detail(identifier: str, _: None = auth):
        return public(config, store.get(identifier))

    def preview_plan(identifier: str, mode: str):
        item = store.get(identifier)
        if item["state"] != "ready":
            raise HTTPException(409, "Only a validated, unapplied archive can be previewed")
        try:
            plan = store.build_plan(store.load_archive(config, identifier), transfer.current_snapshot(), mode)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        store.update(identifier, mode=mode, preview_json=json.dumps(plan["preview"], ensure_ascii=False), error=None)
        return plan["preview"]

    @app.post("/api/v1/library/imports/{identifier}/preview", response_model=LibraryPreview, tags=[TAG], operation_id="previewLibraryImport",
              summary="预览合并或替换 / Preview merge or replacement", description="Choose merge (default) or replace. Returns source/target counts and a revision bound to semantic library contents. / 默认合并，返回影响数量与绑定库内容的预览版本。", responses=responses)
    async def preview(identifier: str, payload: LibraryModeRequest, _: None = auth):
        if app.state.library_changing:
            raise HTTPException(409, "Library is being updated")
        return await asyncio.to_thread(preview_plan, identifier, payload.mode)

    @app.post("/api/v1/library/imports/{identifier}/apply", status_code=202, response_model=LibraryImportResponse, tags=[TAG], operation_id="applyLibraryImport",
              summary="应用资料包 / Apply library archive",
              description="Requires the matching preview revision. Replacement requires idle queues and creates a downloadable backup first. Same completed mode/revision replays 200; changed previews return 409. / 需匹配预览版本；替换要求任务空闲并先备份，重复完成请求返回 200。", responses={**responses, **api.ADMISSION_RESPONSE})
    async def apply(identifier: str, payload: LibraryApplyRequest, response: Response, _: None = auth):
        item = store.get(identifier)
        if item["state"] == "completed":
            if item["mode"] != payload.mode or item["preview"]["preview_revision"] != payload.preview_revision:
                raise HTTPException(409, "Import was already applied with a different preview")
            response.status_code = 200
            return public(config, item)
        if app.state.library_changing or item["state"] != "ready":
            raise HTTPException(409, "Import is not ready or another import is applying")
        app.state.library_changing = True
        accepted = False
        try:
            if app.state.library_requests or app.state.admission.active:
                raise HTTPException(409, "Wait for in-flight submissions or library edits to complete")
            if payload.mode == "replace":
                with database.connect() as db:
                    store.require_idle(db)
                    store.require_complete(db)
            current = await asyncio.to_thread(transfer.current_snapshot)
            try:
                plan = await asyncio.to_thread(store.build_plan, store.load_archive(config, identifier), current, payload.mode)
            except ValueError as exc:
                raise HTTPException(409, "Library changed; refresh the preview: " + str(exc)) from exc
            if not item["preview"] or item["mode"] != payload.mode or plan["preview"]["preview_revision"] != payload.preview_revision or item["preview"]["preview_revision"] != payload.preview_revision:
                raise HTTPException(409, "Library changed; refresh the preview / 资料库已变化，请刷新预览")
            store.update(identifier, state="applying", mode=payload.mode, error=None)
            accepted = True
            return public(config, store.get(identifier))
        finally:
            if not accepted:
                app.state.library_changing = False

    @app.get("/api/v1/library/imports/{identifier}/backup", tags=[TAG], operation_id="downloadLibraryBackup",
              summary="下载替换前备份 / Download replacement backup", description="Protected complete ZIP of the previous libraries; backups are not automatically deleted. / 受鉴权保护的替换前完整备份，不自动清理。",
              responses={**responses, 200: {"description": "ZIP", "content": {"application/zip": {"schema": api.BINARY_SCHEMA}}}})
    def backup(identifier: str, _: None = auth):
        store.get(identifier)
        path = transfer.backup_path(config, identifier)
        if not path.is_file():
            raise HTTPException(404, "Replacement backup not available")
        return FileResponse(path, media_type="application/zip", filename=f"audio-intel-library-backup-{identifier}.zip", headers={"Cache-Control": "no-store"})

    @app.delete("/api/v1/library/imports/{identifier}", status_code=204, tags=[TAG], operation_id="removeLibraryImport",
                summary="清理导入文件 / Remove staged archive", description="Active validation/application cannot be removed. Keeps completed receipts and replacement backups. / 活动操作不可删除；保留完成记录和替换备份。", responses=responses)
    def remove(identifier: str, _: None = auth):
        item = store.get(identifier)
        if item["state"] in {"queued", "validating", "applying"}:
            raise HTTPException(409, "Import is active")
        transfer.cleanup(config, identifier, item["state"] == "completed")
        if (store.root(config, identifier) / "files.json").exists():
            raise HTTPException(409, "File rollback or cleanup is pending")
        shutil.rmtree(store.root(config, identifier), ignore_errors=True)
        if item["state"] != "completed":
            store.update(identifier, state="expired", preview_json=None)

    for route in app.routes:
        if TAG in getattr(route, "tags", []) and route.description:
            english, chinese = route.description.split(" / ", 1)
            route.description = api.bilingual(chinese, english)
