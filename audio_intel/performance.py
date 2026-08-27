from __future__ import annotations

from typing import Any

import psutil

from .gpu import gpu_snapshot


BATCH_LADDER = (16, 12, 8, 6, 4, 2, 1)


def gpu_batch_size(memory_total_mib: int) -> int:
    if memory_total_mib >= 32 * 1024:
        return 16
    if memory_total_mib >= 24 * 1024:
        return 12
    if memory_total_mib >= 16 * 1024:
        return 8
    if memory_total_mib >= 12 * 1024:
        return 6
    if memory_total_mib >= 8 * 1024:
        return 4
    return 2


def cpu_batch_size(physical_cores: int, available_bytes: int) -> int:
    available_gib = available_bytes / 1024**3
    if physical_cores >= 48 and available_gib >= 64:
        return 8
    if physical_cores >= 32 and available_gib >= 48:
        return 6
    if physical_cores >= 16 and available_gib >= 24:
        return 4
    if physical_cores >= 8 and available_gib >= 12:
        return 2
    return 1


def resolve_acceleration(
    enabled: bool,
    compute_device: str,
    batch_penalty_steps: int = 0,
) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "requested": bool(enabled),
        "device": compute_device,
        "target_batch_size": 1,
    }
    if not enabled:
        return profile
    if compute_device == "gpu":
        snapshot = gpu_snapshot(0)
        memory_total_mib = int(snapshot["memory_total_mib"]) if snapshot else 0
        target = gpu_batch_size(memory_total_mib) if memory_total_mib else 2
        for _ in range(max(0, batch_penalty_steps)):
            target = lower_batch_size(target)
        profile.update({
            "target_batch_size": target,
            "gpu_memory_total_mib": memory_total_mib or None,
            "batch_penalty_steps": max(0, batch_penalty_steps),
        })
        return profile
    physical_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 1
    available_bytes = int(psutil.virtual_memory().available)
    target = cpu_batch_size(physical_cores, available_bytes)
    for _ in range(max(0, batch_penalty_steps)):
        target = lower_batch_size(target)
    profile.update({
        "target_batch_size": target,
        "physical_cores": physical_cores,
        "available_memory_bytes": available_bytes,
        "batch_penalty_steps": max(0, batch_penalty_steps),
    })
    return profile


def lower_batch_size(current: int) -> int:
    return next((candidate for candidate in BATCH_LADDER if candidate < current), 1)
