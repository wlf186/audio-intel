from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
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


def test_queue_is_fifo_and_retry_moves_job_to_tail(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    db_module.init_db()
    first = db_module.create_job("asr", "first", {"input_path": "one"})
    second = db_module.create_job("asr", "second", {"input_path": "two"})
    assert db_module.claim_job("asr", "worker")["id"] == first["id"]
    db_module.finish_job(first["id"], "failed", stage="failed", progress=.2)
    retried = db_module.retry_job(first["id"])
    assert retried["queue_seq"] > second["queue_seq"]
    assert db_module.claim_job("asr", "worker")["id"] == second["id"]
    db_module.request_cancel(retried["id"])
    assert db_module.delete_job_record(retried["id"])
    later = db_module.create_job("asr", "later", {"input_path": "three"})
    assert later["queue_seq"] > retried["queue_seq"]


def test_concurrent_idempotent_creates_return_one_job(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    db_module.init_db()

    def create(index: int):
        return db_module.create_job_idempotent(
            "tts", "same", {"text": "same"}, f"job-{index}",
            "submit_tts", "key-hash", "request-hash",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(not replayed for _, replayed in results) == 1
    assert len(db_module.list_jobs()) == 1


def test_job_history_page_is_stable_counted_and_searches_literals(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    db_module.init_db()
    jobs = [
        db_module.create_job("asr", "普通任务", {"input_path": "one"}, "job-a"),
        db_module.create_job("tts", "包含 100% 字样", {"text": "two"}, "job-b"),
        db_module.create_job("tts", "包含下划线_字样", {"text": "three"}, "job-c"),
    ]
    with sqlite3.connect(local.database_path) as database:
        database.execute(
            "UPDATE jobs SET created_at='2026-08-27T12:00:00+00:00'",
        )

    first_page, total = db_module.list_jobs_page(limit=2)
    second_page, repeated_total = db_module.list_jobs_page(limit=2, offset=2)
    assert [job["id"] for job in first_page + second_page] == ["job-c", "job-b", "job-a"]
    assert total == repeated_total == 3

    percent, percent_total = db_module.list_jobs_page(query="%")
    underscore, underscore_total = db_module.list_jobs_page(query="_")
    filtered, filtered_total = db_module.list_jobs_page(kind="tts", state="queued", query="包含")
    assert [job["id"] for job in percent] == [jobs[1]["id"]] and percent_total == 1
    assert [job["id"] for job in underscore] == [jobs[2]["id"]] and underscore_total == 1
    assert [job["id"] for job in filtered] == ["job-c", "job-b"] and filtered_total == 2


def test_stage_progress_persists_stable_codes_and_timings(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    db_module.init_db()
    job = db_module.create_job("tts", "progress", {"text": "one two"})
    db_module.claim_job("tts", "worker")
    updated = db_module.update_job_progress(
        job["id"], .4, "synthesizing_1_of_2", "synthesis", 0, 2,
        stage_progress=.35, stage_unit="text_chunk", progress_basis="estimated",
        activity={
            "sequence": 1, "current": 9, "total": 20,
            "unit": "codec_frame", "basis": "estimated",
        },
    )
    assert updated["stage_code"] == "synthesis"
    assert updated["stage_current"] == 0 and updated["stage_total"] == 2
    assert updated["stage_progress"] == .35
    assert updated["stage_unit"] == "text_chunk"
    assert updated["progress_basis"] == "estimated"
    assert updated["progress_activity"]["current"] == 9
    assert updated["progress_activity"]["updated_at"]
    monotonic = db_module.update_job_progress(
        job["id"], .3, "synthesizing_1_of_2", "synthesis", 0, 2,
        stage_progress=.2, stage_unit="text_chunk", progress_basis="estimated",
    )
    assert monotonic["progress"] == .4
    assert monotonic["stage_progress"] == .35
    db_module.update_job_progress(job["id"], .9, "writing_audio", "writing_output")
    db_module.finish_job(job["id"], "succeeded", stage="completed", progress=1)
    with sqlite3.connect(local.database_path) as database:
        rows = database.execute(
            "SELECT stage_code,finished_at FROM job_stage_timings WHERE job_id=? ORDER BY sequence",
            (job["id"],),
        ).fetchall()
    assert [row[0] for row in rows] == ["synthesis", "writing_output"]
    assert all(row[1] is not None for row in rows)

    with sqlite3.connect(local.database_path) as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(jobs)")}
    assert {"progress_basis", "stage_progress", "stage_unit", "progress_activity_json"} <= columns


def test_cancel_request_is_immediate_and_idempotent(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    db_module.init_db()
    queued = db_module.create_job("asr", "queued", {"input_path": "local"})
    running = db_module.create_job("tts", "running", {"text": "busy"})
    db_module.claim_job("tts", "worker-one")

    assert db_module.request_cancel(queued["id"])["state"] == "cancelled"
    cancelling = db_module.request_cancel(running["id"])
    assert cancelling["state"] == "running"
    assert cancelling["stage"] == "cancelling"
    assert cancelling["cancel_requested"] is True
    repeated = db_module.request_cancel(running["id"])
    assert repeated["state"] == "running"
    assert repeated["stage"] == "cancelling"


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
        assert database.execute("SELECT version FROM schema_meta").fetchone()[0] == 7


def test_schema_upgrade_reaches_voiceprints_without_a_gpu(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    monkeypatch.setattr(gpu_module, "gpu_snapshot", lambda *_: None)
    db_module.init_db()
    with sqlite3.connect(local.database_path) as database:
        database.execute("UPDATE schema_meta SET version=2")

    db_module.init_db()
    db_module.init_db()

    with sqlite3.connect(local.database_path) as database:
        assert database.execute("SELECT version FROM schema_meta").fetchone()[0] == 7
        assert database.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='voiceprint_people'"
        ).fetchone()[0] == 1


def test_voiceprint_people_samples_and_import_job_purge_are_independent(tmp_path, monkeypatch) -> None:
    local = local_settings(tmp_path)
    install_settings(local, monkeypatch)
    db_module.init_db()
    person = db_module.create_voiceprint_person(" 尼克  杨 ")
    assert db_module.find_voiceprint_person("尼克 杨")["id"] == person["id"]
    with __import__("pytest").raises(sqlite3.IntegrityError):
        db_module.create_voiceprint_person("尼克 杨")

    successful_job = db_module.create_job("asr", "声纹样本入库", {"purpose": "voiceprint_import"})
    sample_path = local.voiceprints_dir / person["id"] / "ready.wav"
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_bytes(b"RIFF-ready")
    ready = db_module.create_voiceprint_sample(
        person["id"], state="ready", audio_path=str(sample_path), transcript="测试",
        source_job_id=successful_job["id"],
    )
    db_module.update_job(successful_job["id"], state="succeeded", stage="completed")
    create_payload(local, successful_job["id"])
    assert purge_module.purge_jobs([successful_job["id"]])["deleted_count"] == 1
    assert db_module.get_voiceprint_sample(ready["id"])["audio_path"] == str(sample_path)
    assert sample_path.is_file()

    pending_job = db_module.create_job("asr", "声纹样本入库", {
        "purpose": "voiceprint_import", "voiceprint_sample_id": "sample_pending",
    })
    db_module.create_voiceprint_sample(
        person["id"], sample_id="sample_pending", state="pending", source_job_id=pending_job["id"],
    )
    create_payload(local, pending_job["id"])
    assert purge_module.purge_jobs([pending_job["id"]])["deleted_count"] == 1
    assert db_module.get_voiceprint_sample("sample_pending") is None


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
