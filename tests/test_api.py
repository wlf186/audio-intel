from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import uuid
import wave

from fastapi.testclient import TestClient

import audio_intel.api as api_module
import audio_intel.db as db_module
import asr.pipeline as asr_pipeline
from audio_intel.config import settings


def idem() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


def test_native_submissions_require_and_enforce_idempotency(tmp_path, monkeypatch) -> None:
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        enabled_services=frozenset({"asr"}), min_free_disk_bytes=0,
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    key = str(uuid.uuid4())
    request = {
        "files": {"file": ("sample.wav", b"RIFF-same", "audio/wav")},
        "data": {"compute_device": "cpu"},
    }
    with TestClient(api_module.create_app()) as client:
        missing = client.post("/api/v1/asr/jobs", **request)
        invalid = client.post("/api/v1/asr/jobs", headers={"Idempotency-Key": "short"}, **request)
        created = client.post("/api/v1/asr/jobs", headers={"Idempotency-Key": key}, **request)
        replay = client.post("/api/v1/asr/jobs", headers={"Idempotency-Key": key}, **request)
        conflict = client.post(
            "/api/v1/asr/jobs", headers={"Idempotency-Key": key},
            files={"file": ("sample.wav", b"RIFF-different", "audio/wav")},
            data={"compute_device": "cpu"},
        )
        jobs = client.get("/api/v1/jobs").json()["items"]

    assert missing.status_code == 400
    assert missing.json()["code"] == "idempotency_key_required"
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "invalid_idempotency_key"
    assert created.status_code == 202
    assert replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["id"] == created.json()["id"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_key_conflict"
    assert [job["id"] for job in jobs] == [created.json()["id"]]
    assert len(list(local.jobs_dir.glob("*/input/sample.wav"))) == 1


def test_queue_admission_etag_and_position(tmp_path, monkeypatch) -> None:
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        enabled_services=frozenset({"asr"}), max_queued_asr=1,
        max_concurrent_submissions=2, min_free_disk_bytes=0,
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    with TestClient(api_module.create_app()) as client:
        created = client.post(
            "/api/v1/asr/jobs", headers=idem(),
            files={"file": ("one.wav", b"RIFF-one", "audio/wav")},
            data={"compute_device": "cpu"},
        )
        rejected = client.post(
            "/api/v1/asr/jobs", headers=idem(),
            files={"file": ("two.wav", b"RIFF-two", "audio/wav")},
            data={"compute_device": "cpu"},
        )
        queue = client.get("/api/v1/queue")
        status = client.get(created.json()["status_url"])
        unchanged = client.get(
            created.json()["status_url"], headers={"If-None-Match": status.headers["etag"]},
        )

    assert created.status_code == 202
    assert created.json()["queue"]["position"] == 1
    assert created.json()["queue"]["capacity"] == 1
    assert created.json()["estimate"]["state"] == "warming_up"
    assert rejected.status_code == 429
    assert rejected.headers["retry-after"] == "30"
    assert rejected.json()["code"] == "queue_capacity_reached"
    asr_queue = next(item for item in queue.json()["items"] if item["kind"] == "asr")
    assert asr_queue == {
        "kind": "asr", "queued": 1, "running": 0, "reserved": 0,
        "capacity": 1, "accepting": False, "retry_after_seconds": 30,
    }
    assert status.status_code == 200 and status.headers["etag"]
    assert unchanged.status_code == 304 and unchanged.content == b""


def test_terminal_per_job_sse_emits_snapshot_and_closes(tmp_path, monkeypatch) -> None:
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        enabled_services=frozenset({"tts"}), min_free_disk_bytes=0,
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    with TestClient(api_module.create_app()) as client:
        job = db_module.create_job("tts", "finished", {"text": "done"})
        db_module.finish_job(job["id"], "succeeded", stage="completed", progress=1)
        response = client.get(f"/api/v1/jobs/{job['id']}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: job" in response.text
    assert '"state": "succeeded"' in response.text


def test_api_queues_asr_and_validates_tts(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"asr", "tts"}))
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: {"name": "Test GPU", "memory_used_mib": 0, "memory_total_mib": 4096, "utilization": 0})
    with TestClient(api_module.create_app()) as client:
        assert client.get("/openapi.json").json()["info"]["title"] == "Sandevistan-Audio"
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        response = client.post("/api/v1/asr/jobs", headers=idem(), files={"file": ("sample.wav", b"RIFF-test", "audio/wav")}, data={"language": "Chinese", "speaker_count": "2"})
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
        invalid = client.post("/api/v1/tts/jobs", headers=idem(), data={"text": "test", "voice_mode": "preset", "speaker": "not-a-voice"})
        assert invalid.status_code == 422
        tts = client.post("/api/v1/tts/jobs", headers=idem(), data={"text": "test", "voice_mode": "preset", "speaker": "Vivian"})
        assert tts.status_code == 202
        assert tts.json()["request"]["compute_device"] == "gpu"
        assert tts.json()["request"]["language"] == "Auto"
        assert tts.json()["compute_device_name"] == "Test GPU"
        assert tts.json()["request"]["accelerate_single_task"] is True
        explicit = client.post("/api/v1/asr/jobs", headers=idem(), files={"file": ("cpu.wav", b"RIFF-cpu", "audio/wav")}, data={"compute_device": "cpu"})
        assert explicit.status_code == 202
        assert explicit.json()["request"]["compute_device"] == "cpu"
        unaccelerated = client.post(
            "/api/v1/tts/jobs",
            headers=idem(),
            data={
                "text": "test", "voice_mode": "preset", "speaker": "Vivian",
                "accelerate_single_task": "false",
            },
        )
        assert unaccelerated.status_code == 202
        assert unaccelerated.json()["request"]["accelerate_single_task"] is False
        bad_device = client.post("/api/v1/tts/jobs", headers=idem(), data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "tpu"})
        assert bad_device.status_code == 422
        capabilities = client.get("/api/v1/capabilities").json()
        assert next(item for item in capabilities["asr"]["compute_devices"] if item["id"] == "gpu")["default"] is True
        assert next(item for item in capabilities["tts"]["compute_devices"] if item["id"] == "gpu")["default"] is True
        assert capabilities["asr"]["languages"] == api_module.ASR_LANGUAGES
        assert capabilities["asr"]["default_language"] == "Auto"
        assert capabilities["asr"]["aligner_languages"] == api_module.ALIGNER_LANGUAGES
        assert capabilities["tts"]["default_language"] == "Auto"
        assert capabilities["tts"]["languages"] == api_module.TTS_LANGUAGES
        assert capabilities["tts"]["preset_speaker_native_languages"]["Ryan"] == "English"
        assert capabilities["asr"]["single_task_acceleration"] == {"supported": True, "default": True}
        assert capabilities["tts"]["single_task_acceleration"] == {"supported": True, "default": True}


