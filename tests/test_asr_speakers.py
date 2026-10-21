from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from audio_intel import api as api_module, asr_speakers, db
from audio_intel.config import settings
from asr import pipeline


@pytest.fixture
def local(tmp_path, monkeypatch):
    value = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
                    api_key="", mock_mode=True, min_free_disk_bytes=0,
                    deployment_profile="cpu", enabled_services=frozenset({"asr"}))
    for module in (api_module, db, pipeline):
        monkeypatch.setattr(module, "settings", value)
    db.init_db()
    return value


def source_job(local, speakers=None):
    speakers = speakers or [
        {"id": "Speaker_0", "label": "许榕俊", "label_source": "manual"},
        {"id": "Speaker_2", "label": "Speaker 2", "label_source": "default"},
    ]
    job = db.create_job("asr", "meeting.wav", {"export_formats": ["json", "txt", "srt", "vtt"]})
    source = local.jobs_dir / job["id"] / "input" / "meeting.wav"
    source.parent.mkdir(parents=True)
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\0\0" * 16000 * len(speakers))
    with db.connect() as connection:
        request = {**job["request"], "input_path": str(source), "compute_device": "cpu"}
        connection.execute("UPDATE jobs SET request_json=? WHERE id=?", (json.dumps(request), job["id"]))
    result = {
        "text": "你好。" * len(speakers), "duration": len(speakers), "language": "Chinese",
        "speakers": speakers,
        "segments": [
            {"id": index, "start": float(index), "end": index + .8,
             "speaker": speaker["id"], "speaker_label": speaker["label"], "text": "你好。",
             "words": [{"text": "你好", "start": index + .1, "end": index + .7, "speaker": speaker["id"]}]}
            for index, speaker in enumerate(speakers)
        ],
        "hotword_context": {"enabled": False, "list_ids": [], "list_names": [], "term_count": 0},
    }
    result = pipeline.write_asr_exports(job["id"], result, job["request"]["export_formats"])
    db.update_job(job["id"], state="succeeded", stage="completed", progress=1, result_json=result)
    return db.get_job(job["id"])


@pytest.mark.parametrize('grouped', [True, False])
def test_new_enrollment_person_keeps_selected_group_and_source_history(local, grouped):
    from audio_intel.voiceprint_groups import UNGROUPED_ID
    job = source_job(local)
    before = deepcopy(job)
    with TestClient(api_module.create_app()) as client:
        group_id = client.post('/api/v1/voiceprints/groups', json={'name': '工作组'}).json()['id']
        body = {'name': '新声纹人员', **({'group_id': group_id} if grouped else {})}
        created = client.post('/api/v1/voiceprints/people', json=body)
        assert created.status_code == 201, created.text
        person = created.json()
        expected = group_id if grouped else UNGROUPED_ID
        assert person['group_id'] == expected
        enrolled = client.post(f"/api/v1/voiceprints/people/{person['id']}/samples/from-asr", json={'job_id': job['id'], 'segment_ids': [0]})
        assert enrolled.status_code == 201, enrolled.text
        assert all(sample['person_id'] == person['id'] for sample in enrolled.json()['items'])
        assert db.get_voiceprint_person(person['id'])['group_id'] == expected
        groups = client.get('/api/v1/voiceprints/groups').json()['items']
        assert next(group for group in groups if group['id'] == expected)['person_count'] == 1
        assert client.post('/api/v1/voiceprints/people', json={'name': '不可创建', 'group_id': 'missing'}).status_code == 422
    assert db.get_job(job['id']) == before


def export_bytes(local, job):
    output = local.jobs_dir / job["id"] / "output"
    return {item["name"]: (output / item["name"]).read_bytes() for item in job["result"]["artifacts"]}


