from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import replace

import psutil

import audio_intel.db as db_module
import audio_intel.worker as worker_module
from audio_intel.config import settings


def _wait_for_job(job_id: str, predicate, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = db_module.get_job(job_id)
        if job is not None and predicate(job):
            return job
        time.sleep(0.03)
    raise AssertionError(f"Timed out waiting for job {job_id}: {db_module.get_job(job_id)}")


def test_process_tree_termination_removes_descendants() -> None:
    parent = subprocess.Popen(
        [
            sys.executable, "-c",
            "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert parent.stdout is not None
    child_pid = int(parent.stdout.readline().strip())

    remaining = worker_module._terminate_process_tree(parent.pid)

    assert remaining == []
    parent.wait(timeout=2)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and psutil.pid_exists(child_pid):
        time.sleep(0.03)
    assert not psutil.pid_exists(child_pid)


def test_supervised_worker_cancels_running_job_and_continues(tmp_path, monkeypatch) -> None:
    local = replace(
        settings,
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        models_dir=tmp_path / "models",
        mock_mode=True,
        worker_poll_seconds=0.03,
        cancel_grace_seconds=0.0,
    )
    monkeypatch.setattr(db_module, "settings", local)
    db_module.init_db()
    environment = {
        **os.environ,
        "AUDIO_INTEL_DATA_DIR": str(local.data_dir),
        "AUDIO_INTEL_TEMP_DIR": str(local.temp_dir),
        "AUDIO_INTEL_RUN_DIR": str(local.run_dir),
        "AUDIO_INTEL_LOG_DIR": str(local.log_dir),
        "AUDIO_INTEL_MODELS_DIR": str(local.models_dir),
        "AUDIO_INTEL_MOCK_MODE": "1",
        "AUDIO_INTEL_WORKER_POLL_SECONDS": "0.03",
        "AUDIO_INTEL_CANCEL_GRACE_SECONDS": "0",
        "PYTHONPATH": str(settings.root),
    }
    supervisor = subprocess.Popen(
        [sys.executable, "-m", "audio_intel.worker", "tts"],
        cwd=settings.root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        first = db_module.create_job("tts", "blocking", {
            "text": "测" * 3000, "voice_mode": "preset", "speaker": "Vivian",
            "compute_device": "cpu", "response_format": "wav", "language": "Chinese",
        })
        _wait_for_job(
            first["id"],
            lambda job: job["state"] == "running" and job["stage"].startswith("synthesizing_"),
        )
        started = time.monotonic()
        db_module.request_cancel(first["id"])
        second = db_module.create_job("tts", "next", {
            "text": "你好。", "voice_mode": "preset", "speaker": "Vivian",
            "compute_device": "cpu", "response_format": "wav", "language": "Chinese",
        })

        cancelled = _wait_for_job(first["id"], lambda job: job["state"] == "cancelled", timeout=3)
        completed = _wait_for_job(second["id"], lambda job: job["state"] == "succeeded")

        assert time.monotonic() - started < 3
        assert cancelled["stage"] == "cancelled"
        assert completed["result"]["duration"] > 0
        assert supervisor.poll() is None
        assert not (local.temp_dir / first["id"]).exists()
    finally:
        supervisor.terminate()
        try:
            supervisor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            supervisor.kill()
            supervisor.wait(timeout=2)
