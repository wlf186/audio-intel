"""Bounded, disposable CPU previews; only explicit revision-checked apply edits a job."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import psutil
from fastapi import Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from . import db
from .asr_speakers import speaker_update_lock
from .document_download import reader_lease
from .video_detection import SceneDetectionSettings, VERSION
from .api_models import VideoSceneResponse

TTL = 1800


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    settings: SceneDetectionSettings


class PreviewProgress(BaseModel):
    phase: Literal['scanning', 'detecting', 'selecting', 'images']
    completed: float
    total: float


class PreviewScene(VideoSceneResponse):
    preview_url: str | None


class PreviewResponse(BaseModel):
    contract_version: Literal[2] = 2
    id: str
    job_id: str
    revision: int
    state: Literal['running', 'ready', 'failed', 'cancelled', 'applying', 'applied']
    settings: SceneDetectionSettings
    expires_at: float
    progress: PreviewProgress | None = None
    error: str | None = None
    scenes: list[PreviewScene] | None = None
    cache_hit: bool | None = None


@contextmanager
def task_reader(job_id: str):
    try:
        with reader_lease(job_id):
            yield
    except FileNotFoundError as exc:
        raise HTTPException(404, 'Task or preview is no longer available') from exc


def completed(job_id: str) -> dict:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(404, 'Task not found')
    if job['state'] != 'succeeded' or not (job.get('result') or {}).get('video'):
        raise HTTPException(409, 'A completed video transcription is required')
    return job


class PreviewManager:
    def __init__(self, config: Any):
        self.config = config
        self.items: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.tasks: dict[str, asyncio.Task] = {}
        self.sweeper: asyncio.Task | None = None

    async def start(self):
        # An interrupted preview never becomes an applied result after restart.
        from .document_api import _stop_process
        for path in self.config.temp_dir.glob('*/scene-previews/*'):
            if path.is_dir() and not path.is_symlink():
                identity = path / 'process.json'
                if identity.is_file():
                    try:
                        await asyncio.to_thread(_stop_process, json.loads(identity.read_text(encoding='utf-8')))
                    except (OSError, ValueError, KeyError):
                        pass
                for partial in (self.config.jobs_dir / path.parent.parent.name / 'output').glob('scene-features.partial-*'):
                    shutil.rmtree(partial, ignore_errors=True)
                shutil.rmtree(path, ignore_errors=True)
        self.sweeper = asyncio.create_task(self.sweep())

    async def sweep(self):
        while True:
            await asyncio.sleep(30)
            with self.lock:
                for key, item in list(self.items.items()):
                    if item['state'] not in {'running', 'applying'} and item['expires_at'] < time.time():
                        shutil.rmtree(item['folder'], ignore_errors=True)
                        del self.items[key]

    async def close(self):
        if self.sweeper:
            self.sweeper.cancel()
            await asyncio.gather(self.sweeper, return_exceptions=True)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for item in self.items.values():
            shutil.rmtree(item['folder'], ignore_errors=True)
        self.items.clear()

    def public(self, item: dict) -> dict:
        result = {k: item[k] for k in ('id', 'job_id', 'revision', 'state', 'settings', 'expires_at')}
        for key in ('progress', 'error', 'cache_hit'):
            if key in item:
                result[key] = item[key]
        if item['state'] in {'ready', 'applied'}:
            result['scenes'] = [
                {**s, 'preview_url': f"/api/v1/jobs/{item['job_id']}/video-scene-previews/{item['id']}/frames/{s['image']}" if s['image'] else None}
                for s in item['scenes']]
        return result

    def get(self, job_id: str, identifier: str) -> dict:
        with self.lock:
            item = self.items.get(identifier)
            if not item or item['job_id'] != job_id or (item['expires_at'] < time.time() and item['state'] not in {'running', 'applying'}):
                raise HTTPException(410, 'Preview expired; create a new preview')
            return item

    def create(self, job_id: str, body: PreviewRequest, key: str) -> tuple[dict, bool]:
        with self.lock, task_reader(job_id):
            job = completed(job_id)
            fingerprint = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
            key_hash = hashlib.sha256(key.encode()).hexdigest()
            for item in self.items.values():
                if item['job_id'] == job_id and item['key_hash'] == key_hash and (item['expires_at'] >= time.time() or item['state'] in {'running', 'applying'}):
                    if item['fingerprint'] != fingerprint:
                        raise HTTPException(409, 'Idempotency-Key conflict')
                    return item, True
            if job['result']['video']['revision'] != body.revision:
                raise HTTPException(409, 'Sections changed; reload before previewing')
            if self.tasks or any(item['state'] == 'applying' for item in self.items.values()):
                raise HTTPException(429, 'Video analysis is busy; retry shortly', headers={'Retry-After': '2'})
            root = self.config.jobs_dir / job_id
            if root.resolve().parent != self.config.jobs_dir.resolve():
                raise HTTPException(404, 'Task not found')
            source = Path(job['request']['input_path'])
            if source.resolve().parent != (root / 'input').resolve() or not source.is_file():
                raise HTTPException(409, 'Original video is unavailable')
            if shutil.disk_usage(self.config.data_dir).free <= self.config.min_free_disk_bytes:
                raise HTTPException(429, 'Insufficient storage for preview', headers={'Retry-After': '60'})
            while len(self.items) >= 8:
                oldest = min(self.items.values(), key=lambda v: v['expires_at'])
                shutil.rmtree(oldest['folder'], ignore_errors=True)
                del self.items[oldest['id']]
            identifier = uuid.uuid4().hex
            folder = self.config.temp_dir / job_id / 'scene-previews' / identifier
            folder.mkdir(parents=True)
            video = job['result']['video']
            request = {'source': str(source), 'cache': str(root / 'output/scene-features'),
                       'existing_frames': str(root / 'output/frames'),
                       'existing_scenes': video['scenes'] + video['original_scenes'] + (video.get('automatic_scenes') or []),
                       'roi': video.get('roi'), 'duration': video['duration'], 'time_origin': video.get('time_origin', 0),
                       'settings': body.settings.model_dump(), 'min_free': self.config.min_free_disk_bytes}
            (folder / 'request.json').write_text(json.dumps(request), encoding='utf-8')
            item = {'id': identifier, 'job_id': job_id, 'revision': body.revision, 'state': 'running',
                    'settings': body.settings.model_dump(), 'expires_at': time.time() + TTL, 'folder': folder,
                    'key_hash': key_hash, 'fingerprint': fingerprint}
            self.items[identifier] = item
            self.tasks[identifier] = asyncio.create_task(self.run(item))
            return item, False

    async def run(self, item: dict):
        try:
            # Keep deletion blocked until the complete process tree has exited.
            with task_reader(item['job_id']):
                await self._run(item)
        except HTTPException:
            item.update(state='failed', error='Task is no longer available')
        finally:
            self.tasks.pop(item['id'], None)

    async def _run(self, item: dict):
        from .document_api import _stop_process
        process = None
        identity = None
        folder = item['folder']
        try:
            completed(item['job_id'])
            with (folder / 'analysis.log').open('wb') as log:
                process = subprocess.Popen([sys.executable, '-m', 'audio_intel.video_detection', str(folder)],
                    cwd=self.config.root, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != 'nt')
                probe = psutil.Process(process.pid)
                identity = {'pid': process.pid, 'created_at': probe.create_time(), 'executable': probe.exe()}
                (folder / 'process.json').write_text(json.dumps(identity), encoding='utf-8')
                started = time.monotonic()
                while process.poll() is None:
                    self.drain(item)
                    # Bounded memory and runtime even for malformed media.
                    try:
                        memory = probe.memory_info().rss
                    except psutil.NoSuchProcess:
                        memory = 0
                    if time.monotonic() - started > 3600 or memory > 1024**3:
                        raise RuntimeError('Video preview resource limit exceeded')
                    await asyncio.sleep(.1)
            self.drain(item)
            if process.returncode:
                raise RuntimeError('Video analysis failed; retry the preview')
            result = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
            with self.lock:
                item.update(state='ready', scenes=result['scenes'], cache_hit=result['cache_hit'], expires_at=time.time() + TTL)
        except asyncio.CancelledError:
            if identity:
                await asyncio.to_thread(_stop_process, identity)
            item.update(state='cancelled')
            raise
        except Exception:
            if identity:
                await asyncio.to_thread(_stop_process, identity)
            item.update(state='failed', error='Video analysis failed; retry the preview')
        finally:
            if identity:
                await asyncio.to_thread(_stop_process, identity)
            if process:
                await asyncio.to_thread(process.wait)
            (folder / 'process.json').unlink(missing_ok=True)
            # A killed builder's unpublished cache is not reusable.
            for path in (self.config.jobs_dir / item['job_id'] / 'output').glob('scene-features.partial-*'):
                shutil.rmtree(path, ignore_errors=True)

    def drain(self, item: dict):
        for path in sorted(item['folder'].glob('progress-*.json')):
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                with self.lock:
                    item['progress'] = value
                path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass  # Best-effort progress must never fail decoding.

    async def discard(self, job_id: str, identifier: str):
        with self.lock:
            item = self.get(job_id, identifier)
            if item['state'] == 'applying':
                raise HTTPException(409, 'Preview is being applied')
            # Prevent an apply thread from acquiring a ready preview while its
            # files are being discarded (including while cancellation awaits).
            item['state'] = 'cancelled'
            task = self.tasks.get(identifier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with self.lock:
            self.tasks.pop(identifier, None)
            shutil.rmtree(item['folder'], ignore_errors=True)
            self.items.pop(identifier, None)

    def apply(self, job_id: str, identifier: str | None, revision: int) -> dict:
        with self.lock:
            item = self.get(job_id, identifier or '')
            if item['state'] != 'ready':
                raise HTTPException(409, 'Preview is not ready')
            item['state'] = 'applying'
        created: list[Path] = []
        try:
            with speaker_update_lock(job_id), task_reader(job_id):
                job = completed(job_id)
                result = copy.deepcopy(job['result'])
                video = result['video']
                if revision != item['revision'] or revision != video['revision']:
                    raise HTTPException(409, 'Sections changed; create a new preview')
                folder = self.config.jobs_dir / job_id / 'output/frames'
                folder.mkdir(parents=True, exist_ok=True)
                required = sum((item['folder'] / 'frames' / s['image']).stat().st_size for s in item['scenes']
                               if s['image'] and not (folder / s['image']).exists())
                if shutil.disk_usage(folder).free - required < self.config.min_free_disk_bytes:
                    raise HTTPException(429, 'Insufficient storage to apply preview', headers={'Retry-After': '60'})
                for scene in item['scenes']:
                    if not scene['image']:
                        continue
                    target = folder / scene['image']
                    if not target.exists():
                        created.append(target)
                        shutil.copyfile(item['folder'] / 'frames' / scene['image'], target)
                from .video_visibility import retained_hidden
                hidden = retained_hidden(video, item['scenes']) if video.get('scene_detection') == item['settings'] else []
                video.update(export_hidden_scene_ids=hidden)
                video.update(scenes=copy.deepcopy(item['scenes']), automatic_scenes=copy.deepcopy(item['scenes']),
                             scene_detection=item['settings'], detector=VERSION, contract_version=2, revision=revision + 1,
                             applied_preview_id=item['id'])
                with db.connect() as connection:
                    changed = connection.execute("UPDATE jobs SET result_json=?,updated_at=? WHERE id=? AND state='succeeded' AND updated_at=?",
                        (json.dumps(result, ensure_ascii=False), db.utcnow(), job_id, job['updated_at']))
                    if changed.rowcount != 1:
                        raise HTTPException(409, 'Task changed; create a new preview')
                created.clear()
                retained = {s['image'] for s in video['scenes'] + video['original_scenes'] + video['automatic_scenes']}
                for path in folder.glob('*.jpg'):
                    if path.name not in retained:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            pass  # Committed results remain successful; later edits retry cleanup.
                item['state'] = 'applied'
                return result
        finally:
            for path in created:
                path.unlink(missing_ok=True)
            if item['state'] == 'applying':
                item['state'] = 'ready'


def register_previews(app: Any, api: Any, manager: PreviewManager):
    auth = Depends(api.require_api_key)
    root = '/api/v1/jobs/{job_id}/video-scene-previews'
    errors = {401: {'description': 'Unauthorized'}, 409: {'description': 'Revision or state conflict'},
              410: {'description': 'Preview expired'}, 429: {'description': 'Busy; honor Retry-After'}}

    @app.post(root, tags=[api.ASR_TAG], response_model=PreviewResponse, status_code=202, responses={
        **errors, 200: {'model': PreviewResponse, 'description': 'Idempotent replay'}},
        summary='预览视频画面检测 / Preview picture detection',
        description='本地 CPU 分析，原结果不变。需要 Idempotency-Key；同 key 同参数重放返回 200。最多一个分析，预览保留 30 分钟；重启失效。\n\n**English:** Local CPU analysis without editing the result. Requires Idempotency-Key. Same-request replay is 200. One active analysis; previews expire after 30 minutes or restart.')
    async def create(job_id: str, body: PreviewRequest, response: Response, idempotency_key: str = Header(..., alias='Idempotency-Key'), _: None = auth):
        key = api.validate_idempotency_key(idempotency_key)
        item, replayed = manager.create(job_id, body, key)
        if replayed:
            response.status_code = 200
        return manager.public(item)

    @app.get(root + '/{preview_id}', tags=[api.ASR_TAG], response_model=PreviewResponse, responses=errors,
             description='返回真实分析进度；ready 后返回候选画面。重启或过期后重新预览。\n\n**English:** Read actual analysis progress and candidate pictures when ready. Create a new preview after expiry or restart.',
             summary='读取检测预览进度 / Read detection preview progress')
    def read(job_id: str, preview_id: str, _: None = auth):
        completed(job_id)
        return manager.public(manager.get(job_id, preview_id))

    @app.delete(root + '/{preview_id}', tags=[api.ASR_TAG], status_code=204, responses=errors,
                description='等待分析进程退出后删除预览，不修改转写结果。\n\n**English:** Wait for the analysis process to exit, then discard the preview without editing the transcript.',
                summary='取消或丢弃检测预览 / Cancel or discard detection preview')
    async def discard(job_id: str, preview_id: str, _: None = auth):
        await manager.discard(job_id, preview_id)
        return Response(status_code=204)

    @app.get(root + '/{preview_id}/frames/{name}', tags=[api.ASR_TAG], response_class=Response, responses={
        **errors, 200: {'content': {'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}}},
        summary='读取检测预览画面 / Read a detection preview image',
        description='仅返回该预览已生成的 JPEG，不能读取任意路径。\n\n**English:** Return a JPEG generated for this preview; arbitrary file paths are not accepted.')
    def image(job_id: str, preview_id: str, name: str, _: None = auth):
        with manager.lock, task_reader(job_id):
            completed(job_id)
            item = manager.get(job_id, preview_id)
            if item['state'] not in {'ready', 'applied'} or name not in {s['image'] for s in item['scenes']} or Path(name).name != name:
                raise HTTPException(404, 'Preview image not found')
            return Response((item['folder'] / 'frames' / name).read_bytes(), media_type='image/jpeg',
                            headers={'Cache-Control': 'no-store'})
