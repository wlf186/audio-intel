from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Protocol


SAFE_NAME = re.compile(r"[^\w.\-\u4e00-\u9fff]+", re.UNICODE)


def safe_filename(name: str, fallback: str = "audio.bin") -> str:
    clean = SAFE_NAME.sub("_", Path(name or fallback).name).strip("._")
    return clean[:180] or fallback


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def timecode(seconds: float, separator: str = ".") -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{milliseconds:03d}"


class SampleSequence(Protocol):
    def __len__(self) -> int: ...
    def __getitem__(self, index: slice) -> Any: ...


def waveform_peaks(samples: SampleSequence, bins: int = 1200) -> list[float]:
    """Legacy ceil-width bins, with bounded temporary memory even for one bin."""
    import numpy as np

    if not isinstance(bins, int) or isinstance(bins, bool) or bins <= 0:
        raise ValueError("bins must be a positive integer")
    total = len(samples)  # Unknown-length iterators must never be materialized.
    if not total:
        return []
    width = max(1, (total + bins - 1) // bins)
    peaks = []
    for start in range(0, total, width):
        end = min(total, start + width)
        peak = 0.0
        for cursor in range(start, end, 65_536):
            # float64 preserves the old per-sample float() conversion, including
            # integer inputs, without boxing the complete recording into a list.
            block = np.asarray(samples[cursor:min(end, cursor + 65_536)], dtype=np.float64)
            if block.ndim != 1:
                raise ValueError("Expected one-dimensional audio samples")
            maximum = float(np.max(np.abs(block)))
            if not math.isfinite(maximum):
                raise ValueError("Audio contains non-finite samples")
            peak = max(peak, maximum)
        peaks.append(round(peak, 4))
    return peaks