def test_asr_public_languages_are_normalized_and_rejected_before_job_creation(tmp_path, monkeypatch) -> None:
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        enabled_services=frozenset({"asr"}),
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    with TestClient(api_module.create_app()) as client:
        for expected in api_module.ASR_LANGUAGES:
            response = client.post(
                "/api/v1/asr/jobs",
                headers=idem(),
                files={"file": (f"{expected}.wav", b"RIFF-test", "audio/wav")},
                data={"language": expected.swapcase(), "compute_device": "cpu"},
            )
            assert response.status_code == 202
            assert response.json()["request"]["language"] == expected

        person = client.post("/api/v1/voiceprints/people", json={"name": "语言校验"}).json()
        existing_jobs = {item["id"] for item in client.get("/api/v1/jobs").json()["items"]}
        native = client.post(
            "/api/v1/asr/jobs",
            headers=idem(),
            files={"file": ("arabic.wav", b"RIFF-invalid", "audio/wav")},
            data={"language": "Arabic", "compute_device": "cpu"},
        )
        typo = client.post(
            "/api/v1/asr/jobs",
            headers=idem(),
            files={"file": ("typo.wav", b"RIFF-invalid", "audio/wav")},
            data={"language": "Spanisch", "compute_device": "cpu"},
        )
        voiceprint = client.post(
            f"/api/v1/voiceprints/people/{person['id']}/samples/upload",
            headers=idem(),
            files={"file": ("arabic.wav", b"RIFF-invalid", "audio/wav")},
            data={"language": "Arabic", "compute_device": "cpu"},
        )
        compatible = client.post(
            "/v1/audio/transcriptions",
            files={"file": ("arabic.wav", b"RIFF-invalid", "audio/wav")},
            data={"language": "Arabic", "compute_device": "cpu"},
        )

        rejected = (native, typo, voiceprint, compatible)
        assert [response.status_code for response in rejected] == [422] * len(rejected)
        assert all("language must be one of" in response.json()["detail"] for response in rejected)
        assert {item["id"] for item in client.get("/api/v1/jobs").json()["items"]} == existing_jobs
        assert not list(local.jobs_dir.glob("*/input/arabic.wav"))


