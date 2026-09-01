from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import audio_intel.gpu as gpu_module


def test_gpu_snapshot_targets_cuda_zero_and_parses_dynamic_name(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run(command, **_):
        captured.extend(command)
        return SimpleNamespace(stdout="Dynamic Test GPU, 123, 3650, 4096, 45\n")

    monkeypatch.setattr(gpu_module.subprocess, "run", fake_run)
    snapshot = gpu_module.gpu_snapshot(0)
    assert snapshot == {
        "name": "Dynamic Test GPU", "memory_used_mib": 123, "memory_free_mib": 3650,
        "memory_total_mib": 4096, "memory_system_reserved_mib": 323, "utilization": 45,
    }
    assert "--id=0" in captured


def test_gpu_snapshot_falls_back_when_free_memory_query_is_unsupported(monkeypatch) -> None:
    calls = 0

    def fake_run(_command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise subprocess.CalledProcessError(1, "nvidia-smi")
        return SimpleNamespace(stdout="Fallback GPU, 12, 8192, 3\n")

    monkeypatch.setattr(gpu_module.subprocess, "run", fake_run)

    assert gpu_module.gpu_snapshot(0) == {
        "name": "Fallback GPU", "memory_used_mib": 12,
        "memory_total_mib": 8192, "utilization": 3,
    }


def test_gpu_compute_processes_are_best_effort(monkeypatch) -> None:
    monkeypatch.setattr(
        gpu_module.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout="42, python, 512\n43, hidden, N/A\n"),
    )

    assert gpu_module.gpu_compute_processes(0) == [
        {"pid": 42, "process_name": "python", "memory_used_mib": 512},
        {"pid": 43, "process_name": "hidden", "memory_used_mib": None},
    ]


def test_gpu_lease_serializes_independent_processes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(gpu_module, "settings", SimpleNamespace(run_dir=tmp_path))
    root = Path(gpu_module.__file__).resolve().parent.parent
    environment = os.environ.copy()
    environment["AUDIO_INTEL_RUN_DIR"] = str(tmp_path)
    script = """
from audio_intel.gpu import gpu_lease
print("ready", flush=True)
with gpu_lease(poll_seconds=0.02):
    print("acquired", flush=True)
"""
    with gpu_module.gpu_lease():
        child = subprocess.Popen(
            [sys.executable, "-c", script],
            cwd=root,
            env={**environment, "PYTHONPATH": str(root)},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        try:
            child.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
        else:
            stdout, stderr = child.communicate()
            raise AssertionError(f"GPU lease was not exclusive: {stdout} {stderr}")
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode == 0, stderr
    assert "acquired" in stdout
