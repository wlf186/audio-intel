"""Single-file streaming multipart uploads, admitted before reading any body bytes."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool

from .document_text import FORMATS
from .utils import safe_filename

OVERHEAD = 1024 * 1024


async def receive_document(request: Request, partial: Path | None, limit: int) -> tuple[str, str, int, str]:
    content_type, options = parse_options_header(request.headers.get("content-type", ""))
    if content_type != b"multipart/form-data" or not options.get(b"boundary"):
        raise HTTPException(422, "Expected multipart/form-data with one file field")
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError as exc:
        raise HTTPException(400, "Invalid Content-Length") from exc
    if length < 0 or length > limit + OVERHEAD:
        raise HTTPException(413, "Document upload exceeds the limit")
    headers: dict[bytes, bytes] = {}
    header_name = bytearray()
    header_value = bytearray()
    sha = hashlib.sha256()
    size = total = parts = 0
    ended = False
    name = suffix = ""
    output = None

    def part_begin() -> None:
        nonlocal parts
        parts += 1
        if parts > 1:
            raise HTTPException(422, "Only one file field is supported")

    def header_field(data: bytes, start: int, end: int) -> None:
        header_name.extend(data[start:end])

    def header_data(data: bytes, start: int, end: int) -> None:
        header_value.extend(data[start:end])

    def header_end() -> None:
        key = bytes(header_name).lower()
        if key in headers:
            raise HTTPException(422, "Duplicate multipart header")
        headers[key] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished() -> None:
        nonlocal name, suffix, output
        disposition, fields = parse_options_header(headers.get(b"content-disposition", b""))
        if disposition != b"form-data" or fields.get(b"name") != b"file" or b"filename" not in fields:
            raise HTTPException(422, "Only one file field is supported")
        name = safe_filename(fields[b"filename"].decode("utf-8", errors="replace") or "document")
        suffix = Path(name).suffix.lower()
        if suffix not in FORMATS:
            raise HTTPException(422, "Use EPUB, TXT, PDF, Markdown, DOCX, XLSX or PPTX; convert legacy DOC/XLS/PPT first / 旧格式请先另存为 DOCX/XLSX/PPTX")
        if partial is not None:
            partial.parent.mkdir(parents=True, exist_ok=True)
            output = partial.open("xb")

    def part_data(data: bytes, start: int, end: int) -> None:
        nonlocal size
        size += end - start
        if size > limit:
            raise HTTPException(413, "Document upload exceeds the limit")
        chunk = data[start:end]
        sha.update(chunk)
        if output is not None:
            output.write(chunk)

    def finish() -> None:
        nonlocal ended
        ended = True

    parser = MultipartParser(options[b"boundary"], {
        "on_part_begin": part_begin, "on_header_field": header_field,
        "on_header_value": header_data, "on_header_end": header_end,
        "on_headers_finished": headers_finished, "on_part_data": part_data, "on_end": finish,
    })
    try:
        async for chunk in request.stream():
            # Bound callback buffers and writes even when an ASGI sender emits a large chunk.
            for start in range(0, len(chunk), 256 * 1024):
                block = chunk[start:start + 256 * 1024]
                total += len(block)
                if total > limit + OVERHEAD or total - size > OVERHEAD + len(block):
                    raise HTTPException(413, "Document upload exceeds the limit")
                await run_in_threadpool(parser.write, block)
                if total - size > OVERHEAD:
                    raise HTTPException(413, "Multipart overhead exceeds the limit")
        parser.finalize()
        if not ended or parts != 1 or not name:
            raise HTTPException(422, "Incomplete document upload")
        return name, suffix, size, sha.hexdigest()
    except MultipartParseError as exc:
        raise HTTPException(400, "Invalid multipart document upload") from exc
    finally:
        if output is not None:
            await run_in_threadpool(output.close)
