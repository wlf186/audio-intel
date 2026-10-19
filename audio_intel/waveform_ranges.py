"""Seekable WAV peaks for the visible video timeline; no persistent media writes."""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from pathlib import Path

from .waveforms import run_limited

_lock = threading.Lock()
_cache: OrderedDict[tuple, dict] = OrderedDict()


def retrieve(path: Path, track: str, start: float, end: float | None, points: int) -> dict:
    import numpy as np
    import soundfile as sf

    info = path.stat()
    key = (str(path), info.st_size, info.st_mtime_ns, track, start, end, points)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached

    def calculate():
        with sf.SoundFile(path) as source:
            if source.format != 'WAV' or source.channels != 1 or source.samplerate != 16000 or source.subtype != 'PCM_16':
                raise ValueError('Expected saved 16 kHz mono PCM16 WAV')
            duration = source.frames / source.samplerate
            upper = duration if end is None else min(end, duration)
            if not math.isfinite(start) or not math.isfinite(upper) or start < 0 or start >= upper:
                raise ValueError('Invalid waveform window')
            first = int(start * source.samplerate)
            last = min(source.frames, math.ceil(upper * source.samplerate))
            total = last - first
            count = min(points, total)
            peaks = []
            source.seek(first)
            for index in range(count):
                remaining = (index + 1) * total // count - index * total // count
                peak = 0.
                while remaining:
                    block = source.read(min(8192, remaining), dtype='float32')
                    if not len(block):
                        raise ValueError('Audio changed while reading')
                    peak = max(peak, float(np.max(np.abs(block))))
                    remaining -= len(block)
                peaks.append(round(min(1., peak), 4))
            result = {'track': track, 'duration': duration, 'start': first / source.samplerate,
                      'end': last / source.samplerate, 'waveform': peaks}
        after = path.stat()
        if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Audio changed while reading')
        with _lock:
            _cache[key] = result
            _cache.move_to_end(key)
            while len(_cache) > 128:
                _cache.popitem(last=False)
        return result

    return run_limited(repr(key), calculate)