def test_merge_normalized_identities_preserves_segments_words_and_input():
    result = {
        "speakers": [
            {"id": "Speaker_0", "name": "Ａlice  Li", "note": "Ｔeam A", "label": "original", "label_source": "voiceprint", "voiceprint_match": {"person_id": "p", "name": "Alice Li", "score": .8}},
            {"id": "Speaker_2", "name": "alice li", "note": " team  a ", "label": "changed", "label_source": "manual"},
            {"id": "Speaker_3", "name": "Alice Li", "note": "Team B", "label": "other", "label_source": "manual"},
        ],
        "segments": [
            {"id": 9, "speaker": "Speaker_2", "speaker_label": "changed", "text": "保留。", "start": 3.5, "end": 4.0,
             "words": [{"text": "保留", "start": 3.6, "end": 3.9, "speaker": "Speaker_2"}]},
        ],
    }
    before = deepcopy(result)
    merged, mapping = asr_speakers.merge_speakers(result, preferred_id="Speaker_2")
    assert result == before
    assert mapping == {"Speaker_0": "Speaker_0", "Speaker_2": "Speaker_0", "Speaker_3": "Speaker_3"}
    assert [item["id"] for item in merged["speakers"]] == ["Speaker_0", "Speaker_3"]
    assert merged["speakers"][0]["label"] == "alice li（team a）"
    assert merged["speakers"][0]["label_source"] == "manual"
    assert merged["speakers"][0]["voiceprint_match"] == before["speakers"][0]["voiceprint_match"]
    expected = deepcopy(before["segments"][0])
    expected.update(speaker="Speaker_0", speaker_label="alice li（team a）")
    expected["words"][0]["speaker"] = "Speaker_0"
    assert merged["segments"] == [expected]
    assert asr_speakers.merge_speakers(merged)[0] == merged


def test_manual_snapshots_ignore_stale_matches_and_labels_are_not_identity_keys():
    result = {"speakers": [
        {"id": "Speaker_0", "label": "张三（研发）", "label_source": "manual", "voiceprint_match": {"name": "旧名", "note": "旧备注"}},
        {"id": "Speaker_1", "label": "张三（研发）", "label_source": "voiceprint", "voiceprint_match": {"name": "张三", "note": "研发"}},
        {"id": "Speaker_2", "label": "Speaker 2", "label_source": "default"},
        {"id": "Speaker_3", "label": "Speaker 2", "label_source": "manual"},
    ], "segments": []}
    merged, _ = asr_speakers.merge_speakers(result)
    assert len(merged["speakers"]) == 4
    assert asr_speakers.speaker_identity(merged["speakers"][0]) == ("张三（研发）", None)
    assert asr_speakers.speaker_identity(merged["speakers"][1]) == ("张三", "研发")


@pytest.mark.parametrize("empty_note", [None, "", "   "])
def test_empty_notes_merge_with_missing_historical_notes(empty_note):
    result = {"speakers": [
        {"id": "Speaker_0", "label": "许榕俊", "label_source": "manual"},
        {"id": "Speaker_2", "label": "许榕俊", "note": empty_note, "label_source": "manual"},
    ], "segments": []}
    merged, mapping = asr_speakers.merge_speakers(result)
    assert len(merged["speakers"]) == 1
    assert mapping["Speaker_2"] == "Speaker_0"


def test_patch_merges_persisted_results_exports_and_allows_combined_enrollment(local):
    job = source_job(local)
    original = deepcopy(job)
    person = db.create_voiceprint_person("许榕俊")
    with TestClient(api_module.create_app()) as client:
        response = client.patch(f"/api/v1/jobs/{job['id']}/speakers/Speaker_2", json={"name": "许榕俊", "note": None})
        assert response.status_code == 200
        result = response.json()
        assert result["speakers"] == [{"id": "Speaker_0", "name": "许榕俊", "note": None, "label": "许榕俊", "label_source": "manual"}]
        for before, after in zip(original["result"]["segments"], result["segments"]):
            assert {k: before[k] for k in ("id", "start", "end", "text")} == {k: after[k] for k in ("id", "start", "end", "text")}
            assert after["speaker"] == after["words"][0]["speaker"] == "Speaker_0"
        assert result["hotword_context"] == original["result"]["hotword_context"]
        assert client.get(f"/api/v1/jobs/{job['id']}/result").json() == result
        assert db.get_job(job["id"])["result"] == result
        assert db.get_job(job["id"])["request"] == original["request"]
        assert client.patch(f"/api/v1/jobs/{job['id']}/speakers/Speaker_2", json={"name": "许榕俊"}).status_code == 404
        enrolled = client.post(f"/api/v1/voiceprints/people/{person['id']}/samples/from-asr", json={"job_id": job["id"], "segment_ids": [1, 0]})
        assert enrolled.status_code == 201
        assert [item["source_segment_id"] for item in enrolled.json()["items"]] == [1, 0]
        assert all(item["source_speaker_id"] == "Speaker_0" for item in enrolled.json()["items"])
        assert client.post(f"/api/v1/voiceprints/people/{person['id']}/samples/from-asr", json={"job_id": job["id"], "segment_ids": [1]}).status_code == 409
    output = local.jobs_dir / job["id"] / "output"
    exported = json.loads((output / "transcript.json").read_text(encoding="utf-8"))
    assert exported == {key: value for key, value in result.items() if key != "artifacts"}
    for artifact in result["artifacts"]:
        path = output / artifact["name"]
        assert artifact["path"] == str(path)
        assert artifact["size_bytes"] == path.stat().st_size
        assert "许榕俊" in path.read_text(encoding="utf-8")


