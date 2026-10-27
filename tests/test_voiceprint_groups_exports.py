from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import io
import json
import sqlite3
import wave

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from docx import Document

from audio_intel import api, db, library_store, voiceprint_groups as groups
from audio_intel.config import settings
from audio_intel.library_models import LibraryArchive
from audio_intel.transcript_export import Replacement, TranscriptExportRequest, export_result, initials, mask_note


@pytest.fixture
def local(tmp_path, monkeypatch):
    config = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", api_key="", mock_mode=True,
                     deployment_profile="cpu", min_free_disk_bytes=0, enabled_services=frozenset({"asr", "tts"}))
    monkeypatch.setattr(api, "settings", config)
    monkeypatch.setattr(db, "settings", config)
    config.ensure_directories()
    db.init_db()
    return config


def test_groups_migration_default_atomic_moves_and_delete(local):
    person = db.create_voiceprint_person("刘亦菲")
    history = db.create_job("asr", "历史", {"text": "原始快照"})
    before = db.get_job(history["id"])
    with db.connect() as connection:
        original = tuple(connection.execute("SELECT * FROM voiceprint_people").fetchone())
        connection.execute("DROP TRIGGER voiceprint_default_membership")
        connection.execute("DROP TABLE voiceprint_memberships")
        connection.execute("DROP TABLE voiceprint_groups")
        connection.execute("UPDATE schema_meta SET version=13")
    db.init_db()
    db.init_db()
    assert db.get_job(history["id"]) == before
    assert db.get_voiceprint_person(person["id"])["group_id"] == groups.UNGROUPED_ID
    with db.connect() as connection:
        assert tuple(connection.execute("SELECT * FROM voiceprint_people").fetchone()) == original
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 16
    group = groups.create_group(" Team A ")
    with pytest.raises(sqlite3.IntegrityError):
        groups.create_group("Ｔeam A")
    with pytest.raises(ValueError):
        groups.move_people([person["id"], "missing"], group["id"])
    assert db.get_voiceprint_person(person["id"])["group_id"] == groups.UNGROUPED_ID
    groups.move_people([person["id"]], group["id"])
    assert db.get_voiceprint_person(person["id"])["group_id"] == group["id"]
    groups.delete_group(group["id"])
    assert db.get_voiceprint_person(person["id"])["group_id"] == groups.UNGROUPED_ID
    with pytest.raises(ValueError):
        groups.delete_group(groups.UNGROUPED_ID)


def test_group_api_batch_protects_active_import_and_readers(local):
    from audio_intel.document_download import reader_lease
    with TestClient(api.create_app()) as client:
        group = client.post("/api/v1/voiceprints/groups", json={"name": "工作组"}).json()
        first = client.post("/api/v1/voiceprints/people", json={"name": "甲", "group_id": group["id"]}).json()
        second = db.create_voiceprint_person("乙")
        assert first["group_id"] == group["id"]
        assert client.patch("/api/v1/voiceprints/people/" + first["id"], json={"group_id": groups.UNGROUPED_ID}).status_code == 200
        job = db.create_job("asr", "导入", {"purpose": "voiceprint_import"})
        sample = db.create_voiceprint_sample(first["id"], source_job_id=job["id"])
        response = client.post("/api/v1/voiceprints/people/batch-delete", json={"person_ids": [first["id"], second["id"]], "purge": True})
        assert [(item["deleted"], item["status"]) for item in response.json()["items"]] == [(False, 409), (True, 204)]
        assert db.get_voiceprint_person(first["id"])
        db.update_job(job["id"], state="cancelled")
        with reader_lease("voiceprint:" + sample["id"]):
            assert client.delete(f"/api/v1/voiceprints/people/{first['id']}?purge=true").status_code == 409
        assert client.delete(f"/api/v1/voiceprints/people/{first['id']}?purge=true").status_code == 204


