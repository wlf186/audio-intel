from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audio_intel.config import settings
from audio_intel.db import list_workers


EXPECTED_COMMANDS = {
    "api": ("-m", "uvicorn", "audio_intel.api:app"),
    "asr": ("-m", "audio_intel.worker", "asr"),
    "tts": ("-m", "audio_intel.worker", "tts"),
}


def _matching_process(component: str, pid: int) -> psutil.Process | None:
    try:
        process = psutil.Process(pid)
        matches = (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
            and all(value in process.cmdline() for value in EXPECTED_COMMANDS[component])
        )
        return process if matches else None
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return None


def _process_matches(component: str, pid: int) -> bool:
    try:
        return _matching_process(component, pid) is not None
    except psutil.AccessDenied:
        return False


def _alive(processes: list[psutil.Process]) -> list[psutil.Process]:
    alive = []
    for process in processes:
        try:
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                alive.append(process)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
        except psutil.AccessDenied:
            alive.append(process)
    return alive


def _terminate_tree(pid: int) -> list[int]:
    try:
        root = psutil.Process(pid)
        processes = list(reversed(root.children(recursive=True))) + [root]
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return []
    except psutil.AccessDenied:
        return [pid]

    targets = _alive(processes)
    for process in targets:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    _, alive = psutil.wait_procs(targets, timeout=1.0)
    for process in alive:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    _, alive = psutil.wait_procs(alive, timeout=1.0)
    return [process.pid for process in _alive(alive)]


def _cleanup_executor(kind: str) -> list[int]:
    path = settings.run_dir / f"{kind}-executor.json"
    metadata: object = {}
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise TypeError("executor metadata must be an object")
        pid = int(metadata["pid"])
        expected_created = float(metadata["created_at"])
        expected_executable = Path(metadata["executable"]).resolve()
        process = psutil.Process(pid)
        matches = (
            abs(process.create_time() - expected_created) < 0.01
            and Path(process.exe()).resolve() == expected_executable
        )
        remaining = _terminate_tree(pid) if matches else []
    except (
        json.JSONDecodeError,
        KeyError,
        OSError,
        TypeError,
        ValueError,
        psutil.NoSuchProcess,
        psutil.ZombieProcess,
    ):
        remaining = []
    except psutil.AccessDenied:
        remaining = [int(metadata.get("pid", -1))] if isinstance(metadata, dict) else [-1]

    if not remaining:
        path.unlink(missing_ok=True)
    return remaining


def cleanup(component: str, pid: int) -> int:
    remaining: list[int] = []
    if pid > 0:
        try:
            if _matching_process(component, pid) is not None:
                remaining.extend(_terminate_tree(pid))
        except psutil.AccessDenied:
            remaining.append(pid)
    if component in {"asr", "tts"}:
        remaining.extend(_cleanup_executor(component))
    remaining = sorted(set(remaining))
    if remaining:
        print(f"Unable to stop {component} processes: {remaining}", file=sys.stderr)
        return 1
    return 0


def _probe_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "*"}:
        return "127.0.0.1"
    if bind_host in {"::", "[::]"}:
        return "::1"
    return bind_host


def wait_api(pid: int, bind_host: str, port: int, timeout: float) -> int:
    host = _probe_host(bind_host)
    authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_error = "health probe did not respond"
    while time.monotonic() < deadline:
        if not _process_matches("api", pid):
            print("API process exited before becoming ready", file=sys.stderr)
            return 1
        try:
            with opener.open(f"http://{authority}/api/v1/health", timeout=0.75) as response:
                payload = json.load(response)
                if response.status == 200 and payload.get("status") == "ok":
                    return 0
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    print(f"API readiness timed out: {last_error}", file=sys.stderr)
    return 1


def wait_worker(kind: str, pid: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    last_error = "worker registration was not found"
    while time.monotonic() < deadline:
        if not _process_matches(kind, pid):
            print(f"{kind} process exited before becoming ready", file=sys.stderr)
            return 1
        try:
            worker = next(
                (
                    item
                    for item in list_workers()
                    if item["kind"] == kind and int(item["pid"]) == pid
                ),
                None,
            )
            if worker is not None and worker["state"] != "stopped":
                return 0
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    print(f"{kind} readiness timed out: {last_error}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)

    matches_parser = subparsers.add_parser("matches")
    matches_parser.add_argument("component", choices=EXPECTED_COMMANDS)
    matches_parser.add_argument("pid", type=int)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("component", choices=EXPECTED_COMMANDS)
    cleanup_parser.add_argument("pid", type=int)

    api_parser = subparsers.add_parser("wait-api")
    api_parser.add_argument("pid", type=int)
    api_parser.add_argument("host")
    api_parser.add_argument("port", type=int)
    api_parser.add_argument("timeout", type=float)

    worker_parser = subparsers.add_parser("wait-worker")
    worker_parser.add_argument("kind", choices=("asr", "tts"))
    worker_parser.add_argument("pid", type=int)
    worker_parser.add_argument("timeout", type=float)

    args = parser.parse_args()
    if args.action == "matches":
        return 0 if _process_matches(args.component, args.pid) else 1
    if args.action == "cleanup":
        return cleanup(args.component, args.pid)
    if args.action == "wait-api":
        return wait_api(args.pid, args.host, args.port, args.timeout)
    return wait_worker(args.kind, args.pid, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