def test_note_omission_preserves_snapshot_explicit_clear_and_different_notes(local):
    match = {"person_id": "person-old", "name": "原名", "note": "研发&平台", "score": .8}
    job = source_job(local, [
        {"id": "Speaker_0", "label": "原名（研发&平台）", "label_source": "voiceprint", "voiceprint_match": match},
        {"id": "Speaker_2", "label": "新名（销售部）", "name": "新名", "note": "销售部", "label_source": "manual"},
    ])
    with TestClient(api_module.create_app()) as client:
        url = f"/api/v1/jobs/{job['id']}/speakers/Speaker_0"
        response = client.patch(url, json={"name": "新名"})
        assert response.status_code == 200
        result = response.json()
        assert len(result["speakers"]) == 2
        assert result["speakers"][0]["note"] == "研发&平台"
        assert result["speakers"][0]["voiceprint_match"] == match
        output = local.jobs_dir / job["id"] / "output"
        assert "<v 新名（研发&amp;平台）>" in (output / "transcript.vtt").read_text(encoding="utf-8")
        for note in (None, "", "   "):
            response = client.patch(url, json={"name": "新名", "note": note})
            assert response.json()["speakers"][0]["note"] is None
            assert response.json()["speakers"][0]["label"] == "新名"
        response = client.patch(url, json={"name": "新名", "note": " Ｓales   A "})
        assert response.json()["speakers"][0]["note"] == "Sales A"
        saved = db.get_job(job["id"])
        for payload in ({"name": "新名", "note": "备" * 21}, {"name": "新名", "note": 12}, {"name": "   "}, {"note": "备注"}):
            assert client.patch(url, json=payload).status_code == 422
        assert db.get_job(job["id"]) == saved
        assert client.patch(url, json={"name": "新名", "note": "备" * 20}).status_code == 200


def test_merge_keeps_existing_library_sample_source_snapshots(local):
    job = source_job(local)
    person = db.create_voiceprint_person("库内名字", note="库内备注")
    sample = db.create_voiceprint_sample(person["id"], name="已入库", state="ready",
                                       source_job_id=job["id"], source_segment_id=1, source_speaker_id="Speaker_2")
    with TestClient(api_module.create_app()) as client:
        response = client.patch(f"/api/v1/jobs/{job['id']}/speakers/Speaker_2", json={"name": "许榕俊"})
        assert response.status_code == 200
        assert client.post(f"/api/v1/voiceprints/people/{person['id']}/samples/from-asr", json={"job_id": job["id"], "segment_ids": [1]}).status_code == 409
    assert db.get_voiceprint_person(person["id"])["name"] == "库内名字"
    assert db.get_voiceprint_person(person["id"])["note"] == "库内备注"
    assert db.get_voiceprint_sample(sample["id"]) == sample


