from __future__ import annotations

from types import SimpleNamespace

import audio_intel.gpu as gpu_module


def test_gpu_snapshot_targets_cuda_zero_and_parses_dynamic_name(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run(command, **_):
        captured.extend(command)
        return SimpleNamespace(stdout="Dynamic Test GPU, 123, 4096, 45\n")

    monkeypatch.setattr(gpu_module.subprocess, "run", fake_run)
    snapshot = gpu_module.gpu_snapshot(0)
    assert snapshot == {
        "name": "Dynamic Test GPU", "memory_used_mib": 123, "memory_total_mib": 4096, "utilization": 45,
    }
    assert "--id=0" in captured