def test_gpu_request_fails_cleanly_when_unavailable(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", enabled_services=frozenset({"tts"}))
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(api_module, "gpu_snapshot", lambda *_: None)
    with TestClient(api_module.create_app()) as client:
        response = client.post("/api/v1/tts/jobs", headers=idem(), data={"text": "test", "voice_mode": "preset", "speaker": "Vivian", "compute_device": "gpu"})
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
            headers=idem(),
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
        assert payload["job"]["request"]["accelerate_single_task"] is True
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
    assert [request["compute_device"] for _, request in captured] == ["gpu", "gpu", "gpu", "gpu"]
    assert captured[2][1]["language"] == "Auto"


def test_tts_clone_reference_analysis_and_snapshot_submission(tmp_path, monkeypatch) -> None:
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        enabled_services=frozenset({"asr", "tts"}),
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    monkeypatch.setattr(
        api_module, "gpu_snapshot",
        lambda *_: {"name": "Test GPU", "memory_used_mib": 0, "memory_total_mib": 4096, "utilization": 0},
    )
    with TestClient(api_module.create_app()) as client:
        analyzed = client.post(
            "/api/v1/tts/clone-references",
            headers=idem(),
            files={"file": ("reference.webm", b"browser-audio", "audio/webm")},
        )
        assert analyzed.status_code == 202
        analysis = analyzed.json()
        assert analysis["kind"] == "asr"
        assert analysis["display_name"] == "TTS 克隆参考分析 · reference.webm"
        assert analysis["request"] == {
            "purpose": "tts_clone_reference",
            "input_path": analysis["request"]["input_path"],
            "original_name": "reference.webm", "size_bytes": 13, "language": "Auto",
            "speaker_count": 1, "diarize": False, "align": True, "context": "",
            "export_formats": ["json", "txt"], "compute_device": "gpu",
            "compute_device_name": "Test GPU", "use_voiceprint_library": False,
            "accelerate_single_task": True,
        }
        reference = local.jobs_dir / analysis["id"] / "output" / "reference.wav"
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_bytes(b"RIFF-normalized")
        result = {
            "text": "自动识别文本。", "language": "Chinese", "duration": 6.0,
            "segments": [{
                "id": 0, "start": 0.0, "end": 6.0, "speaker": "Speaker_0",
                "speaker_label": "Speaker 0", "text": "自动识别文本。",
                "words": [{"text": "自动识别文本", "start": .2, "end": 5.5}],
            }],
            "artifacts": [{
                "name": "reference.wav", "path": str(reference),
                "mime_type": "audio/wav", "size_bytes": reference.stat().st_size,
            }],
        }
        db_module.update_job(analysis["id"], state="succeeded", stage="completed", progress=1, result_json=result)

        submitted = client.post("/api/v1/tts/jobs", headers=idem(), data={
            "text": "English output", "language": "English", "voice_mode": "inline_clone",
            "reference_job_id": analysis["id"], "reference_text": "修正后的文本。",
            "reference_language": "Chinese", "compute_device": "cpu",
        })
        assert submitted.status_code == 202
        request = submitted.json()["request"]
        assert request["reference_job_id"] == analysis["id"]
        assert request["reference_text"] == "修正后的文本。"
        assert request["reference_language"] == "Chinese"
        assert request["reference_words"] == []
        copied = Path(request["reference_audio_path"])
        assert copied.read_bytes() == b"RIFF-normalized"
        assert client.delete(f"/api/v1/jobs/{analysis['id']}?purge=true").status_code == 204
        assert copied.is_file()

        missing = client.post("/api/v1/tts/jobs", headers=idem(), data={
            "text": "test", "voice_mode": "inline_clone", "reference_job_id": "missing",
            "compute_device": "cpu",
        })
        assert missing.status_code == 422
        invalid_language = client.post("/api/v1/tts/jobs", headers=idem(), data={
            "text": "test", "voice_mode": "preset", "speaker": "Vivian",
            "language": "Klingon", "compute_device": "cpu",
        })
        assert invalid_language.status_code == 422


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

        tts = client.post("/api/v1/tts/jobs", headers=idem(), data={
            "text": "测试克隆", "voice_mode": "voiceprint",
            "voiceprint_sample_id": sample["id"], "compute_device": "cpu",
        })
        assert tts.status_code == 202
        assert tts.json()["request"]["voiceprint_person_name"] == "尼克杨"
        assert tts.json()["request"]["voiceprint_sample_id"] == sample["id"]
        assert local.jobs_dir in __import__("pathlib").Path(tts.json()["request"]["reference_audio_path"]).parents