def test_rename_retains_long_library_name_merges_and_snapshots_identity(local):
    name = "会议人员" * 24
    person = db.create_voiceprint_person(name, note="研发")
    match = {"person_id": person["id"], "name": name, "note": "研发", "score": .8}
    job = source_job(local, [
        {"id": "Speaker_0", "label": f"{name}（研发）", "label_source": "voiceprint", "voiceprint_match": match},
        {"id": "Speaker_2", "label": "Speaker 2", "label_source": "default"},
    ])
    with TestClient(api_module.create_app()) as client:
        response = client.patch(f"/api/v1/jobs/{job['id']}/speakers/Speaker_2", json={"name": f"  {person['name']}  ", "note": person["note"]})
        assert response.status_code == 200
        result = response.json()
        assert len(result["speakers"]) == 1
        speaker = result["speakers"][0]
        assert speaker["id"] == "Speaker_0"
        assert speaker["name"] == name and len(speaker["name"]) > 80
        assert speaker["label"] == f"{name}（研发）" and speaker["note"] == "研发"
        assert speaker["label_source"] == "manual" and speaker["voiceprint_match"] == match
        assert db.get_voiceprint_person(person["id"]) == person
        for artifact in result["artifacts"]:
            exported = client.get(f"/api/v1/jobs/{job['id']}/artifacts/{artifact['name']}")
            assert exported.status_code == 200
            assert name in exported.text
        db.update_voiceprint_person(person["id"], name="库内新姓名", note="销售")
        assert client.get(f"/api/v1/jobs/{job['id']}/result").json() == result


def test_rename_keeps_audio_artifacts_and_playback_reference(local):
    job = source_job(local)
    output = local.jobs_dir / job["id"] / "output"
    audio = output / "reference.wav"
    audio.write_bytes(b"existing audio")
    artifact = {"name": audio.name, "path": str(audio), "mime_type": "audio/wav", "size_bytes": audio.stat().st_size}
    result = job["result"]
    result["artifacts"].append(artifact)
    result["playback_url"] = f"/api/v1/jobs/{job['id']}/playback"
    db.update_job(job["id"], result_json=result)
    with TestClient(api_module.create_app()) as client:
        response = client.patch(f"/api/v1/jobs/{job['id']}/speakers/Speaker_2", json={"name": "许榕俊"})
        assert response.status_code == 200
    assert response.json()["artifacts"][-1] == artifact
    assert db.get_job(job["id"])["result"]["artifacts"][-1] == artifact
    assert response.json()["playback_url"] == result["playback_url"]
    assert audio.read_bytes() == b"existing audio"


def test_export_replacement_failure_rolls_back_database_and_installed_files(local, monkeypatch):
    job = source_job(local)
    before = export_bytes(local, job)
    replace_file = asr_speakers.os.replace
    def fail_second_export(source, target):
        if Path(source).parent.name == "new" and Path(target).name == "transcript.txt":
            raise PermissionError("Windows-style open destination")
        return replace_file(source, target)
    with TestClient(api_module.create_app()) as client:
        monkeypatch.setattr(asr_speakers.os, "replace", fail_second_export)
        response = client.patch(f"/api/v1/jobs/{job['id']}/speakers/Speaker_2", json={"name": "许榕俊"})
        assert response.status_code == 503
    assert db.get_job(job["id"]) == job
    assert export_bytes(local, job) == before
    assert not list((local.jobs_dir / job["id"]).glob(".speaker-update-*"))


def test_database_commit_failure_restores_exports(local, monkeypatch):
    job = source_job(local)
    before = export_bytes(local, job)
    result = deepcopy(job["result"])
    result["speakers"][1].update(name="许榕俊", note=None, label_source="manual")
    merged, _ = asr_speakers.merge_speakers(result)
    connect = db.connect
    @contextmanager
    def failing_connection():
        with connect() as connection:
            class CommitFailure:
                def execute(self, sql, *args):
                    if sql == "COMMIT":
                        raise sqlite3.OperationalError("simulated commit failure")
                    return connection.execute(sql, *args)
            yield CommitFailure()
    with monkeypatch.context() as patch:
        patch.setattr(db, "connect", failing_connection)
        with pytest.raises(sqlite3.OperationalError):
            asr_speakers.persist_speaker_result(job["id"], merged, job["request"]["export_formats"], local.jobs_dir / job["id"] / "output")
    assert db.get_job(job["id"]) == job
    assert export_bytes(local, job) == before


def test_automatically_rolled_back_commit_failure_still_restores_exports(local, monkeypatch):
    job = source_job(local)
    before = export_bytes(local, job)
    connect = db.connect
    @contextmanager
    def failing_connection():
        with connect() as connection:
            class CommitFailure:
                def execute(self, sql, *args):
                    if sql == "COMMIT":
                        connection.execute("ROLLBACK")
                        raise sqlite3.OperationalError("simulated automatic rollback")
                    return connection.execute(sql, *args)
            yield CommitFailure()
    with TestClient(api_module.create_app()) as client, monkeypatch.context() as patch:
        patch.setattr(db, "connect", failing_connection)
        response = client.patch(f"/api/v1/jobs/{job['id']}/speakers/Speaker_2", json={"name": "许榕俊"})
        assert response.status_code == 503
    assert db.get_job(job["id"]) == job
    assert export_bytes(local, job) == before


