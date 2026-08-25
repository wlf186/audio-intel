from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .config import settings
from .db import (
    claim_job, finish_job, get_job, init_db, recover_stale, update_job,
    update_voiceprint_sample, upsert_worker, utcnow,
)


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
        # Sample bookkeeping must never prevent the job from reaching a terminal state.
        pass


class JobContext:
    def __init__(self, job: dict[str, Any], worker_id: str) -> None:
        self.job = job
        self.job_id = job["id"]
        self.worker_id = worker_id
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
        upsert_worker(self.worker_id, self.job["kind"], os.getpid(), "busy", self.job_id, {"stage": stage})


def run(kind: str) -> None:
    init_db()
    worker_id = f"{kind}-{socket.gethostname()}-{os.getpid()}"
    stop = False

    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    recovered = recover_stale(kind)
    processor: Callable[[JobContext], dict[str, Any]]
    if kind == "asr":
        from asr.pipeline import process_job
        processor = process_job
    else:
        from tts.pipeline import process_job
        processor = process_job
    upsert_worker(worker_id, kind, os.getpid(), "idle", details={"recovered_jobs": recovered})

    while not stop:
        job = claim_job(kind, worker_id)
        if job is None:
            upsert_worker(worker_id, kind, os.getpid(), "idle")
            time.sleep(settings.worker_poll_seconds)
            continue
        context = JobContext(job, worker_id)
        try:
            result = processor(context)
            finish_job(
                job["id"], "succeeded", stage="completed", progress=1,
                result_json=result, heartbeat_at=utcnow(),
            )
        except JobCancelled as exc:
            _fail_voiceprint_import(job, str(exc))
            finish_job(
                job["id"], "cancelled", stage="cancelled", error_code="cancelled",
                error_message=str(exc), heartbeat_at=utcnow(),
            )
        except Exception as exc:  # keep the worker alive for subsequent jobs
            _fail_voiceprint_import(job, str(exc))
            error_path = settings.jobs_dir / job["id"] / "error.log"
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_path.write_text(traceback.format_exc(), encoding="utf-8")
            finish_job(
                job["id"], "failed", stage="failed", error_code=type(exc).__name__,
                error_message=str(exc)[:2000], heartbeat_at=utcnow(),
            )
        finally:
            shutil.rmtree(context.work_dir, ignore_errors=True)
            upsert_worker(worker_id, kind, os.getpid(), "idle")

    upsert_worker(worker_id, kind, os.getpid(), "stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("asr", "tts"))
    run(parser.parse_args().kind)
