from __future__ import annotations

from dataclasses import replace
from pathlib import Path
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
        assert response.json()["request"]["accelerate_single_task"] is True
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
        assert tts.json()["request"]["accelerate_single_task"] is True
        explicit = client.post("/api/v1/asr/jobs", files={"file": ("cpu.wav", b"RIFF-cpu", "audio/wav")}, data={"compute_device": "cpu"})
        assert explicit.status_code == 202
        assert explicit.json()["request"]["compute_device"] == "cpu"
        unaccelerated = client.post(
            "/api/v1/tts/jobs",
            data={
                "text": "test", "voice_mode": "preset", "speaker": "Vivian",
                "accelerate_single_task": "false",
            },
        )
        assert unaccelerated.status_code == 202
        assert unaccelerated.json()["request"]["accelerate_single_task"] is False
        bad_device = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "tpu"})
        assert bad_device.status_code == 422
        capabilities = client.get("/api/v1/capabilities").json()
        assert next(item for item in capabilities["asr"]["compute_devices"] if item["id"] == "gpu")["default"] is True
        assert next(item for item in capabilities["tts"]["compute_devices"] if item["id"] == "cpu")["default"] is True
        assert capabilities["asr"]["single_task_acceleration"] == {"supported": True, "default": True}
        assert capabilities["tts"]["single_task_acceleration"] == {"supported": True, "default": True}


def test_gpu_request_fails_cleanly_when_unavailable(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"tts"}))
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: None)
    with TestClient(api_module.create_app()) as client:
        response = client.post("/api/v1/tts/jobs", data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "gpu"})
        assert response.status_code == 503
        assert "GPU compute is unavailable" in response.json()["detail"]


def test_voiceprint_upload_accepts_browser_recording_container(tmp_path, monkeypatch) -> None:
    local = replace(
        settings,
        data_dir=tmp_path / "data",
        temp_dir=tmp_path / "tmp",
        enabled_services=frozenset({"asr"}),
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: None)
    with TestClient(api_module.create_app()) as client:
        person = client.post("/api/v1/voiceprints/people", json={"name": "浏览器录音"}).json()
        response = client.post(
            f"/api/v1/voiceprints/people/{person['id']}/samples/upload",
            files={
                "file": (
                    "voiceprint-recording-20260826120000.webm",
                    b"browser-webm-opus",
                    "audio/webm;codecs=opus",
                ),
            },
            data={"language": "Chinese", "compute_device": "cpu"},
        )

        assert response.status_code == 202
        payload = response.json()
        assert payload["sample"]["state"] == "pending"
        assert payload["job"]["request"]["purpose"] == "voiceprint_import"
        assert payload["job"]["request"]["language"] == "Chinese"
        assert payload["job"]["request"]["compute_device"] == "cpu"
        assert payload["job"]["request"]["original_name"].endswith(".webm")
        input_path = Path(payload["job"]["request"]["input_path"])
        assert input_path.read_bytes() == b"browser-webm-opus"


def test_openai_compatible_acceleration_defaults_and_explicit_opt_out(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"asr", "tts"}))
    local.ensure_directories()
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: {"name": "Test GPU", "memory_used_mib": 0, "memory_total_mib": 4096, "utilization": 0})
    captured: list[tuple[str, dict[str, object]]] = []
    artifact = tmp_path / "speech.wav"
    artifact.write_bytes(b"RIFF-test")

    def fake_create_job(kind: str, _: str, request: dict[str, object], job_id: str | None = None) -> dict[str, str]:
        captured.append((kind, request))
        return {"id": job_id or f"{kind}-job"}

    async def fake_wait_for_job(_: str, timeout: float = 24 * 3600) -> dict[str, object]:
        return {
            "state": "succeeded",
            "result": {
                "text": "transcribed",
                "artifacts": [{"path": str(artifact), "mime_type": "audio/wav"}],
            },
        }

    monkeypatch.setattr(api_module, "create_job", fake_create_job)
    monkeypatch.setattr(api_module, "wait_for_job", fake_wait_for_job)
    with TestClient(api_module.create_app()) as client:
        omitted_asr = client.post("/v1/audio/transcriptions", files={"file": ("sample.wav", b"RIFF-test", "audio/wav")})
        disabled_asr = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("sample.wav", b"RIFF-test", "audio/wav")},
            data={"accelerate_single_task": "false"},
        )
        omitted_tts = client.post("/v1/audio/speech", json={"input": "test", "voice": "Vivian"})
        disabled_tts = client.post(
            "/v1/audio/speech",
            json={"input": "test", "voice": "Vivian", "accelerate_single_task": False},
        )

    assert [response.status_code for response in (omitted_asr, disabled_asr, omitted_tts, disabled_tts)] == [200, 200, 200, 200]
    assert omitted_asr.headers["x-job-id"]
    assert omitted_tts.headers["x-job-id"]
    assert [request["accelerate_single_task"] for _, request in captured] == [True, False, True, False]


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
