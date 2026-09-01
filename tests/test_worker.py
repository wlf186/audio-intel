from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace

import psutil
import pytest

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


def _wait_for_executor_pid(path, predicate=lambda _pid: True, timeout: float = 8.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pid = int(json.loads(path.read_text())["pid"])
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            time.sleep(0.03)
            continue
        if predicate(pid):
            return pid
        time.sleep(0.03)
    raise AssertionError(f"Timed out waiting for executor PID at {path}")


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


def test_terminal_cuda_oom_requests_executor_recycle(tmp_path, monkeypatch) -> None:
    local = replace(
        settings,
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        models_dir=tmp_path / "models",
    )
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(worker_module, "settings", local)
    local.ensure_directories()
    db_module.init_db()
    worker_id = "tts-test-worker"
    job = db_module.create_job("tts", "oom", {
        "text": "你好。", "compute_device": "gpu",
    })
    db_module.claim_job("tts", worker_id)

    def fail(_context):
        exc = RuntimeError("CUDA out of memory")
        worker_module.mark_executor_for_recycle(exc, "cuda_oom")
        raise exc

    outcome = worker_module._run_one_job(
        "tts", job["id"], worker_id, os.getpid(), fail,
    )

    assert outcome == {"state": "failed", "recycle_reason": "cuda_oom"}
    assert db_module.get_job(job["id"])["error_code"] == "RuntimeError"
    assert not (local.temp_dir / job["id"]).exists()


@pytest.mark.skipif(os.name == "nt", reason="fork-only integration injection")
def test_supervisor_rebuilds_poisoned_executor_before_next_job(
    tmp_path, monkeypatch,
) -> None:
    local = replace(
        settings,
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        models_dir=tmp_path / "models",
        worker_poll_seconds=0.03,
        executor_idle_seconds=60,
    )
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(worker_module, "settings", local)
    local.ensure_directories()
    db_module.init_db()
    child_path = local.run_dir / "poison-child.pid"

    def processor(context):
        if context.job["display_name"] == "oom":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            child_path.write_text(str(child.pid), encoding="utf-8")
            exc = RuntimeError("CUDA out of memory")
            worker_module.mark_executor_for_recycle(exc, "cuda_oom")
            raise exc
        return {"duration": 1.0}

    fork_context = worker_module.multiprocessing.get_context("fork")
    monkeypatch.setattr(worker_module, "_processor", lambda _kind: processor)
    monkeypatch.setattr(worker_module.multiprocessing, "get_context", lambda _method: fork_context)
    supervisor = fork_context.Process(target=worker_module.run, args=("tts",))
    supervisor.start()
    metadata = local.run_dir / "tts-executor.json"
    tracked: set[int] = set()
    try:
        initial_pid = _wait_for_executor_pid(metadata)
        tracked.add(initial_pid)
        first = db_module.create_job("tts", "oom", {"compute_device": "gpu"})
        second = db_module.create_job("tts", "next", {"compute_device": "gpu"})

        _wait_for_job(first["id"], lambda job: job["state"] == "failed")
        completed = _wait_for_job(second["id"], lambda job: job["state"] == "succeeded")
        replacement_pid = _wait_for_executor_pid(metadata, lambda pid: pid != initial_pid)
        tracked.add(replacement_pid)
        child_pid = int(child_path.read_text())

        assert completed["result"]["duration"] == 1.0
        assert not psutil.pid_exists(initial_pid)
        assert not psutil.pid_exists(child_pid)
        assert supervisor.is_alive()
    finally:
        supervisor.terminate()
        supervisor.join(timeout=5)
        if supervisor.is_alive():
            supervisor.kill()
            supervisor.join(timeout=2)
        for pid in tracked:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and psutil.pid_exists(pid):
                time.sleep(0.03)
            assert not psutil.pid_exists(pid)


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
        cancel_grace_seconds=1.0,
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
        "AUDIO_INTEL_CANCEL_GRACE_SECONDS": "1",
        "PYTHONPATH": str(settings.root),
    }
    supervisor = subprocess.Popen(
        [sys.executable, "-m", "audio_intel.worker", "tts"],
        cwd=settings.root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    metadata = local.run_dir / "tts-executor.json"
    try:
        initial_executor_pid = _wait_for_executor_pid(metadata)
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
        assert not psutil.pid_exists(initial_executor_pid)
        assert _wait_for_executor_pid(
            metadata, lambda pid: pid != initial_executor_pid,
        ) != initial_executor_pid
        assert not (local.temp_dir / first["id"]).exists()
    finally:
        supervisor.terminate()
        try:
            supervisor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            supervisor.kill()
            supervisor.wait(timeout=2)


@pytest.mark.parametrize("kind", ["asr", "tts"])
def test_worker_reuses_executor_for_queue_then_recycles_once_when_idle(
    tmp_path, monkeypatch, kind,
) -> None:
    local = replace(
        settings,
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        run_dir=tmp_path / "run",
        log_dir=tmp_path / "logs",
        models_dir=tmp_path / "models",
        mock_mode=True,
        worker_poll_seconds=0.03,
        executor_idle_seconds=0.6,
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
        "AUDIO_INTEL_EXECUTOR_IDLE_SECONDS": "0.6",
        "PYTHONPATH": str(settings.root),
    }
    supervisor = subprocess.Popen(
        [sys.executable, "-m", "audio_intel.worker", kind],
        cwd=settings.root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    metadata = local.run_dir / f"{kind}-executor.json"
    tracked: set[int] = set()

    def request(label: str, *, long: bool = False) -> dict[str, object]:
        if kind == "tts":
            return {
                "text": (label + "。") * (120 if long else 1),
                "voice_mode": "preset", "speaker": "Vivian", "compute_device": "cpu",
                "response_format": "wav", "language": "Chinese",
            }
        source = tmp_path / f"{label}.wav"
        source.write_bytes(b"mock audio")
        return {
            "input_path": str(source), "model": "qwen3-asr-0.6b", "compute_device": "cpu",
            "language": "Chinese", "diarize": False, "align": False,
            "use_voiceprint_library": False, "export_formats": ["json"],
        }

    try:
        initial_pid = _wait_for_executor_pid(metadata)
        tracked.add(initial_pid)
        first = db_module.create_job(kind, "first", request("第一批", long=True))
        second = db_module.create_job(kind, "second", request("第二批"))

        _wait_for_job(first["id"], lambda job: job["state"] == "succeeded")
        _wait_for_job(second["id"], lambda job: job["state"] == "succeeded")
        assert _wait_for_executor_pid(metadata) == initial_pid

        replacement_pid = _wait_for_executor_pid(
            metadata, lambda pid: pid != initial_pid, timeout=4,
        )
        tracked.add(replacement_pid)
        assert not psutil.pid_exists(initial_pid)
        time.sleep(0.8)
        assert _wait_for_executor_pid(metadata) == replacement_pid

        third = db_module.create_job(kind, "third", request("回收后继续"))
        _wait_for_job(third["id"], lambda job: job["state"] == "succeeded")
        assert supervisor.poll() is None
        assert not (local.temp_dir / first["id"]).exists()
        assert not (local.temp_dir / second["id"]).exists()
        assert not (local.temp_dir / third["id"]).exists()
    finally:
        supervisor.terminate()
        try:
            supervisor.wait(timeout=5)
        except subprocess.TimeoutExpired:
            supervisor.kill()
            supervisor.wait(timeout=2)
        for pid in tracked:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and psutil.pid_exists(pid):
                time.sleep(0.03)
            assert not psutil.pid_exists(pid)
