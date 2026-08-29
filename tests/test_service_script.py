from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil
import pytest


ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "service.sh"
pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="service.sh requires Linux /proc")


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


def _run_service(*args: str, env: dict[str, str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SERVICE), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


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


def _process_alive(pid: int) -> bool:
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _assert_processes_exit(pids: list[int], timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_process_alive(pid) for pid in pids):
        time.sleep(0.05)
    assert not [pid for pid in pids if _process_alive(pid)]


def _wait_for_foreground_ready(
    manager: subprocess.Popen[bytes], run_dir: Path, output_path: Path, timeout: float = 15,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.poll() is not None:
            raise AssertionError(
                f"service manager exited with {manager.returncode}:\n{output_path.read_text()}"
            )
        if all((run_dir / f"{component}.pid").is_file() for component in ("api", "asr", "tts")):
            if "Sandevistan-Audio:" in output_path.read_text():
                return
        time.sleep(0.05)
    raise AssertionError(f"foreground services did not become ready:\n{output_path.read_text()}")


def _stop_isolated(env: dict[str, str]) -> None:
    _run_service("stop", "all", env=env, timeout=20)


def test_background_start_waits_until_ready_and_stops_complete_tree(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    tracked: list[int] = []
    try:
        started = _run_service("start", "all", env=env)
        assert started.returncode == 0, started.stdout + started.stderr
        assert "started api" in started.stdout
        assert "started asr" in started.stdout
        assert "started tts" in started.stdout

        status = _run_service("status", env=env)
        assert status.returncode == 0
        assert status.stdout.count(": running (pid ") == 3
        assert (tmp_path / "logs" / "api.log").is_file()

        component_pids = list(_read_pids(run_dir).values())
        for pid in component_pids:
            assert os.getsid(pid) == pid
            assert os.getpgid(pid) == pid
        tracked = component_pids + _read_executor_pids(run_dir)
        stopped = _run_service("stop", "all", env=env, timeout=20)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not list(run_dir.glob("*.pid"))
        _assert_processes_exit(tracked)
    finally:
        _stop_isolated(env)
        _assert_processes_exit(tracked)


def test_background_start_survives_manager_process_group_cleanup(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    output_path = tmp_path / "manager.log"
    tracked: list[int] = []
    try:
        with output_path.open("wb") as output:
            manager = subprocess.Popen(
                ["bash", "-c", 'set -e; "$1" start all; kill -TERM -- -$$', "manager", str(SERVICE)],
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            assert manager.wait(timeout=30) != 0

        status = _run_service("status", env=env)
        assert status.returncode == 0, status.stdout + status.stderr
        assert status.stdout.count(": running (pid ") == 3
        tracked = list(_read_pids(run_dir).values()) + _read_executor_pids(run_dir)
    finally:
        _stop_isolated(env)
        _assert_processes_exit(tracked)


def test_restart_replaces_complete_process_trees(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    tracked: list[int] = []
    try:
        started = _run_service("start", "all", env=env)
        assert started.returncode == 0, started.stdout + started.stderr
        old_roots = list(_read_pids(run_dir).values())
        old_processes = old_roots + _read_executor_pids(run_dir)
        tracked.extend(old_processes)

        restarted = _run_service("restart", "all", env=env, timeout=40)
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        _assert_processes_exit(old_processes)

        new_roots = list(_read_pids(run_dir).values())
        new_processes = new_roots + _read_executor_pids(run_dir)
        tracked.extend(new_processes)
        assert set(old_roots).isdisjoint(new_roots)
        status = _run_service("status", env=env)
        assert status.returncode == 0, status.stdout + status.stderr
        assert status.stdout.count(": running (pid ") == 3
    finally:
        _stop_isolated(env)
        _assert_processes_exit(tracked)


def test_foreground_run_handles_sigterm_and_cleans_complete_tree(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    output_path = tmp_path / "manager.log"
    tracked: list[int] = []
    with output_path.open("wb") as output:
        manager = subprocess.Popen(
            [str(SERVICE), "run", "all"], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_foreground_ready(manager, run_dir, output_path)
            tracked = list(_read_pids(run_dir).values()) + _read_executor_pids(run_dir)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{env['AUDIO_INTEL_PORT']}/api/v1/health", timeout=2,
            ) as response:
                assert response.status == 200

            manager.send_signal(signal.SIGTERM)
            assert manager.wait(timeout=20) == 0
            assert not list(run_dir.glob("*.pid"))
            _assert_processes_exit(tracked)
        finally:
            if manager.poll() is None:
                manager.kill()
                manager.wait(timeout=5)
            _stop_isolated(env)
            _assert_processes_exit(tracked)


def test_foreground_run_fails_and_cleans_up_after_worker_exit(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    output_path = tmp_path / "manager.log"
    tracked: list[int] = []
    with output_path.open("wb") as output:
        manager = subprocess.Popen(
            [str(SERVICE), "run", "all"], cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_foreground_ready(manager, run_dir, output_path)
            pids = _read_pids(run_dir)
            tracked = list(pids.values()) + _read_executor_pids(run_dir)
            os.kill(pids["asr"], signal.SIGKILL)

            assert manager.wait(timeout=20) != 0
            assert "asr exited unexpectedly" in output_path.read_text()
            assert not list(run_dir.glob("*.pid"))
            _assert_processes_exit(tracked)
        finally:
            if manager.poll() is None:
                manager.kill()
                manager.wait(timeout=5)
            _stop_isolated(env)
            _assert_processes_exit(tracked)


def test_stale_pid_is_removed_without_signalling_unrelated_process(tmp_path: Path) -> None:
    env = _environment(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "api.pid").write_text(str(os.getpid()))

    status = _run_service("status", env=env)

    assert status.returncode == 0
    assert "api: stopped" in status.stdout
    assert not (run_dir / "api.pid").exists()
    assert psutil.Process(os.getpid()).is_running()


def test_port_conflict_fails_without_leaving_a_pid_file(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        env = _environment(tmp_path, port)

        started = _run_service("start", "api", env=env)

    assert started.returncode != 0
    assert "api failed to start" in started.stderr
    assert not (tmp_path / "run" / "api.pid").exists()
