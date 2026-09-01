from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from audio_intel.deployment import deployment_metadata, read_deployment_profile
from scripts.runtime_profile import nonterminal_jobs


def test_deployment_profile_defaults_to_full_and_reads_cpu(tmp_path: Path) -> None:
    assert read_deployment_profile(tmp_path) == "full"
    marker = tmp_path / ".runtime" / "deployment-profile"
    marker.parent.mkdir()
    marker.write_text("CPU\n", encoding="utf-8")
    assert read_deployment_profile(tmp_path) == "cpu"
    assert deployment_metadata("cpu") == {
        "profile": "cpu", "default_compute_device": "cpu", "gpu_runtime_installed": False,
    }


def test_invalid_deployment_profile_is_rejected(tmp_path: Path) -> None:
    marker = tmp_path / ".runtime" / "deployment-profile"
    marker.parent.mkdir()
    marker.write_text("hybrid", encoding="utf-8")
    with pytest.raises(RuntimeError, match="expected full or cpu"):
        read_deployment_profile(tmp_path)


def test_profile_switch_guard_counts_nonterminal_jobs(tmp_path: Path) -> None:
    database = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (state TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO jobs(state) VALUES (?)",
            [("queued",), ("running",), ("cancelling",), ("succeeded",), ("failed",)],
        )
    assert nonterminal_jobs(database) == 3


@pytest.mark.parametrize("os_name", ["linux", "windows"])
@pytest.mark.parametrize("name", ["asr", "tts", "aligner"])
def test_cpu_locks_exclude_gpu_runtime_packages(os_name: str, name: str) -> None:
    lock = Path("requirements-lock") / os_name / f"{name}-cpu.txt"
    contents = lock.read_text(encoding="utf-8").lower()
    assert "torch==2.11.0+cpu" in contents
    assert "nvidia-" not in contents
    assert "triton==" not in contents
