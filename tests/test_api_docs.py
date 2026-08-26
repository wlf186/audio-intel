from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

import audio_intel.api as api_module
import audio_intel.db as db_module
from audio_intel.config import settings


HTTP_METHODS = {"get", "post", "patch", "put", "delete"}


def docs_settings(tmp_path):
    frontend = tmp_path / "frontend"
    assets = frontend / "docs-assets"
    assets.mkdir(parents=True)
    (assets / "swagger-ui.css").write_text("/* local swagger css */", encoding="utf-8")
    (assets / "swagger-ui-bundle.js").write_text("window.SwaggerUIBundle=()=>({})", encoding="utf-8")
    (frontend / "sandevistan-audio.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8",
    )
    return replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        frontend_dir=frontend, enabled_services=frozenset({"asr", "tts"}),
    )


def test_swagger_is_fully_local_and_protected_by_csp(tmp_path, monkeypatch) -> None:
    local = docs_settings(tmp_path)
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)

    with TestClient(api_module.create_app()) as client:
        response = client.get("/docs")
        assert response.status_code == 200
        assert 'src="/docs-assets/swagger-ui-bundle.js"' in response.text
        assert 'href="/docs-assets/swagger-ui.css"' in response.text
        assert 'href="/sandevistan-audio.svg"' in response.text
        assert "cdn.jsdelivr.net" not in response.text
        assert "validator.swagger.io" not in response.text
        assert '"validatorUrl": null' in response.text
        assert "connect-src 'self'" in response.headers["content-security-policy"]
        assert client.get("/docs-assets/swagger-ui.css").text == "/* local swagger css */"
        assert "SwaggerUIBundle" in client.get("/docs-assets/swagger-ui-bundle.js").text


def test_docs_fail_locally_without_assets_and_never_fall_back(tmp_path, monkeypatch) -> None:
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        frontend_dir=tmp_path / "missing-frontend",
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)

    with TestClient(api_module.create_app()) as client:
        response = client.get("/docs")
        assert response.status_code == 503
        assert "setup api" in response.text
        assert "http://" not in response.text and "https://" not in response.text


def test_openapi_is_complete_bilingual_and_sdk_ready(tmp_path, monkeypatch) -> None:
    local = docs_settings(tmp_path)
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    schema = api_module.create_app().openapi()

    operations = [
        operation
        for methods in schema["paths"].values()
        for method, operation in methods.items()
        if method in HTTP_METHODS
    ]
    assert len(operations) == 33
    assert len({operation["operationId"] for operation in operations}) == len(operations)
    assert all(operation.get("tags") for operation in operations)
    assert all("**English:**" in operation.get("description", "") for operation in operations)
    assert schema["servers"] == [{"url": "/", "description": "当前本地服务 / Current local service"}]
    assert schema["components"]["securitySchemes"] == {
        "BearerAuth": {
            "type": "http",
            "description": "配置 AUDIO_INTEL_API_KEY 后输入密钥本身；客户端发送 Bearer token。 / Enter the API key itself when configured.",
            "scheme": "bearer",
        },
        "SessionCookie": {
            "type": "apiKey",
            "description": "由 /api/v1/auth/session 创建的同源 HttpOnly 浏览器会话。 / Same-origin HttpOnly browser session.",
            "in": "cookie", "name": "audio_intel_session",
        },
    }
    protected = schema["paths"]["/api/v1/jobs/{job_id}"]["get"]
    assert protected["security"] == [{"BearerAuth": []}, {"SessionCookie": []}]
    assert not any(parameter["name"].lower() == "authorization" for parameter in protected.get("parameters", []))
    assert schema["components"]["schemas"]["JobState"]["enum"] == [
        "queued", "running", "succeeded", "failed", "cancelled",
    ]
    progress = schema["components"]["schemas"]["JobResponse"]["properties"]["progress"]
    assert progress["minimum"] == 0 and progress["maximum"] == 1
    assert "EventSnapshot" in schema["components"]["schemas"]
    assert "OpenAIVerboseTranscription" in schema["components"]["schemas"]
    assert "text/event-stream" in schema["paths"]["/api/v1/events"]["get"]["responses"]["200"]["content"]
    assert "audio/wav" in schema["paths"]["/v1/audio/speech"]["post"]["responses"]["200"]["content"]

    for methods in schema["paths"].values():
        for method, operation in methods.items():
            if method not in HTTP_METHODS:
                continue
            for status, response in operation.get("responses", {}).items():
                problem = response.get("content", {}).get("application/problem+json", {})
                if "example" in problem:
                    assert problem["example"]["status"] == int(status)

    refs: list[str] = []

    def collect(value) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                refs.append(value["$ref"])
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(schema)
    names = set(schema["components"]["schemas"])
    assert not {
        ref.rsplit("/", 1)[-1]
        for ref in refs if ref.startswith("#/components/schemas/")
    } - names
