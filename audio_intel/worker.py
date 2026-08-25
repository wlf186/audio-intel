from __future__ import annotations

import argparse
import multiprocessing
import os
import shutil
import signal
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

import psutil

from .config import settings
from .db import (
    claim_job, finish_job, get_job, init_db, recover_stale, update_job,
    update_voiceprint_sample, upsert_worker, utcnow,
)
from .utils import atomic_json, read_json


WORKER_MONITOR_SECONDS = 0.10
EXECUTOR_TERMINATE_SECONDS = 0.75
EXECUTOR_KILL_SECONDS = 0.50
WORKER_HEARTBEAT_SECONDS = 1.0


class JobCancelled(RuntimeError):
    pass


def _fail_voiceprint_import(job: dict[str, Any], message: str) -> None:
    request = job.get("request") or {}
    if request.get("purpose") != "voiceprint_import":
        return
    try:
        update_voiceprint_sample(
            request.get("voiceprint_sample_id", ""), state="failed",
            error_message=message[:500],
        )
    except Exception:
        pass


class JobContext:
    def __init__(self, job: dict[str, Any], worker_id: str, worker_pid: int | None = None) -> None:
        self.job = job
        self.job_id = job["id"]
        self.worker_id = worker_id
        self.worker_pid = worker_pid or os.getpid()
        self.work_dir = settings.temp_dir / self.job_id
        self.output_dir = settings.jobs_dir / self.job_id / "output"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def progress(self, value: float, stage: str) -> None:
        current = get_job(self.job_id)
        if current and current.get("cancel_requested"):
            raise JobCancelled("Job cancellation requested")
        update_job(
            self.job_id,
            progress=max(0.01, min(float(value), 0.99)),
            stage=stage,
            heartbeat_at=utcnow(),
        )
        upsert_worker(
            self.worker_id, self.job["kind"], self.worker_pid, "busy", self.job_id,
            {"stage": stage, "executor_pid": os.getpid()},
        )


def _processor(kind: str) -> Callable[[JobContext], dict[str, Any]]:
    if kind == "asr":
        from asr.pipeline import process_job
    else:
        from tts.pipeline import process_job
    return process_job


def _run_one_job(
    kind: str,
    job_id: str,
    worker_id: str,
    worker_pid: int,
    processor: Callable[[JobContext], dict[str, Any]],
) -> str:
    job = get_job(job_id)
    if job is None or job["state"] != "running" or job.get("worker_id") != worker_id:
        return "skipped"
    context = JobContext(job, worker_id, worker_pid)
    state = "failed"
    try:
        result = processor(context)
        current = get_job(job_id)
        if current and current.get("cancel_requested"):
            raise JobCancelled("Job cancellation requested")
        finish_job(
            job_id, "succeeded", stage="completed", progress=1,
            result_json=result, heartbeat_at=utcnow(),
        )
        state = "succeeded"
    except JobCancelled as exc:
        _fail_voiceprint_import(job, str(exc))
        finish_job(
            job_id, "cancelled", stage="cancelled", error_code="cancelled",
            error_message=str(exc), heartbeat_at=utcnow(),
        )
        state = "cancelled"
    except Exception as exc:
        _fail_voiceprint_import(job, str(exc))
        error_path = settings.jobs_dir / job_id / "error.log"
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        finish_job(
            job_id, "failed", stage="failed", error_code=type(exc).__name__,
            error_message=str(exc)[:2000], heartbeat_at=utcnow(),
        )
        state = "failed"
    finally:
        shutil.rmtree(context.work_dir, ignore_errors=True)
    return state


def _executor_main(kind: str, worker_id: str, worker_pid: int, connection: Any) -> None:
    processor = _processor(kind)
    try:
        while True:
            try:
                job_id = connection.recv()
            except EOFError:
                return
            if job_id is None:
                return
            state = _run_one_job(kind, str(job_id), worker_id, worker_pid, processor)
            try:
                connection.send({"job_id": job_id, "state": state})
            except (BrokenPipeError, EOFError, OSError):
                return
    finally:
        connection.close()