def test_concurrent_renames_keep_both_updates(local, monkeypatch):
    job = source_job(local)
    persist = api_module.persist_speaker_result
    guard = threading.Lock()
    active = 0
    maximum = 0
    def slow_save(*args, **kwargs):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(.03)
            return persist(*args, **kwargs)
        finally:
            with guard:
                active -= 1
    monkeypatch.setattr(api_module, "persist_speaker_result", slow_save)
    with TestClient(api_module.create_app()) as client, ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.patch, f"/api/v1/jobs/{job['id']}/speakers/{speaker_id}", json={"name": name, "note": "备注"})
                   for speaker_id, name in (("Speaker_0", "甲"), ("Speaker_2", "乙"))]
        assert [future.result().status_code for future in futures] == [200, 200]
    assert maximum == 1
    assert [(item["name"], item["note"]) for item in db.get_job(job["id"])["result"]["speakers"]] == [("甲", "备注"), ("乙", "备注")]
    assert not asr_speakers._update_locks


def test_best_voiceprint_assignment_is_many_to_one_and_below_threshold_is_unmatched():
    people = [
        {"id": "research", "name": "张三", "note": "研发", "samples": [{"id": "a", "state": "ready", "audio_path": "unused.wav", "embedding": np.asarray([1, 0, 0], dtype=np.float32).tobytes()}]},
        {"id": "sales", "name": "张三", "note": "销售", "samples": [{"id": "b", "state": "ready", "audio_path": "unused.wav", "embedding": np.asarray([0, 1, 0], dtype=np.float32).tobytes()}]},
    ]
    centers = np.asarray([[.9, .1, 0], [.99, .01, 0], [0, .99, .01], [-.1, -.1, 1]], dtype=np.float32)
    matches, status = pipeline._match_voiceprints(None, centers, people)
    assert {key: value["person_id"] for key, value in matches.items()} == {"Speaker_0": "research", "Speaker_1": "research", "Speaker_2": "sales"}
    assert status["matched_speakers"] == 3
    assert status["indexed_samples"] == 2
    chunks = [{"start": index, "end": index + .8, "text": "你好", "language": "Chinese"} for index in range(4)]
    turns = [{"start": index, "end": index + 1, "speaker": f"Speaker_{index}"} for index in range(4)]
    result = pipeline.assemble(chunks, turns, 4, False, voiceprint_matches=matches)
    assert [item["speaker"] for item in result["segments"]] == ["Speaker_0", "Speaker_0", "Speaker_2", "Speaker_3"]
    assert [item["label"] for item in result["speakers"]] == ["张三（研发）", "张三（销售）", "Speaker 3"]
    assert pipeline._match_voiceprints(None, centers, [])[0] == {}


def test_speaker_openapi_note_contract_and_historical_results(local):
    with TestClient(api_module.create_app()) as client:
        schema = client.get("/openapi.json").json()
    request = schema["components"]["schemas"]["SpeakerNameRequest"]
    assert request["required"] == ["name"]
    assert "maxLength" not in request["properties"]["name"]
    assert "retained in full" in request["properties"]["name"]["description"]
    assert request["properties"]["note"]["maxLength"] == 20
    assert {"name", "note"} <= set(schema["components"]["schemas"]["SpeakerResponse"]["properties"])
    operation = schema["paths"]["/api/v1/jobs/{job_id}/speakers/{speaker_id}"]["patch"]
    examples = operation["requestBody"]["content"]["application/json"]["examples"]
    assert examples["name_and_note"]["value"] == {"name": "张三", "note": "研发一部"}
    assert examples["preserve_note"]["value"] == {"name": "李四"}
    assert examples["clear_note"]["value"]["note"] is None
    assert len(examples["long_name"]["value"]["name"]) > 80
    assert "503" in operation["responses"]
    from audio_intel.api_models import JobResultResponse
    old = {"speakers": [{"id": "Speaker_0", "label": "旧姓名", "label_source": "manual"}]}
    assert JobResultResponse.model_validate(old).model_dump(exclude_unset=True) == old
