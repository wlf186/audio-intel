"""Adaptive, unquantized ASR samples with bounded, thread-safe reads."""
from __future__ import annotations

from contextlib import ExitStack
from contextvars import ContextVar
import math
import mmap
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

import numpy as np

from .asr_resources import AsrResourceLimitError, current_budget

store_stack: ContextVar[ExitStack | None] = ContextVar("audio_stores", default=None)

BLOCK = 65_536
# Legacy float-second endpoints can differ by one sample after independent int()
# conversions (e.g. 2.01..62.01). Preserve those samples without unbounded reads.
MAX_READ = 60 * 16000 + 1
RATE = 16000


@dataclass
class _Extent:
    start: int
    count: int
    memory: mmap.mmap | None = None
    offset: int | None = None


class AudioStore:
    """Owned float32 pages, append-only spill extents, and logical silence.

    Budget checks precede the store lock. Readers always own their returned array,
    so a page can be unmapped as soon as it has been spilled.
    """
    sample_rate = RATE

    def __init__(self, path: Path, *, memory_limit_bytes: int | None = None):
        self.path = path
        self.file = None
        self.lock = threading.RLock()
        self.count = 0
        self.peak = 0.
        self.scale = 1.
        self.sealed = False
        self.closed = False
        self.extents: list[_Extent] = []
        self.starts: list[int] = []
        self.resident: deque[_Extent] = deque()
        self.budget = current_budget.get()
        limit = (self.budget.policy.get('audio_cache_bytes', 0) if self.budget else 0) if memory_limit_bytes is None else memory_limit_bytes
        self.memory_limit = max(0, limit) // (BLOCK * 4) * (BLOCK * 4)
        self.memory_bytes = 0
        self.stats = dict(cache_limit_bytes=self.memory_limit, peak_cache_bytes=0,
                          spilled_bytes=0, spill_events=0, quota_spills=0, pressure_spills=0,
                          normalization_rewrite_bytes=0, normalized_written_bytes=0)
        self.wav_remaining = 0
        if self.budget:
            self.budget.audio_stores.append(self)
        stack = store_stack.get()
        if stack is not None:
            stack.callback(self.close)

    def __len__(self) -> int:
        return self.count

    @property
    def sample_count(self) -> int:
        return self.count

    def iter_blocks(self, size: int = BLOCK):
        if not 0 < size <= MAX_READ:
            raise ValueError('Invalid audio block size')
        for start in range(0, self.count, size):
            yield self.read(start, min(self.count, start + size))

    def __array__(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError('AudioStore requires bounded reads, not whole-array conversion')

    def __getitem__(self, index: slice) -> np.ndarray:
        if not isinstance(index, slice) or index.step not in (None, 1):
            raise TypeError('Expected a contiguous audio slice')
        start, end, _ = index.indices(self.count)
        return self.read(start, max(start, end))

    def read(self, start: int, end: int) -> np.ndarray:
        if not 0 <= start <= end <= self.count or end - start > MAX_READ:
            raise ValueError('Audio read must be within the file and at most 60 seconds plus one endpoint sample')
        if self.budget:
            self.budget.check(memory=(end - start) * 8)
        with self.lock:
            if self.closed:
                raise ValueError('Audio store is closed')
            result = np.zeros(end - start, dtype=np.float32)
            index = max(0, bisect_right(self.starts, start) - 1)
            while index < len(self.extents):
                item = self.extents[index]
                if item.start >= end:
                    break
                first, last = max(start, item.start), min(end, item.start + item.count)
                if first < last:
                    offset = first - item.start
                    if item.memory is not None:
                        result[first-start:last-start] = np.frombuffer(item.memory, dtype=np.float32, count=last-first, offset=offset*4)
                    elif item.offset is not None:
                        self.file.seek(item.offset + offset * 4)
                        data = self.file.read((last-first)*4)
                        if len(data) != (last-first)*4:
                            raise ValueError('Audio store changed during read')
                        result[first-start:last-start] = np.frombuffer(data, dtype=np.float32)
                index += 1
            if self.scale > 1:
                result /= self.scale
            return result

    def _add(self, item: _Extent) -> None:
        previous = self.extents[-1] if self.extents else None
        if (previous is not None and previous.offset is not None and item.offset is not None
                and previous.start + previous.count == item.start
                and previous.offset + previous.count * 4 == item.offset):
            previous.count += item.count
            return
        self.extents.append(item)
        self.starts.append(item.start)

    def _grow(self, count: int) -> None:
        if self.closed or self.sealed:
            raise ValueError('Audio store is not writable')
        future = self.count + count
        if future * 2 + 4096 > 0xffffffff:
            raise AsrResourceLimitError('ASR playback exceeds WAV capacity; split the source')
        self.wav_remaining = future * 2 + 4096
        if self.budget:
            self.budget.check(work=self.wav_remaining, memory=BLOCK * 8)

    def _write(self, values: np.ndarray) -> int:
        if self.file is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.file = self.path.open('w+b', buffering=0)
        self.file.seek(0, 2)
        offset = self.file.tell()
        data = memoryview(values).cast('B')
        while data:
            written = self.file.write(data)
            if not written:
                raise OSError('Short audio cache write')
            self.stats['spilled_bytes'] += written
            data = data[written:]
        return offset

    def reclaim(self, needed: int, reason: str = 'pressure') -> int:
        if self.closed or not self.resident or needed <= 0:
            return 0
        pages = min(len(self.resident), (needed + BLOCK * 4 - 1) // (BLOCK * 4))
        if self.budget:
            self.budget.check(work=pages * BLOCK * 4 + self.wav_remaining, reclaim=False, disk_only=True)
        freed = 0
        with self.lock:
            for _ in range(min(pages, len(self.resident))):
                item = self.resident[0]
                # An I/O exception retains its traceback arguments. An owned bounded
                # copy prevents that traceback from pinning an exported mmap view.
                values = np.frombuffer(item.memory, dtype=np.float32, count=item.count).copy()
                try:
                    offset = self._write(values)
                finally:
                    del values
                item.memory.close()
                item.memory = None
                item.offset = offset
                self.resident.popleft()
                self.memory_bytes -= BLOCK * 4
                freed += BLOCK * 4
            if freed:
                self.stats['spill_events'] += 1
                self.stats[reason + '_spills'] += 1
        return freed

    def append(self, values: Any) -> None:
        position = 0
        while position < len(values):
            item = self.extents[-1] if self.extents else None
            room = BLOCK - item.count if item and item.memory is not None and item.count < BLOCK else BLOCK
            size = min(room, len(values) - position)
            block = np.asarray(values[position:position + size], dtype=np.float32)
            peak = float(np.max(np.abs(block)))
            if not math.isfinite(peak):
                raise ValueError('Audio contains non-finite samples')
            self._grow(size)
            # A budget check may have spilled the previously partial page.
            item = self.extents[-1] if self.extents else None
            if not (item and item.memory is not None and item.count + size <= BLOCK):
                if self.memory_limit:
                    self.reclaim(self.memory_bytes + BLOCK * 4 - self.memory_limit, 'quota')
                    if self.budget:
                        self.budget.check(memory=BLOCK * 4)
                    item = _Extent(self.count, 0, mmap.mmap(-1, BLOCK * 4))
                    self._add(item)
                    self.resident.append(item)
                    self.memory_bytes += BLOCK * 4
                    self.stats['peak_cache_bytes'] = max(self.stats['peak_cache_bytes'], self.memory_bytes)
                else:
                    if self.budget:
                        self.budget.check(work=size * 4 + self.wav_remaining)
                    self._add(_Extent(self.count, size, offset=self._write(block)))
                    item = None
            if item is not None:
                view = np.frombuffer(item.memory, dtype=np.float32, count=size, offset=item.count*4)
                view[:] = block
                del view
                item.count += size
            self.count += size
            self.peak = max(self.peak, peak)
            position += size

    def silence(self, count: int) -> None:
        if count < 0:
            raise ValueError('Negative silence length')
        self._grow(count)
        if count:
            item = self.extents[-1] if self.extents else None
            if item and item.memory is None and item.offset is None:
                item.count += count
            else:
                self._add(_Extent(self.count, count))
            self.count += count

    def finish(self, normalized: Path) -> AudioStore:
        import soundfile as sf
        if not self.count or self.sealed:
            raise ValueError('Audio is empty or already sealed')
        self.sealed = True
        self.scale = max(1., self.peak)
        with sf.SoundFile(normalized, 'w', samplerate=RATE, channels=1, format='WAV', subtype='PCM_16') as writer:
            for block in self.iter_blocks():
                if self.budget:
                    self.budget.check(work=self.wav_remaining)
                writer.write(block)
                self.stats['normalized_written_bytes'] += len(block)*2
                self.wav_remaining = max(0, self.wav_remaining - len(block)*2)
        self.wav_remaining = 0
        return self

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            for item in self.resident:
                item.memory.close()
                item.memory = None
            self.resident.clear()
            self.memory_bytes = 0
            if self.file is not None:
                self.file.close()
                self.path.unlink(missing_ok=True)
            self.extents.clear()
            self.starts.clear()
        if self.budget:
            self.budget.last_check = 0.

    def __enter__(self) -> AudioStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def speaker_windows(audio: AudioStore, regions: list[dict]) -> list[list]:
    """The exact sv_chunk geometry; descriptors retain no audio arrays."""
    windows = []
    for region in regions:
        begin = int(region['start'] * RATE)
        length = int(region['end'] * RATE) - begin
        last = 0
        for cursor in range(0, length, 12000):
            end = min(cursor + 24000, length)
            if end <= last:
                break
            last = end
            start = max(0, end - 24000)
            windows.append([start / RATE + region['start'], end / RATE + region['start'], (begin + start, begin + end)])
            if len(windows) % 1024 == 0 and audio.budget:
                audio.budget.check(memory=len(windows) * 4096)
    return windows


def window_audio(audio: AudioStore, window: list) -> np.ndarray:
    start, end = window[2]
    values = audio.read(start, end)
    return np.pad(values, (0, max(0, 24000 - len(values))), 'constant')