def _executor_metadata_path(kind: str) -> Path:
    return settings.run_dir / f"{kind}-executor.json"


def _alive(processes: list[psutil.Process]) -> list[psutil.Process]:
    alive = []
    for process in processes:
        try:
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                alive.append(process)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
    return alive


def _terminate_processes(processes: list[psutil.Process]) -> list[psutil.Process]:
    targets = _alive(processes)
    for process in targets:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(targets, timeout=EXECUTOR_TERMINATE_SECONDS)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(alive, timeout=EXECUTOR_KILL_SECONDS)
    return _alive(alive)


def _terminate_process_tree(pid: int) -> list[int]:
    try:
        root = psutil.Process(pid)
        processes = list(reversed(root.children(recursive=True))) + [root]
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return []
    return [process.pid for process in _terminate_processes(processes)]


def _terminate_remaining(pids: list[int]) -> list[int]:
    processes = []
    for pid in pids:
        try:
            processes.append(psutil.Process(pid))
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
    return [process.pid for process in _terminate_processes(processes)]


def _cleanup_stale_executor(kind: str) -> None:
    path = _executor_metadata_path(kind)
    metadata = read_json(path, {}) or {}
    try:
        pid = int(metadata["pid"])
        expected_created = float(metadata["created_at"])
        process = psutil.Process(pid)
        same_process = abs(process.create_time() - expected_created) < 0.01
        same_runtime = Path(process.exe()).resolve() == Path(metadata["executable"]).resolve()
        if same_process and same_runtime:
            remaining = _terminate_process_tree(pid)
            if remaining:
                raise RuntimeError(f"Unable to stop stale executor processes: {remaining}")
    except (
        KeyError, TypeError, ValueError, psutil.NoSuchProcess,
        psutil.ZombieProcess, psutil.AccessDenied,
    ):
        pass
    finally:
        path.unlink(missing_ok=True)


def _spawn_executor(kind: str, worker_id: str, worker_pid: int) -> tuple[Any, Any]:
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe()
    process = context.Process(
        target=_executor_main,
        args=(kind, worker_id, worker_pid, child_connection),
        name=f"audio-intel-{kind}-executor",
    )
    process.start()
    child_connection.close()
    created_at = psutil.Process(process.pid).create_time()
    atomic_json(_executor_metadata_path(kind), {
        "pid": process.pid,
        "created_at": created_at,
        "executable": sys.executable,
        "worker_id": worker_id,
    })
    return process, parent_connection


def _forced_cancel(job: dict[str, Any]) -> None:
    current = get_job(job["id"])
    if current is None or current["state"] != "running":
        return
    message = "Job force-stopped after cancellation request"
    _fail_voiceprint_import(current, message)
    finish_job(
        job["id"], "cancelled", stage="cancelled", error_code="cancelled",
        error_message=message, heartbeat_at=utcnow(),
    )
    shutil.rmtree(settings.temp_dir / job["id"], ignore_errors=True)


