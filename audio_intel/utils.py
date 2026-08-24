from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any, Iterable


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


def waveform_peaks(samples: Iterable[float], bins: int = 1200) -> list[float]:
    values = list(samples)
    if not values:
        return []
    width = max(1, math.ceil(len(values) / bins))
    return [round(max(abs(float(v)) for v in values[i : i + width]), 4) for i in range(0, len(values), width)]
