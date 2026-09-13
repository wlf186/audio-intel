"""Clone reference selection, independent of model and HTTP runtimes."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

DEFAULT_SECONDS = 15
MIN_SECONDS = 3
MAX_SECONDS = 30
EPSILON = 1e-9
ReferenceKey = str | tuple[str, float, float]
RANGE_FIELDS = ("reference_start_seconds", "reference_end_seconds")
RESULT_FIELDS = ("reference_start_seconds_used", "reference_end_seconds_used", "reference_text_used",
                 "reference_duration_original", "reference_duration_used", "reference_truncated")


def capability() -> dict[str, Any]:
    return {"voice_modes": ["voiceprint"], "min_seconds": MIN_SECONDS,
            "max_seconds": MAX_SECONDS, "default_max_seconds": DEFAULT_SECONDS}


def validate(start: float | None, end: float | None, duration: float | None = None) -> None:
    if start is None and end is None:
        return
    if start is None or end is None:
        raise ValueError("reference_start_seconds and reference_end_seconds must be supplied together")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
        raise ValueError("Reference range endpoints must be finite numbers")
    if start < 0 or not MIN_SECONDS - EPSILON <= end - start <= MAX_SECONDS + EPSILON:
        raise ValueError("Reference range must start at or after zero and contain 3–30 seconds")
    if duration is not None and (not math.isfinite(duration) or end > duration + EPSILON):
        raise ValueError("Reference range exceeds the sample duration")


def audio_duration(path: Path) -> float:
    import av
    with av.open(str(path)) as source:
        stream = source.streams.audio[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if source.duration is not None:
            return float(source.duration / av.time_base)
    raise ValueError("Reference audio duration is unavailable")


def aligned_words(text: str, words: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Map repeated words monotonically into the original transcript, preserving punctuation."""
    if not words:
        raise ValueError("Reference word alignment is unavailable")
    mapped = []
    cursor = 0
    previous_start = -1.0
    previous_end = -1.0
    for word in words:
        token = str(word.get("text") or "").strip()
        if not token:
            continue
        try:
            start, end = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Reference word alignment is invalid") from None
        offset = text.find(token, cursor)
        if (offset < 0 or not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start <= end <= duration or start < previous_start or end < previous_end):
            raise ValueError("Reference word alignment does not match the sample")
        cursor = offset + len(token)
        mapped.append({"start": start, "end": end, "text_start": offset, "text_end": cursor})
        previous_start, previous_end = start, end
    if not mapped:
        raise ValueError("Reference word alignment is unavailable")
    return mapped


def resolve(text: str, words: list[dict[str, Any]], start: float, end: float, duration: float) -> tuple[float, float, str]:
    validate(start, end, duration)
    mapped = aligned_words(text, words, duration)
    eligible = [w for w in mapped if w["start"] >= start and w["end"] <= end]
    if not eligible:
        raise ValueError("Reference range contains no complete aligned words; select a wider speech interval")
    first, last = eligible[0], eligible[-1]
    validate(first["start"], last["end"], duration)
    # Overlapping alignment must never leave part of an excluded word in the crop.
    if any(w["start"] < first["start"] < w["end"] or w["start"] < last["end"] < w["end"] for w in mapped):
        raise ValueError("Reference range crosses overlapping words; adjust its endpoints")
    return first["start"], last["end"], text[first["text_start"]:last["text_end"]]


def key(item: dict[str, Any]) -> ReferenceKey:
    sample = str(item.get("voiceprint_sample_id") or "")
    if item.get(RANGE_FIELDS[0]) is None:
        return sample  # retain the legacy in-memory interface for default references
    return sample, item[RANGE_FIELDS[0]], item[RANGE_FIELDS[1]]


def result(reference: dict[str, Any]) -> dict[str, Any]:
    return {field: reference[field] for field in RESULT_FIELDS if field in reference}
