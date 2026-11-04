"""Bounded multipart audio reception directly on the task data filesystem."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import uuid

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser, _user_safe_decode
from python_multipart.multipart import parse_options_header
from python_multipart.exceptions import MultipartParseError

OVERHEAD = 1024**2
BLOCK = 256 * 1024


def staging_directory(config) -> Path:
    return config.jobs_dir / '.audio-upload-staging'


def clean_staging(config) -> None:
    folder = staging_directory(config)
    if not folder.exists() or folder.is_symlink():
        return
    for path in folder.iterdir():
        if re.fullmatch(r'[a-f0-9]{32}\.partial', path.name) and not path.is_symlink() and path.is_file():
            path.unlink()


class StagedAudio(UploadFile):
    def __init__(self, path: Path, limit: int, filename: str, headers: Headers):
        super().__init__(file=path.open('xb'), size=0, filename=filename, headers=headers)
        self.path = path
        self.limit = limit
        self.digest = hashlib.sha256()
        self.target: Path | None = None

    async def write(self, data: bytes) -> None:
        if (self.size or 0) + len(data) > self.limit:
            from .api import ApiProblem
            raise ApiProblem(413, 'upload_too_large', 'Uploaded file is too large')
        await super().write(data)
        self.digest.update(data)

    async def adopt(self, target: Path, limit: int | None) -> tuple[int, str]:
        if limit is not None and (self.size or 0) > limit:
            from .api import ApiProblem
            raise ApiProblem(413, 'upload_too_large', 'Uploaded file is too large')
        await self.close()  # Windows requires closure before rename.
        self.target = target
        await run_in_threadpool(os.replace, self.path, target)
        return self.size or 0, self.digest.hexdigest()

    def cleanup(self) -> None:
        self.file.close()
        self.path.unlink(missing_ok=True)
        if self.target is not None:
            from .db import connect
            job_root = self.target.parent.parent
            with connect() as connection:
                accepted = connection.execute('SELECT 1 FROM jobs WHERE id=?', (job_root.name,)).fetchone()
            if not accepted:
                self.target.unlink(missing_ok=True)
                for folder in (self.target.parent, job_root):
                    try:
                        folder.rmdir()
                    except (FileNotFoundError, OSError):
                        pass


class AudioParser(MultiPartParser):
    def __init__(self, request: Request, config):
        self.config = config
        self.uploads: list[StagedAudio] = []
        self.ended = False

        async def bounded_stream():
            total = 0
            async for incoming in request.stream():
                for offset in range(0, len(incoming), BLOCK):
                    chunk = incoming[offset:offset + BLOCK]
                    total += len(chunk)
                    if total > config.max_upload_bytes + OVERHEAD:
                        raise HTTPException(413, 'Audio multipart upload exceeds the limit')
                    yield chunk
                    size = sum(upload.size or 0 for upload in self.uploads)
                    if total - size > OVERHEAD:
                        raise HTTPException(413, 'Audio multipart overhead exceeds the limit')

        super().__init__(request.headers, bounded_stream(), max_files=1)

    def on_headers_finished(self) -> None:
        _, options = parse_options_header(self._current_part.content_disposition)
        if b'filename' not in options:
            super().on_headers_finished()
            return
        if options.get(b'name') != b'file' or self.uploads:
            raise HTTPException(422, 'Expected exactly one file field')
        folder = staging_directory(self.config)
        folder.mkdir(parents=True, exist_ok=True)
        upload = StagedAudio(folder / (uuid.uuid4().hex + '.partial'), self.config.max_upload_bytes,
                             _user_safe_decode(options[b'filename'], self._charset),
                             Headers(raw=self._current_part.item_headers))
        self.uploads.append(upload)
        self._current_part.field_name = 'file'
        self._current_part.file = upload
        self._files_to_close_on_error.append(upload.file)

    def on_end(self) -> None:
        self.ended = True

    async def parse(self):
        try:
            form = await super().parse()
        except (MultiPartException, MultipartParseError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if not self.ended:
            raise HTTPException(422, 'Incomplete multipart audio upload')
        return form

    def cleanup(self) -> None:
        for upload in self.uploads:
            upload.cleanup()
