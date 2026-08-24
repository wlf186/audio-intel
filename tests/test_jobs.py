from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import audio_intel.api as api_module
import audio_intel.db as db_module
import audio_intel.gpu as gpu_module
import audio_intel.purge as purge_module
from audio_intel.config import settings


def local_settings(tmp_path):
    return replace(
        settings,
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        enabled_services=frozenset({"asr", "tts"}),
    )


def install_settings(local, monkeypatch) -> None:
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(purge_module, "settings", local)


def create_payload(local, job_id: str, content: bytes = b"task-data") -> None:
    output = local.jobs_dir / job_id / "output" / "result.bin"
    temporary = local.temp_dir / job_id / "nested" / "partial.bin"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    temporary.write_bytes(content)


def test_processing_time_accumulates_across_retries(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    db_module.init_db()
    job = db_module.create_job("tts", "timed", {"text": "test"})
    claimed = db_module.claim_job("tts", "worker-one")
    assert claimed and claimed["id"] == job["id"]
    first_start = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(timespec="seconds")
    db_module.update_job(job["id"], started_at=first_start)
    finished = db_module.finish_job(job["id"], "failed", stage="failed", progress=.5)
    assert finished and 4 <= finished["processing_seconds"] <= 7
    first_total = finished["processing_seconds"]

    db_module.retry_job(job["id"])
    claimed = db_module.claim_job("tts", "worker-two")
    assert claimed and claimed["processing_seconds"] == first_total
    second_start = (datetime.now(timezone.utc) - timedelta(seconds=3)).isoformat(timespec="seconds")
    db_module.update_job(job["id"], started_at=second_start)
    finished = db_module.finish_job(job["id"], "succeeded", stage="completed", progress=1)
    assert finished and finished["attempts"] == 2
    assert first_total + 2 <= finished["processing_seconds"] <= first_total + 5


def test_historical_jobs_backfill_compute_device_names(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    monkeypatch.setattr(gpu_module, "gpu_snapshot", lambda *_: {
        "name": "Migration GPU", "memory_used_mib": 0, "memory_total_mib": 4096, "utilization": 0,
    })
    db_module.init_db()
    old_asr = db_module.create_job("asr", "old-asr", {"input_path": "local"})
    old_tts = db_module.create_job("tts", "old-tts", {"text": "local"})
    named = db_module.create_job("asr", "named", {
        "input_path": "local", "compute_device": "gpu", "compute_device_name": "Original GPU",
    })
    with sqlite3.connect(local.database_path) as database:
        database.execute("UPDATE schema_meta SET version=2")
    db_module.init_db()

    assert db_module.get_job(old_asr["id"])["request"]["compute_device_name"] == "Migration GPU"
    assert db_module.get_job(old_tts["id"])["request"]["compute_device_name"] == "CPU"
    assert db_module.get_job(named["id"])["request"]["compute_device_name"] == "Original GPU"
    with sqlite3.connect(local.database_path) as database:
        assert database.execute("SELECT version FROM schema_meta").fetchone()[0] == 3


def test_batch_delete_purges_files_queue_and_database(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    with TestClient(api_module.create_app()) as client:
        completed = db_module.create_job("tts", "completed", {"text": "done"})
        db_module.update_job(completed["id"], state="succeeded", stage="completed")
        queued = db_module.create_job("asr", "queued", {"input_path": "local"})
        running = db_module.create_job("tts", "running", {"text": "busy"})
        db_module.update_job(running["id"], state="running", stage="synthesizing", started_at=db_module.utcnow())
        for job in (completed, queued, running):
            create_payload(local, job["id"], b"x" * 8192)

        response = client.post("/api/v1/jobs/batch-delete", json={
            "job_ids": [completed["id"], queued["id"], running["id"], "missing", completed["id"]],
            "purge": True,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["requested_count"] == 4
        assert body["deleted_count"] == 2
        assert body["failed_count"] == 2
        assert body["reclaimed_bytes"] > 0
        assert body["database_compacted"] is True
        assert {item["code"] for item in body["failed"]} == {"running", "not_found"}
        for job in (completed, queued):
            assert db_module.get_job(job["id"]) is None
            assert not (local.jobs_dir / job["id"]).exists()
            assert not (local.temp_dir / job["id"]).exists()
        assert db_module.get_job(running["id"]) is not None
        assert (local.jobs_dir / running["id"]).is_dir()
        with sqlite3.connect(local.database_path) as database:
            assert database.execute("PRAGMA freelist_count").fetchone()[0] == 0


def test_batch_delete_keeps_failed_item_and_continues(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    with TestClient(api_module.create_app()) as client:
        blocked = db_module.create_job("tts", "blocked", {"text": "one"})
        removable = db_module.create_job("tts", "removable", {"text": "two"})
        for job in (blocked, removable):
            db_module.update_job(job["id"], state="succeeded", stage="completed")
            create_payload(local, job["id"])
        original = purge_module._remove_and_verify

        def fail_one(path):
            if blocked["id"] in str(path):
                raise PermissionError("simulated permission failure")
            original(path)

        monkeypatch.setattr(purge_module, "_remove_and_verify", fail_one)
        body = client.post("/api/v1/jobs/batch-delete", json={
            "job_ids": [blocked["id"], removable["id"]], "purge": True,
        }).json()
        assert body["deleted_count"] == 1
        assert body["failed_count"] == 1
        assert body["failed"][0]["code"] == "purge_failed"
        assert db_module.get_job(blocked["id"]) is not None
        assert (local.jobs_dir / blocked["id"]).exists()
        assert db_module.get_job(removable["id"]) is None


def test_batch_delete_validation(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    with TestClient(api_module.create_app()) as client:
        assert client.post("/api/v1/jobs/batch-delete", json={"job_ids": [], "purge": True}).status_code == 422
        job = db_module.create_job("tts", "protected", {"text": "test"})
        assert client.post("/api/v1/jobs/batch-delete", json={"job_ids": [job["id"]]}).status_code == 409
        assert db_module.get_job(job["id"]) is not None
        too_many = [f"job-{index}" for index in range(101)]
        assert client.post("/api/v1/jobs/batch-delete", json={"job_ids": too_many, "purge": True}).status_code == 422


def test_single_delete_uses_strict_purge_for_queued_job(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    with TestClient(api_module.create_app()) as client:
        job = db_module.create_job("tts", "single", {"text": "queued"})
        create_payload(local, job["id"])
        response = client.delete(f"/api/v1/jobs/{job['id']}?purge=true")
        assert response.status_code == 204
        assert db_module.get_job(job["id"]) is None
        assert not (local.jobs_dir / job["id"]).exists()
        assert not (local.temp_dir / job["id"]).exists()