def test_submission_snapshot_replays_after_group_moves_and_deletion(local):
    group = groups.create_group("识别范围")
    person = db.create_voiceprint_person("Marco", group_id=group["id"])
    other = db.create_voiceprint_person("刘亦菲")
    audio = io.BytesIO()
    with wave.open(audio, "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 16000)
    fields = {"compute_device": "cpu", "voiceprint_group_ids": json.dumps([group["id"]])}
    with TestClient(api.create_app()) as client:
        def submit(data=fields):
            return client.post("/api/v1/asr/jobs", data=data, files={"file": ("sample.wav", audio.getvalue(), "audio/wav")}, headers={"Idempotency-Key": "groups-snapshot-01"})
        accepted = submit()
        assert accepted.status_code == 202, accepted.text
        job = db.get_job(accepted.json()["id"])
        assert job["request"]["voiceprint_person_ids"] == [person["id"]]
        groups.move_people([person["id"]], groups.UNGROUPED_ID)
        groups.move_people([other["id"]], group["id"])
        groups.delete_group(group["id"])
        replay = submit()
        assert replay.status_code == 200, replay.text
        assert db.get_job(job["id"])["request"] == job["request"]
        assert submit({"compute_device": "cpu", "voiceprint_group_ids": json.dumps([groups.UNGROUPED_ID])}).status_code == 409
        db.update_job(job["id"], state="failed")
        db.retry_job(job["id"])
        assert db.get_job(job["id"])["request"] == job["request"]
        assert client.post("/api/v1/asr/jobs", data={"compute_device": "cpu", "voiceprint_group_ids": "[]"}, files={"file": ("sample.wav", audio.getvalue())}, headers={"Idempotency-Key": "empty-groups-001"}).status_code == 422


def test_library_v1_and_v2_preserve_membership_and_preview_revision(local):
    group = groups.create_group("影视组")
    person = db.create_voiceprint_person("刘亦菲", group_id=group["id"])
    with db.connect() as connection:
        current = library_store.snapshot(connection)
    source_person = {k: person[k] for k in ("id", "name", "note", "created_at", "updated_at")}
    source_person.update(include_in_hotword_library=True, aliases=[person["id"]], samples=[])
    legacy = LibraryArchive(exported_at=db.utcnow(), source_version="0.1.30", people=[source_person], hotword_lists=[])
    merge = library_store.build_plan(legacy, current, "merge")
    assert merge["preview"]["warnings"]
    with db.connect() as connection:
        library_store.commit_plan(connection, merge, "merge")
    assert db.get_voiceprint_person(person["id"])["group_id"] == group["id"]
    replacement = library_store.build_plan(legacy, current, "replace")
    with db.connect() as connection:
        library_store.commit_plan(connection, replacement, "replace")
    assert db.get_voiceprint_person(person["id"])["group_id"] == groups.UNGROUPED_ID
    archive_groups = [{k: g[k] for k in ("id", "name", "created_at", "updated_at")} for g in current["groups"]]
    modern = LibraryArchive(contract_version=2, exported_at=db.utcnow(), source_version="0.1.31", groups=archive_groups,
                            people=[dict(source_person, group_id=group["id"])], hotword_lists=[])
    with db.connect() as connection:
        plan = library_store.build_plan(modern, library_store.snapshot(connection), "replace")
        library_store.commit_plan(connection, plan, "replace")
    assert db.get_voiceprint_person(person["id"])["group_id"] == group["id"]
    before = library_store.revision(current)
    groups.move_people([person["id"]], groups.UNGROUPED_ID)
    with db.connect() as connection:
        assert library_store.revision(library_store.snapshot(connection)) != before


@pytest.mark.parametrize("name,expected", [("Vince Carter", "VC"), ("刘亦菲", "LYF"), ("Liu Yifei", "LY"), ("Marco", "M"), ("曾华", "ZH"), ("Speaker 0", "Speaker 0"), ("123", "S-1")])
def test_initials(name, expected):
    assert initials(name, "S-1") == expected


@pytest.mark.parametrize("note,expected", [("01234567", "01****67"), ("广东移动有限公司", "广东****公司"), ("1", "*"), ("123", "**3"), (None, None), ("😀乙丙丁", "😀**丁")])
def test_note_mask(note, expected):
    assert mask_note(note) == expected


def transcript():
    return {"text": "广东移动已经联系广东。", "hotword_context": {"raw": "广东移动"}, "voiceprint_library": {"people": "Vince Carter"},
            "speakers": [{"id": "Speaker_0", "name": "Vince Carter", "note": "01234567", "voiceprint_match": {"name": "Vince Carter", "note": "01234567"}},
                         {"id": "Speaker_1", "name": "Victor Cole"}],
            "segments": [{"id": 0, "speaker": "Speaker_0", "speaker_label": "Vince Carter（01234567）", "start": 0, "end": 3,
                          "text": "广东移动已经联系广东。", "words": [{"text": "广东", "start": 0, "end": 1}, {"text": "移动", "start": 1, "end": 2}, {"text": "已经联系广东。", "start": 2, "end": 3}]}]}


def test_export_copy_longest_literal_nonchained_and_words():
    result = transcript()
    before = deepcopy(result)
    policy = TranscriptExportRequest(replacements=[Replacement(source="广东", target="短"), Replacement(source="广东移动", target="gy"), Replacement(source="gy", target="again")])
    output = export_result(result, policy)
    assert output["text"] == "gy已经联系短。"
    assert [s["name"] for s in output["speakers"]] == ["VC-1", "VC-2"]
    assert output["segments"][0]["words"] == [{"text": "gy", "start": 0, "end": 2}, {"text": "已经联系短。", "start": 2, "end": 3}]
    assert output["segments"][0]["speaker_label"] == "VC-1（01****67）"
    encoded = json.dumps(output, ensure_ascii=False)
    for original in ("Vince Carter", "Victor Cole", "01234567", "广东移动", "hotword_context", "voiceprint_match"):
        assert original not in encoded
    assert result == before
    assert export_result(result, TranscriptExportRequest(redact=False, replacements=policy.replacements)) == result


def test_all_download_formats_default_on_auth_and_original_unchanged(local):
    result = transcript()
    job = db.create_job("asr", "个人隐私标题.wav", {})
    db.update_job(job["id"], state="succeeded", result_json=result)
    with TestClient(api.create_app()) as client:
        url = f"/api/v1/jobs/{job['id']}/transcript-export"
        for format in ("json", "txt", "srt", "vtt", "pdf", "docx"):
            response = client.post(url, json={"format": format, "replacements": [{"source": "广东移动", "target": "gy"}]})
            assert response.status_code == 200, response.text
            from urllib.parse import unquote
            assert unquote(response.headers["content-disposition"]).endswith("个人隐私标题." + format)
            text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages) if format == "pdf" else response.text
            if format == 'docx':
                document = Document(io.BytesIO(response.content))
                text = '\n'.join(p.text for p in document.paragraphs)
                assert document.core_properties.title == 'Transcript'
                assert document.core_properties.author == ''
            assert "VC-1" in text and "gy" in text
            assert "Vince Carter" not in text and "01234567" not in text
        assert "Vince Carter" in client.post(url, json={"format": "json", "redact": False}).text
        assert client.post(url, json={"replacements": [{"source": "x", "target": "y"}] * 101}).status_code == 422
        assert client.post(url, json={"replacements": [{"source": "x", "target": "y"}, {"source": "x", "target": "z"}]}).status_code == 422
        assert client.post(url, json={"unknown": True}).status_code == 422
    assert db.get_job(job["id"])["result"] == result


