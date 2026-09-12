"""Small, versioned waveform sidecars; audio decoding has bounded memory."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import tempfile
import threading
from concurrent.futures import Future
from contextlib import closing, suppress
from pathlib import Path
from typing import Any, Iterable

BINS = 240
VERSION = 1
_log = logging.getLogger(__name__)
_lock = threading.Lock()
_pending: dict[str, Future] = {}


class WaveformBusy(Exception):
    pass


def _reduce(blocks: Iterable[Any], total: int) -> list[float]:
    import numpy as np
    if total <= 0:
        raise ValueError("Audio contains no samples")
    count = min(BINS, total)
    peaks = [0.0] * count
    offset = 0
    index = 0
    for block in blocks:
        values = np.asarray(block)
        if values.ndim == 2:
            values = np.max(np.abs(values), axis=0)
        cursor = 0
        while cursor < len(values):
            if index >= count:
                raise ValueError("Audio changed while calculating waveform")
            end = (index + 1) * total // count
            size = min(len(values) - cursor, end - offset)
            maximum = float(np.max(np.abs(values[cursor:cursor + size])))
            if not math.isfinite(maximum):
                raise ValueError("Audio contains non-finite samples")
            peaks[index] = max(peaks[index], min(1.0, maximum))
            cursor += size
            offset += size
            if offset == end:
                index += 1
    if offset != total:
        raise ValueError("Audio changed while calculating waveform")
    return [round(value, 4) for value in peaks]


def pcm_peaks(samples: Any) -> list[float]:
    return _reduce((samples[start:start + 8192] for start in range(0, len(samples), 8192)), len(samples))


def file_waveform(path: Path) -> dict[str, Any]:
    import av
    def blocks():
        with av.open(str(path)) as source:
            if len(source.streams.audio) != 1:
                raise ValueError("Expected one audio stream")
            stream = source.streams.audio[0]
            resampler = av.AudioResampler(format="fltp", layout=stream.codec_context.layout.name,
                                          rate=stream.codec_context.sample_rate)
            for frame in source.decode(stream):
                for converted in resampler.resample(frame):
                    yield converted.to_ndarray(), converted.sample_rate
            for converted in resampler.resample(None):
                yield converted.to_ndarray(), converted.sample_rate
    total = 0
    rate = None
    with closing(blocks()) as decoded:
        for values, current_rate in decoded:
            if rate is not None and rate != current_rate:
                raise ValueError("Audio sample rate changed")
            rate = current_rate
            total += values.shape[1]
    if not total or not rate:
        raise ValueError("Audio contains no samples")
    with closing(blocks()) as decoded:
        peaks = _reduce((values for values, _ in decoded), total)
    return {"artifact_name": path.name, "duration": total / rate, "waveform": peaks}


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def cache_path(path: Path) -> Path:
    return path.parent.parent / "waveforms" / (hashlib.sha256(path.name.encode()).hexdigest() + ".json")


def cached(path: Path) -> dict[str, Any] | None:
    try:
        target = cache_path(path)
        if target.stat().st_size > 16384:
            return None
        value = json.loads(target.read_text(encoding="utf-8"))
        data = value["data"]
        peaks = data["waveform"]
        if (value["version"] != VERSION or value["source"] != _fingerprint(path)
                or data["artifact_name"] != path.name or not 0 < data["duration"] < math.inf
                or not isinstance(peaks, list) or not 0 < len(peaks) <= BINS
                or any(not isinstance(p, (float, int)) or not 0 <= p <= 1 for p in peaks)):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare(path: Path, samples: Any = None, rate: int | None = None) -> dict[str, Any]:
    existing = cached(path)
    if existing is not None:
        return existing
    before = _fingerprint(path)
    data = file_waveform(path) if samples is None else {
        "artifact_name": path.name, "duration": len(samples) / rate, "waveform": pcm_peaks(samples),
    }
    if before != _fingerprint(path):
        raise ValueError("Audio changed while calculating waveform")
    temporary = None
    try:
        target = cache_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, prefix="waveform-", suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"version": VERSION, "source": before, "data": data}, stream, allow_nan=False)
        os.replace(temporary, target)
    except OSError:
        _log.warning("Unable to cache waveform for %s", path.name, exc_info=True)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    return data


def prepare_optional(path: Path, samples: Any = None, rate: int | None = None) -> dict[str, Any] | None:
    try:
        return prepare(path, samples, rate)
    except Exception:
        _log.warning("Unable to prepare waveform for %s", path.name, exc_info=True)
        return None


def retrieve(path: Path) -> dict[str, Any]:
    existing = cached(path)
    if existing is not None:
        return existing
    key = str(path)
    with _lock:
        future = _pending.get(key)
        owner = future is None
        if owner:
            if len(_pending) >= 2:
                raise WaveformBusy()
            future = Future()
            _pending[key] = future
    if not owner:
        return future.result()
    try:
        result = prepare(path)
        future.set_result(result)
        return result
    except Exception as exc:
        future.set_exception(exc)
        raise
    finally:
        with _lock:
            _pending.pop(key, None)
