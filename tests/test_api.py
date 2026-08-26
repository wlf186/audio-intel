from __future__ import annotations

from dataclasses import replace
import wave

from fastapi.testclient import TestClient

import audio_intel.api as api_module
import audio_intel.db as db_module
import asr.pipeline as asr_pipeline
from audio_intel.config import settings


def test_api_queues_asr_and_validates_tts(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"asr", "tts"}))
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: {"name": "Test GPU", "memory_used_mib": 0, "memory_total_mib": 4096, "utilization": 0})
    with TestClient(api_module.create_app()) as client:
        assert client.get("/openapi.json").json()["info"]["title"] == "Sandevistan-Audio"
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        response = client.post("/api/v1/asr/jobs", files={"file": ("sample.wav", b"RIFF-test", "audio/wav")}, data={"language": "Chinese", "speaker_count": "2"})
        assert response.status_code == 202
        assert response.json()["state"] == "queued"
        assert response.json()["request"]["compute_device"] == "gpu"
        assert response.json()["request"]["compute_device_name"] == "Test GPU"
        assert response.json()["request"]["accelerate_single_task"] is False
        assert response.json()["compute_device_name"] == "Test GPU"
        assert response.json()["source_url"].endswith("/source")
        source = client.get(response.json()["source_url"])
        assert source.status_code == 200
        assert source.content == b"RIFF-test"
        assert source.headers["content-type"].startswith("audio/")
        partial = client.get(response.json()["source_url"], headers={"Range": "bytes=0-3"})
        assert partial.status_code == 206
        assert partial.content == b"RIFF"
        download = client.get(response.json()["source_url"] + "?download=true")
        assert "attachment" in download.headers["content-disposition"]
        tts_job = db_module.create_job("tts", "speech", {"text": "test"})
        assert client.get(f"/api/v1/jobs/{tts_job['id']}/source").status_code == 409
        outside = tmp_path / "outside.wav"
        outside.write_bytes(b"private")
        unsafe = db_module.create_job("asr", "outside.wav", {"input_path": str(outside)})
        assert client.get(f"/api/v1/jobs/{unsafe['id']}/source").status_code == 404
        assert client.get("/api/v1/jobs/not-found/source").status_code == 404
        invalid = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "not-a-voice"})
        assert invalid.status_code == 422
        tts = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "Vivian"})
        assert tts.status_code == 202
        assert tts.json()["request"]["compute_device"] == "cpu"
        assert tts.json()["compute_device_name"] == "CPU"
        assert tts.json()["request"]["accelerate_single_task"] is False
        explicit = client.post("/api/v1/asr/jobs", files={"file": ("cpu.wav", b"RIFF-cpu", "audio/wav")}, data={"compute_device": "cpu"})
        assert explicit.status_code == 202
        assert explicit.json()["request"]["compute_device"] == "cpu"
        accelerated = client.post(
            "/api/v1/tts/jobs",
            data={
                "text": "test", "voice_mode": "preset", "speaker": "Vivian",
                "accelerate_single_task": "true",
            },
        )
        assert accelerated.status_code == 202
        assert accelerated.json()["request"]["accelerate_single_task"] is True
        bad_device = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "tpu"})
        assert bad_device.status_code == 422
        capabilities = client.get("/api/v1/capabilities").json()
        assert next(item for item in capabilities["asr"]["compute_devices"] if item["id"] == "gpu")["default"] is True
        assert next(item for item in capabilities["tts"]["compute_devices"] if item["id"] == "cpu")["default"] is True
        assert capabilities["asr"]["single_task_acceleration"] == {"supported": True, "default": False}
        assert capabilities["tts"]["single_task_acceleration"] == {"supported": True, "default": False}


def test_gpu_request_fails_cleanly_when_unavailable(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"tts"}))
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: None)
    with TestClient(api_module.create_app()) as client:
        response = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "gpu"})
        assert response.status_code == 503
        assert "GPU compute is unavailable" in response.json()["detail"]


def test_voiceprint_api_adds_asr_segments_and_tts_uses_selected_sample(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"asr", "tts"}))
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(asr_pipeline, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: None)
    with TestClient(api_module.create_app()) as client:
        person_response = client.post("/api/v1/voiceprints/people", json={"name": "尼克杨"})
        assert person_response.status_code == 201
        person_id = person_response.json()["id"]
        assert client.post("/api/v1/voiceprints/people", json={"name": "尼克杨"}).status_code == 409

        job_id = "asr-source"
        source = local.jobs_dir / job_id / "input" / "meeting.wav"
        source.parent.mkdir(parents=True)
        with wave.open(str(source), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(16000)
            handle.writeframes(b"\0\0" * 16000 * 3)
        job = db_module.create_job("asr", "meeting.wav", {
            "input_path": str(source), "export_formats": ["json", "txt"], "compute_device": "cpu",
        }, job_id)
        result = {
            "text": "你好。", "language": "Chinese", "duration": 3.0,
            "speakers": [{"id": "Speaker_0", "label": "Speaker 0", "label_source": "default"}],
            "segments": [{
                "id": 0, "start": .2, "end": 2.8, "speaker": "Speaker_0",
                "speaker_label": "Speaker 0", "text": "你好。",
                "words": [{"text": "你好", "start": .4, "end": 2.2}],
            }],
        }
        db_module.update_job(job["id"], state="succeeded", stage="completed", result_json=result)
        added = client.post(
            f"/api/v1/voiceprints/people/{person_id}/samples/from-asr",
            json={"job_id": job_id, "segment_ids": [0]},
        )
        assert added.status_code == 201
        sample = added.json()["items"][0]
        assert sample["transcript"] == "你好。"
        assert sample["duration"] == 2.6
        assert sample["words"] == [{"text": "你好", "start": .2, "end": 2.0}]
        assert client.get(sample["audio_url"]).status_code == 200
        assert client.post(
            f"/api/v1/voiceprints/people/{person_id}/samples/from-asr",
            json={"job_id": job_id, "segment_ids": [0]},
        ).status_code == 409

        renamed = client.patch(
            f"/api/v1/jobs/{job_id}/speakers/Speaker_0", json={"name": "尼克杨"},
        )
        assert renamed.status_code == 200
        assert renamed.json()["speakers"][0]["label_source"] == "manual"
        assert renamed.json()["segments"][0]["speaker_label"] == "尼克杨"

        tts = client.post("/api/v1/tts/jobs", data={
            "text": "测试克隆", "voice_mode": "voiceprint",
            "voiceprint_sample_id": sample["id"], "compute_device": "cpu",
        })
        assert tts.status_code == 202
        assert tts.json()["request"]["voiceprint_person_name"] == "尼克杨"
        assert tts.json()["request"]["voiceprint_sample_id"] == sample["id"]
        assert local.jobs_dir in __import__("pathlib").Path(tts.json()["request"]["reference_audio_path"]).parents