def test_group_and_export_authentication_and_strict_openapi(local, monkeypatch):
    config = replace(local, api_key="export-test-key")
    monkeypatch.setattr(api, "settings", config)
    monkeypatch.setattr(db, "settings", config)
    job = db.create_job("asr", "fixture.wav", {})
    db.update_job(job["id"], state="succeeded", result_json=transcript())
    url = f"/api/v1/jobs/{job['id']}/transcript-export"
    app = api.create_app()
    with TestClient(app) as client:
        assert client.get("/api/v1/voiceprints/groups").status_code == 401
        assert client.post(url, json={"format": "json"}).status_code == 401
        bearer = {"Authorization": "Bearer export-test-key"}
        assert client.post(url, json={"format": "json"}, headers=bearer).status_code == 200
        assert client.post("/api/v1/auth/session", headers=bearer).status_code == 204
        assert client.get("/api/v1/voiceprints/groups").status_code == 200
        assert client.post(url, json={"format": "json"}).status_code == 403
        assert client.post(url, json={"format": "json"}, headers={"Origin": "https://foreign.invalid"}).status_code == 403
        assert client.post(url, json={"format": "json"}, headers={"Origin": "http://testserver"}).status_code == 200
        assert client.post("/api/v1/voiceprints/groups", json={"name": "blocked"}).status_code == 403
        created = client.post("/api/v1/voiceprints/groups", json={"name": "same origin"}, headers={"Origin": "http://testserver"})
        assert created.status_code == 201
        cookies = dict(client.cookies)
        assert client.delete("/api/v1/auth/session").status_code == 204
        assert client.post(url, json={"format": "json"}, headers={"Origin": "http://testserver"}).status_code == 401
    with TestClient(api.create_app()) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get("/api/v1/voiceprints/groups").status_code == 401
    schema = app.openapi()
    definitions = schema["components"]["schemas"]
    assert definitions["TranscriptExportRequest"]["additionalProperties"] is False
    assert definitions["TranscriptExportRequest"]["properties"]["redact"]["default"] is True
    assert definitions["TranscriptExportRequest"]["properties"]["replacements"]["maxItems"] == 100
    assert definitions["Replacement"]["additionalProperties"] is False
    assert "group_id" in definitions["VoiceprintPersonResponse"]["required"]
    responses = schema["paths"]["/api/v1/jobs/{job_id}/transcript-export"]["post"]["responses"]
    assert set(responses["200"]["content"]) == {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/pdf", "application/json", "text/plain", "application/x-subrip", "text/vtt"}
