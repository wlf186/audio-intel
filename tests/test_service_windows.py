from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest


ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "service.cmd"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="service.cmd requires native Windows")


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _environment(tmp_path: Path, port: int | None = None) -> dict[str, str]:
    return {
        **os.environ,
        "AUDIO_INTEL_HOST": "127.0.0.1",
        "AUDIO_INTEL_PORT": str(port or _unused_port()),
        "AUDIO_INTEL_MOCK_MODE": "1",
        "AUDIO_INTEL_DATA_DIR": str(tmp_path / "data"),
        "AUDIO_INTEL_TEMP_DIR": str(tmp_path / "tmp"),
        "AUDIO_INTEL_CACHE_DIR": str(tmp_path / "cache"),
        "AUDIO_INTEL_LOG_DIR": str(tmp_path / "logs"),
        "AUDIO_INTEL_RUN_DIR": str(tmp_path / "run"),
        "AUDIO_INTEL_MODELS_DIR": str(tmp_path / "models"),
        "AUDIO_INTEL_FRONTEND_DIR": str(ROOT / "frontend" / "dist"),
        "AUDIO_INTEL_MIN_FREE_DISK_BYTES": "0",
        "AUDIO_INTEL_WORKER_POLL_SECONDS": "0.03",
        "AUDIO_INTEL_CANCEL_GRACE_SECONDS": "0",
    }


def _run_service(*args: str, env: dict[str, str], timeout: float = 45) -> subprocess.CompletedProcess[str]:
    command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(SERVICE), *args]
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        stdout, stderr = process.communicate(timeout=10)
        stderr += f"\nservice command timed out after {timeout} seconds"
        return subprocess.CompletedProcess(command, 124, stdout, stderr)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _read_pids(run_dir: Path) -> dict[str, int]:
    return {
        component: int((run_dir / f"{component}.pid").read_text().strip())
        for component in ("api", "asr", "tts")
    }


def _read_executor_pids(run_dir: Path) -> list[int]:
    return [
        int(json.loads((run_dir / f"{kind}-executor.json").read_text())["pid"])
        for kind in ("asr", "tts")
    ]


def _process_alive(process_id: int) -> bool:
    try:
        process = psutil.Process(process_id)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False
    except psutil.AccessDenied:
        return True


def _assert_processes_exit(process_ids: list[int], timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_process_alive(process_id) for process_id in process_ids):
        time.sleep(0.05)
    assert not [process_id for process_id in process_ids if _process_alive(process_id)]


def _stop_isolated(env: dict[str, str]) -> None:
    _run_service("stop", "all", env=env, timeout=30)


def test_windows_start_restart_and_stop_complete_process_trees(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    tracked: list[int] = []
    try:
        started = _run_service("start", "all", env=env)
        assert started.returncode == 0, started.stdout + started.stderr
        assert started.stdout.count("started ") == 3
        assert (tmp_path / "logs" / "api.log").is_file()
        first_processes = list(_read_pids(run_dir).values()) + _read_executor_pids(run_dir)
        tracked.extend(first_processes)

        restarted = _run_service("restart", "all", env=env)
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        _assert_processes_exit(first_processes)
        second_processes = list(_read_pids(run_dir).values()) + _read_executor_pids(run_dir)
        tracked.extend(second_processes)

        status = _run_service("status", env=env)
        assert status.returncode == 0
        assert status.stdout.count(": running (pid ") == 3

        stopped = _run_service("stop", "all", env=env)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not list(run_dir.glob("*.pid"))
        _assert_processes_exit(second_processes)
    finally:
        _stop_isolated(env)
        _assert_processes_exit(tracked)


def test_windows_stale_pid_does_not_signal_unrelated_process(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "api.pid").write_text(str(os.getpid()))

    status = _run_service("status", env=env)

    assert status.returncode == 0
    assert "api: stopped" in status.stdout
    assert not (run_dir / "api.pid").exists()
    assert psutil.Process(os.getpid()).is_running()


def test_windows_port_conflict_fails_without_process_residue(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        env = _environment(tmp_path, port)

        started = _run_service("start", "api", env=env)

    assert started.returncode != 0
    assert "api failed to start" in (started.stdout + started.stderr)
    assert not (tmp_path / "run" / "api.pid").exists()
