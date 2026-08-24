from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import audio_intel.api as api_module
import audio_intel.db as db_module
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
        explicit = client.post("/api/v1/asr/jobs", files={"file": ("cpu.wav", b"RIFF-cpu", "audio/wav")}, data={"compute_device": "cpu"})
        assert explicit.status_code == 202
        assert explicit.json()["request"]["compute_device"] == "cpu"
        bad_device = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "tpu"})
        assert bad_device.status_code == 422
        capabilities = client.get("/api/v1/capabilities").json()
        assert next(item for item in capabilities["asr"]["compute_devices"] if item["id"] == "gpu")["default"] is True
        assert next(item for item in capabilities["tts"]["compute_devices"] if item["id"] == "cpu")["default"] is True


def test_gpu_request_fails_cleanly_when_unavailable(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"tts"}))
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: None)
    with TestClient(api_module.create_app()) as client:
        response = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "gpu"})
        assert response.status_code == 503
        assert "GPU compute is unavailable" in response.json()["detail"]
