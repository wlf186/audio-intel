"""Disk-backed, unquantized ASR samples with bounded, thread-safe reads."""
from __future__ import annotations

from contextlib import ExitStack
from contextvars import ContextVar
import math
from pathlib import Path
import threading
from typing import Any

import numpy as np

from .asr_resources import AsrResourceLimitError, check_resources, current_budget

store_stack: ContextVar[ExitStack | None] = ContextVar("audio_stores", default=None)

BLOCK = 65_536
# Legacy float-second endpoints can differ by one sample after independent int()
# conversions (e.g. 2.01..62.01). Preserve those samples without unbounded reads.
MAX_READ = 60 * 16000 + 1
RATE = 16000


class AudioStore:
    sample_rate = RATE
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.file = path.open('w+b')
        self.lock = threading.RLock()
        self.count = 0
        self.peak = 0.
        self.budget = current_budget.get()
        stack = store_stack.get()
        if stack is not None:
            stack.callback(self.close)

    @classmethod
    def open(cls, path: Path) -> AudioStore:
        store = cls.__new__(cls)
        store.path = path
        store.file = path.open('r+b')
        store.lock = threading.RLock()
        store.count = path.stat().st_size // 4
        store.peak = 0.
        store.budget = current_budget.get()
        stack = store_stack.get()
        if stack is not None:
            stack.callback(store.close)
        return store

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
            self.file.seek(start * 4)
            result = np.fromfile(self.file, dtype=np.float32, count=end - start)
        if len(result) != end - start:
            raise ValueError('Audio store changed during read')
        return result

    def append(self, values: Any) -> None:
        for start in range(0, len(values), BLOCK):
            block = np.asarray(values[start:start + BLOCK], dtype=np.float32)
            peak = float(np.max(np.abs(block)))
            if not math.isfinite(peak):
                raise ValueError('Audio contains non-finite samples')
            self._grow(len(block))
            self._write_block(block)
            self.count += len(block)
            self.peak = max(self.peak, peak)

    def _write_block(self, block: np.ndarray) -> None:
        self.file.seek(self.count * 4)
        block.tofile(self.file)

    def _grow(self, count: int) -> None:
        # Existing public playback format is RIFF PCM16, not a silent RF64 change.
        if (self.count + count) * 2 + 4096 > 0xffffffff:
            raise AsrResourceLimitError('ASR playback exceeds WAV capacity; split the source')
        # Reserve raw float32 plus the future normalized PCM16 file.
        future = self.count + count
        if self.budget and future * 6 > self.budget.policy['work_bytes']:
            self.budget.fail('decoded audio workspace', future * 6, self.budget.policy['work_bytes'])
        if count > BLOCK or self.count // BLOCK != future // BLOCK:
            check_resources(work=count * 4 + future * 2, stage='decoding_audio')

    def silence(self, count: int) -> None:
        self._grow(count)
        zeros = np.zeros(min(BLOCK, count), dtype=np.float32)
        while count:
            size = min(len(zeros), count)
            self.append(zeros[:size])
            count -= size

    def finish(self, normalized: Path) -> AudioStore:
        import soundfile as sf
        if not self.count:
            raise ValueError('Audio contains no decodable samples')
        self.file.flush()
        with sf.SoundFile(normalized, 'w', samplerate=RATE, channels=1, format='WAV', subtype='PCM_16') as writer:
            for start in range(0, self.count, BLOCK):
                block = self.read(start, min(self.count, start + BLOCK))
                if self.peak > 1:
                    block /= self.peak
                    self.file.seek(start * 4)
                    block.tofile(self.file)
                check_resources(work=len(block) * 2, stage='normalizing_audio')
                writer.write(block)
        self.file.flush()
        return self

    def close(self) -> None:
        self.file.close()

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