def run(kind: str) -> None:
    init_db()
    worker_pid = os.getpid()
    worker_id = f"{kind}-{socket.gethostname()}-{worker_pid}"
    stop = False

    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    _cleanup_stale_executor(kind)
    recovered = recover_stale(kind)
    process, connection = _spawn_executor(kind, worker_id, worker_pid)
    upsert_worker(worker_id, kind, worker_pid, "idle", details={
        "recovered_jobs": recovered, "executor_pid": process.pid,
    })
    current_job: dict[str, Any] | None = None
    next_heartbeat = 0.0
    remaining_processes: list[int] = []

    try:
        while not stop:
            if not process.is_alive():
                if current_job is not None:
                    current = get_job(current_job["id"])
                    if current and current["state"] == "running":
                        if current.get("cancel_requested"):
                            _forced_cancel(current)
                        else:
                            message = f"{kind.upper()} execution process exited unexpectedly"
                            _fail_voiceprint_import(current, message)
                            finish_job(
                                current["id"], "failed", stage="failed",
                                error_code="WorkerProcessExit", error_message=message,
                                heartbeat_at=utcnow(),
                            )
                            shutil.rmtree(settings.temp_dir / current["id"], ignore_errors=True)
                connection.close()
                process.join(timeout=0.1)
                time.sleep(min(settings.worker_poll_seconds, 0.5))
                process, connection = _spawn_executor(kind, worker_id, worker_pid)
                current_job = None
                upsert_worker(worker_id, kind, worker_pid, "idle", details={"executor_pid": process.pid})

            if current_job is None:
                current_job = claim_job(kind, worker_id)
                if current_job is None:
                    upsert_worker(worker_id, kind, worker_pid, "idle", details={"executor_pid": process.pid})
                    time.sleep(settings.worker_poll_seconds)
                    continue
                upsert_worker(
                    worker_id, kind, worker_pid, "busy", current_job["id"],
                    {"stage": "starting", "executor_pid": process.pid},
                )
                connection.send(current_job["id"])
                next_heartbeat = 0.0

            if connection.poll():
                try:
                    completed = connection.recv()
                except EOFError:
                    continue
                if completed.get("job_id") == current_job["id"]:
                    current_job = None
                    upsert_worker(worker_id, kind, worker_pid, "idle", details={"executor_pid": process.pid})
                continue

            current = get_job(current_job["id"])
            if current is None or current["state"] != "running":
                time.sleep(WORKER_MONITOR_SECONDS)
                continue

            now = time.monotonic()
            if now >= next_heartbeat:
                update_job(current["id"], heartbeat_at=utcnow())
                upsert_worker(
                    worker_id, kind, worker_pid, "busy", current["id"],
                    {"stage": current["stage"], "executor_pid": process.pid},
                )
                next_heartbeat = now + WORKER_HEARTBEAT_SECONDS

            if not current.get("cancel_requested"):
                time.sleep(WORKER_MONITOR_SECONDS)
                continue

            cooperative_deadline = time.monotonic() + max(0.0, settings.cancel_grace_seconds)
            while time.monotonic() < cooperative_deadline and process.is_alive():
                if connection.poll(WORKER_MONITOR_SECONDS):
                    try:
                        completed = connection.recv()
                    except EOFError:
                        break
                    if completed.get("job_id") == current["id"]:
                        current_job = None
                        break
                latest = get_job(current["id"])
                if latest is None or latest["state"] != "running":
                    current_job = None
                    break
            if current_job is None:
                upsert_worker(worker_id, kind, worker_pid, "idle", details={"executor_pid": process.pid})
                continue

            remaining_processes = _terminate_process_tree(process.pid)
            while remaining_processes and not stop:
                update_job(
                    current["id"], stage="cancelling",
                    error_message=f"Waiting for worker processes to stop: {remaining_processes}",
                    heartbeat_at=utcnow(),
                )
                time.sleep(0.25)
                remaining_processes = _terminate_remaining(remaining_processes)
            if remaining_processes:
                continue
            process.join(timeout=0.1)
            _forced_cancel(current)
            connection.close()
            _executor_metadata_path(kind).unlink(missing_ok=True)
            process, connection = _spawn_executor(kind, worker_id, worker_pid)
            current_job = None
            upsert_worker(worker_id, kind, worker_pid, "idle", details={"executor_pid": process.pid})
    finally:
        try:
            connection.close()
        except Exception:
            pass
        if process.pid:
            remaining_processes.extend(_terminate_process_tree(process.pid))
        remaining_processes = list(dict.fromkeys(remaining_processes))
        while remaining_processes:
            remaining_processes = _terminate_remaining(remaining_processes)
        process.join(timeout=0.1)
        _executor_metadata_path(kind).unlink(missing_ok=True)
        upsert_worker(worker_id, kind, worker_pid, "stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("asr", "tts"))
    run(parser.parse_args().kind)
