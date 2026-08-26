from __future__ import annotations

import sqlite3
from dataclasses import replace

import audio_intel.db as db_module
from audio_intel.config import settings
from audio_intel.observability import estimate_for_job, queue_context, stage_details


def test_eta_warms_up_then_uses_local_cohort(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp")
    monkeypatch.setattr(db_module, "settings", local)
    db_module.init_db()
    request = {
        "text": "0123456789", "compute_device": "cpu",
        "accelerate_single_task": True, "voice_mode": "preset",
    }
    current = db_module.create_job("tts", "current", request)
    assert estimate_for_job(current, queue_context())["state"] == "warming_up"

    for index, seconds in enumerate((10, 11, 12, 13, 14)):
        sample = db_module.create_job("tts", f"sample-{index}", request)
        with sqlite3.connect(local.database_path) as database:
            database.execute(
                """UPDATE jobs SET state='succeeded',stage='completed',stage_code='succeeded',
                   processing_seconds=?,finished_at=?,updated_at=? WHERE id=?""",
                (seconds, db_module.utcnow(), db_module.utcnow(), sample["id"]),
            )

    estimate = estimate_for_job(db_module.get_job(current["id"]), queue_context())
    assert estimate["state"] == "ready"
    assert estimate["sample_count"] == 5
    assert estimate["confidence"] == "low"
    assert estimate["remaining_seconds"] == {"lower": 10, "upper": 14}


def test_dynamic_stage_is_normalized_for_consumers() -> None:
    detail = stage_details({
        "stage": "synthesizing_3_of_8", "stage_code": None,
        "stage_current": None, "stage_total": None,
    })
    assert detail == {
        "stage_code": "synthesis", "stage_progress": 3 / 8,
        "current": 3, "total": 8, "unit": "batch",
    }
