from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

import audio_intel.api as api_module
import audio_intel.db as db_module
from audio_intel.config import settings
from audio_intel.model_registry import model_installation, model_manifest, target_ready
import scripts.download_models as download_models


def test_model_marker_requires_the_expected_revision(tmp_path) -> None:
    model = model_manifest()[0]
    models_dir = tmp_path / "models"
    marker = models_dir / model["name"] / ".complete"

    assert model_installation(models_dir, model)["state"] == "missing"
    marker.parent.mkdir(parents=True)
    marker.write_text("", encoding="utf-8")
    assert model_installation(models_dir, model)["state"] == "empty_marker"
    marker.write_text("wrong-revision\n", encoding="utf-8")
    assert model_installation(models_dir, model)["state"] == "revision_mismatch"
    marker.write_text(model["revision"] + "\n", encoding="utf-8")
    assert model_installation(models_dir, model)["state"] == "incomplete"
    for name in model["required_files"]:
        path = marker.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"present")
    assert model_installation(models_dir, model)["state"] == "installed"
    assert not target_ready(models_dir, "asr")


def test_huggingface_download_writes_revision_marker(tmp_path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def snapshot_download(**kwargs) -> None:
        calls.append(kwargs)
        Path(kwargs["local_dir"]).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(download_models, "MODELS", tmp_path / "models")
    monkeypatch.setattr(download_models, "CACHE", tmp_path / "cache")
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=snapshot_download))

    download_models.hf("owner/model", "model", "expected-revision")

    assert calls == [{
        "repo_id": "owner/model", "revision": "expected-revision",
        "local_dir": tmp_path / "models" / "model",
        "cache_dir": tmp_path / "cache" / "huggingface",
    }]
    assert (tmp_path / "models" / "model" / ".complete").read_text(encoding="utf-8") == "expected-revision\n"


def test_browser_session_auth_protects_system_and_media(tmp_path, monkeypatch) -> None:
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        models_dir=tmp_path / "models", api_key="correct horse",
        enabled_services=frozenset({"asr", "tts"}),
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    with TestClient(api_module.create_app()) as client:
        assert client.get("/api/v1/health").json() == {
            "status": "ok", "version": api_module.__version__, "offline": True,
        }
        assert client.get("/api/v1/auth/session").json() == {"required": True, "authenticated": False}
        assert client.get("/api/v1/system").status_code == 401
        assert client.post("/api/v1/auth/session", headers={"Authorization": "Bearer wrong"}).status_code == 401

        login = client.post("/api/v1/auth/session", headers={"Authorization": "Bearer correct horse"})
        assert login.status_code == 204
        cookie = login.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert client.get("/api/v1/auth/session").json()["authenticated"] is True
        assert client.get("/api/v1/system").status_code == 200

        assert client.post("/api/v1/voiceprints/people", json={"name": "blocked"}).status_code == 403
        created = client.post(
            "/api/v1/voiceprints/people", json={"name": "same origin"},
            headers={"Origin": "http://testserver"},
        )
        assert created.status_code == 201

        job_id = "cookie-media"
        source = local.jobs_dir / job_id / "input" / "source.wav"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"RIFF-cookie")
        db_module.create_job("asr", "source.wav", {"input_path": str(source)}, job_id)
        partial = client.get(f"/api/v1/jobs/{job_id}/source", headers={"Range": "bytes=0-3"})
        assert partial.status_code == 206 and partial.content == b"RIFF"

        assert client.delete("/api/v1/auth/session").status_code == 204
        assert client.get(f"/api/v1/jobs/{job_id}/source").status_code == 401
        assert client.get("/api/v1/system", headers={"Authorization": "Bearer correct horse"}).status_code == 200

    with TestClient(api_module.create_app()) as restarted:
        restarted.cookies.set(api_module.SESSION_COOKIE, "expired-on-restart")
        assert restarted.get("/api/v1/system").status_code == 401
