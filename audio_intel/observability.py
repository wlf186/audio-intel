from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import active_jobs, successful_jobs


_SYNTHESIS_STAGE = re.compile(r"^synthesizing_(\d+)_of_(\d+)$")


def stage_details(job: dict[str, Any]) -> dict[str, Any]:
    raw = str(job.get("stage") or "queued")
    current = job.get("stage_current")
    total = job.get("stage_total")
    code = str(job.get("stage_code") or "")
    match = _SYNTHESIS_STAGE.match(raw)
    if match:
        code = "synthesis"
        current, total = int(match.group(1)), int(match.group(2))
    elif raw.startswith("qwen3_asr_"):
        code = "transcription"
    elif raw.startswith("qwen3_forced_alignment_") or raw.startswith("aligning_clone_reference_"):
        code = "alignment"
    elif raw == "voice_activity_detection":
        code = "vad"
    elif raw == "speaker_diarization":
        code = "diarization"
    elif raw == "merging_speakers_and_timestamps":
        code = "merging"
    elif raw in {"writing_exports", "writing_audio"}:
        code = "writing_output"
    elif raw.startswith("waiting_for_gpu"):
        code = "waiting_for_gpu"
    elif not code:
        code = raw
    stage_progress = None
    if isinstance(current, int) and isinstance(total, int) and total > 0:
        stage_progress = max(0.0, min(1.0, current / total))
    return {
        "stage_code": code,
        "stage_progress": stage_progress,
        "current": current,
        "total": total,
        "unit": "batch" if current is not None and total is not None else None,
    }


def queue_context(
    reservations: dict[str, int] | None = None, include_history: bool = True,
) -> dict[str, Any]:
    jobs = active_jobs()
    reservations = reservations or {}
    result: dict[str, Any] = {"jobs": jobs, "positions": {}, "kinds": {}}
    for kind in ("asr", "tts"):
        queued = sorted(
            (job for job in jobs if job["kind"] == kind and job["state"] == "queued"),
            key=lambda item: int(item.get("queue_seq") or 0),
        )
        running = [job for job in jobs if job["kind"] == kind and job["state"] == "running"]
        result["positions"].update({job["id"]: index for index, job in enumerate(queued, 1)})
        result["kinds"][kind] = {
            "queued": len(queued),
            "running": len(running),
            "reserved": int(reservations.get(kind, 0)),
        }
    if include_history:
        result["history"] = {kind: successful_jobs(kind, 200) for kind in ("asr", "tts")}
    return result


def queue_for_job(
    job: dict[str, Any], context: dict[str, Any], capacities: dict[str, int],
) -> dict[str, Any] | None:
    if job.get("state") not in {"queued", "running"}:
        return None
    kind = str(job["kind"])
    info = context["kinds"][kind]
    waiting_for = "worker" if job["state"] == "queued" else (
        "gpu" if stage_details(job)["stage_code"] == "waiting_for_gpu" else None
    )
    return {
        "scope": kind,
        "position": context["positions"].get(job["id"]),
        "depth": info["queued"],
        "capacity": capacities[kind],
        "waiting_for": waiting_for,
    }


def _cohort(job: dict[str, Any]) -> tuple[Any, ...]:
    request = job.get("request") or {}
    common = (
        job.get("kind"), request.get("compute_device"),
        bool(request.get("accelerate_single_task")),
    )
    if job.get("kind") == "asr":
        return common + (bool(request.get("diarize")), bool(request.get("align")))
    return common + (request.get("voice_mode", "preset"),)


def _input_units(job: dict[str, Any]) -> float | None:
    request = job.get("request") or {}
    if job.get("kind") == "tts":
        return float(max(1, len(str(request.get("text") or ""))))
    duration = job.get("input_duration_seconds") or (job.get("result") or {}).get("duration")
    return float(duration) if duration else None


def _nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _history(context: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    histories = context.setdefault("history", {})
    if kind not in histories:
        histories[kind] = successful_jobs(kind, 200)
    return histories[kind]


def _duration_range(job: dict[str, Any], context: dict[str, Any]) -> tuple[float, float, int] | None:
    kind_history = _history(context, str(job["kind"]))
    history = [item for item in kind_history if _cohort(item) == _cohort(job)]
    if len(history) < 5:
        history = [
            item for item in kind_history
            if (item.get("request") or {}).get("compute_device")
            == (job.get("request") or {}).get("compute_device")
        ]
    values: list[float] = []
    units = _input_units(job)
    for item in history:
        elapsed = float(item.get("processing_seconds") or 0)
        if elapsed <= 0:
            continue
        sample_units = _input_units(item)
        if units is not None and sample_units:
            values.append(elapsed / sample_units * units)
        else:
            values.append(elapsed)
    if len(values) < 5:
        return None
    return max(1.0, _nearest_rank(values, 0.10)), max(1.0, _nearest_rank(values, 0.90)), len(values)


def estimate_for_job(job: dict[str, Any], context: dict[str, Any]) -> dict[str, Any] | None:
    if job.get("state") not in {"queued", "running"}:
        return None
    timestamps = [
        datetime.fromisoformat(str(item["updated_at"]))
        for item in [job, *context["jobs"]]
        if item.get("updated_at")
    ]
    as_of = max(timestamps) if timestamps else datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    duration = _duration_range(job, context)
    if duration is None:
        samples = len(_history(context, str(job["kind"])))
        return {
            "state": "warming_up", "confidence": None, "sample_count": samples,
            "start_after_seconds": None, "remaining_seconds": None,
            "completes_at": None, "updated_at": as_of.isoformat(timespec="milliseconds"),
        }
    lower, upper, samples = duration
    confidence = "high" if samples >= 50 else "medium" if samples >= 20 else "low"
    start_lower = start_upper = 0.0
    if job["state"] == "queued":
        position = int(context["positions"].get(job["id"], 1))
        ahead = [
            item for item in context["jobs"]
            if item["kind"] == job["kind"]
            and (
                item["state"] == "running"
                or item["state"] == "queued"
                and int(context["positions"].get(item["id"], 10**9)) < position
            )
        ]
        for item in ahead:
            item_range = _duration_range(item, context)
            if item_range:
                factor = max(0.05, 1.0 - float(item.get("progress") or 0)) if item["state"] == "running" else 1.0
                start_lower += item_range[0] * factor
                start_upper += item_range[1] * factor
            else:
                start_lower += lower
                start_upper += upper
    else:
        factor = max(0.02, 1.0 - float(job.get("progress") or 0))
        lower, upper = lower * factor, upper * factor
    request = job.get("request") or {}
    competing_gpu = request.get("compute_device") == "gpu" and any(
        item["id"] != job["id"] and item["state"] == "running"
        and (item.get("request") or {}).get("compute_device") == "gpu"
        for item in context["jobs"]
    )
    if competing_gpu:
        confidence = "low"
        upper *= 1.5
    remaining_lower, remaining_upper = start_lower + lower, start_upper + upper
    return {
        "state": "ready", "confidence": confidence, "sample_count": samples,
        "start_after_seconds": {"lower": round(start_lower), "upper": round(start_upper)},
        "remaining_seconds": {"lower": round(remaining_lower), "upper": round(remaining_upper)},
        "completes_at": {
            "earliest": (as_of + timedelta(seconds=remaining_lower)).isoformat(timespec="seconds"),
            "latest": (as_of + timedelta(seconds=remaining_upper)).isoformat(timespec="seconds"),
        },
        "updated_at": as_of.isoformat(timespec="milliseconds"),
    }
