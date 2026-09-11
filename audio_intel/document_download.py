"""Bounded streaming exports. No complete audio or ZIP is stored on disk."""
from __future__ import annotations

import io
import threading
import zipfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BLOCK = 256 * 1024
_lock = threading.Lock()
_leases: Counter[str] = Counter()
_deleting: set[str] = set()


def in_use(job_id: str) -> bool:
    with _lock:
        return bool(_leases[job_id])


@contextmanager
def lease(job_id: str, limit: int) -> Iterator[None]:
    with _lock:
        if job_id in _deleting or sum(_leases.values()) >= limit:
            raise RuntimeError("Too many document downloads")
        _leases[job_id] += 1
    try:
        yield
    finally:
        with _lock:
            _leases[job_id] -= 1
            if not _leases[job_id]:
                del _leases[job_id]


@contextmanager
def deletion_guard(job_id: str) -> Iterator[bool]:
    with _lock:
        allowed = not _leases[job_id] and job_id not in _deleting
        if allowed:
            _deleting.add(job_id)
    try:
        yield allowed
    finally:
        if allowed:
            with _lock:
                _deleting.discard(job_id)


def validate_mp3(files: list[tuple[str, Path]]) -> None:
    import av
    expected = None
    if not files:
        raise ValueError("No document audio")
    for _, path in files:
        with av.open(str(path)) as source:
            if len(source.streams.audio) != 1:
                raise ValueError("Invalid document audio")
            audio = source.streams.audio[0]
            signature = (audio.codec_context.name, audio.codec_context.sample_rate, audio.codec_context.layout.name, audio.time_base)
            if signature[0] not in {"mp3", "mp3float"} or (expected is not None and expected != signature):
                raise ValueError("Document MP3 encoding parameters differ")
            expected = signature


class Sink(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.pending = bytearray()
        self.offset = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self.offset

    def write(self, value: Any) -> int:
        self.pending.extend(value)
        self.offset += len(value)
        return len(value)

    def drain(self) -> bytes:
        value = bytes(self.pending)
        self.pending.clear()
        return value


def zip_stream(files: list[tuple[str, Path]]) -> Iterator[bytes]:
    sink = Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, path in files:
            info = zipfile.ZipInfo(name)
            info.file_size = path.stat().st_size
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                while chunk := source.read(BLOCK):
                    target.write(chunk)
                    yield sink.drain()
            if sink.pending:
                yield sink.drain()
    # Central-directory memory is bounded by the document's section count.
    if sink.pending:
        yield sink.drain()


def mp3_stream(files: list[tuple[str, Path]]) -> Iterator[bytes]:
    import av
    sink = Sink()
    cursor = 0
    expected = None
    with av.open(sink, "w", format="mp3", options={"write_xing": "0", "id3v2_version": "3", "write_id3v1": "0"}) as target:
        stream = None
        for _, path in files:
            with av.open(str(path)) as source:
                audio = source.streams.audio[0]
                signature = (audio.codec_context.name, audio.codec_context.sample_rate, audio.codec_context.layout.name, audio.time_base)
                if expected is None:
                    expected = signature
                    stream = target.add_stream_from_template(audio)
                elif signature != expected:
                    raise ValueError("Document MP3 encoding parameters differ")
                for packet in source.demux(audio):
                    if packet.dts is None:
                        continue
                    packet.pts = packet.dts = cursor
                    cursor += packet.duration
                    packet.stream = stream
                    target.mux(packet)
                    if len(sink.pending) >= BLOCK:
                        yield sink.drain()
    if sink.pending:
        yield sink.drain()
