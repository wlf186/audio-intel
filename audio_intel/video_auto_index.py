"""Seekable table of observed sample times and stable-run starts and versioned automatic scene timelines."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
from array import array
from pathlib import Path

import numpy as np

from .asr_resources import AsrResourceLimitError, check_resources
from .video_detection import CACHE_LIMIT, identity


NAME = 'scene-auto-index.bin'
RANGE_NAME = 'scene-auto-range.bin'
MAX_BYTES = 8 * 1024**2


class SampleIndex:
    def __init__(self):
        self.records = array('d')
        self.enabled = True

    def add(self, timestamp: float, since: float) -> None:
        if self.enabled:
            if len(self.records) * 8 + 16 > MAX_BYTES:
                self.records = array('d')
                self.enabled = False
            else:
                self.records.extend((timestamp, since))

    def save(self, path: Path, source: Path, roi, scenes, min_free: int, *, version=1, interval=None) -> None:
        if not self.enabled or not self.records:
            return
        raw = np.asarray(self.records, dtype='<f8').tobytes()
        header = json.dumps(dict(identity=identity(source, roi), version=version, interval=interval, scenes=scenes,
                                 count=len(self.records)//2, digest=hashlib.sha256(raw).hexdigest()), separators=(',', ':')).encode()
        if version >= 2:
            header = hashlib.sha256(header).digest() + header
        size = len(header) + len(self.records)*8 + 4
        if size > MAX_BYTES:
            return
        tmp = None
        try:
            check_resources(work=size)
            path.parent.mkdir(parents=True, exist_ok=True)
            sibling = path.parent/'scene-features'
            used = sum(f.stat().st_size for f in sibling.glob('*') if f.is_file())
            used += sum(f.stat().st_size for f in (path.parent/NAME, path.parent/RANGE_NAME) if f != path and f.is_file())
            if used + size > CACHE_LIMIT or shutil.disk_usage(path.parent).free-size < min_free:
                return
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name+'.partial-', delete=False) as out:
                tmp = Path(out.name)
                out.write(struct.pack('<I', len(header)))
                out.write(header)
                out.write(raw)
            os.replace(tmp, path)
        except (OSError, AsrResourceLimitError):
            pass
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass


def read(path: Path, source: Path, roi, *, version=1, interval=None):
    with path.open('rb') as f:
        prefix = f.read(4)
        if len(prefix) != 4:
            raise ValueError('Incomplete sample index')
        size = int.from_bytes(prefix, 'little')
        if not 0 < size < 1024**2:
            raise ValueError('Invalid sample index')
        payload = f.read(size)
        if version >= 2:
            if hashlib.sha256(payload[32:]).digest() != payload[:32]:
                raise ValueError('Corrupt detail index header')
            payload = payload[32:]
        header = json.loads(payload)
        count = header['count']
        if (header.get('version') != version or header.get('interval') != interval or header['identity'] != identity(source, roi)
                or type(count) is not int or not 0 < count <= MAX_BYTES//16
                or os.fstat(f.fileno()).st_size != 4 + size + 16*count):
            raise ValueError('Stale or incomplete sample index')
        records = np.memmap(f, dtype='<f8', offset=4+size, mode='r', shape=(count, 2))
        if hashlib.sha256(records).hexdigest() != header.get('digest'):
            raise ValueError('Corrupt sample index')
        return header['scenes'], records


def select(records, scenes):
    for scene in scenes:
        first, last = np.searchsorted(records[:, 0], [scene['start'], scene['end']])
        scene.update(frame_time=None, image=None)
        since = run = None
        for timestamp, reference in records[first:last]:
            if reference != run:
                since, run = timestamp, reference
            if timestamp-since >= 1.:
                scene['frame_time'] = float(since)
