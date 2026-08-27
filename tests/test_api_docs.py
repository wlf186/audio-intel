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
    assert len(operations) == 36
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
    assert "best-effort" in progress["description"]
    progress_detail = schema["components"]["schemas"]["JobProgressDetail"]["properties"]
    assert progress_detail["basis"]["$ref"].endswith("/ProgressBasis")
    assert progress_detail["activity"]["anyOf"][0]["$ref"].endswith("/JobProgressActivity")
    activity = schema["components"]["schemas"]["JobProgressActivity"]["properties"]
    assert {"sequence", "current", "total", "unit", "basis", "updated_at"} <= set(activity)
    assert "EventSnapshot" in schema["components"]["schemas"]
    assert "EventJobResponse" in schema["components"]["schemas"]
    assert "AdmissionProblemDetail" in schema["components"]["schemas"]
    assert "OpenAIVerboseTranscription" in schema["components"]["schemas"]
    assert "text/event-stream" in schema["paths"]["/api/v1/events"]["get"]["responses"]["200"]["content"]
    global_events = schema["paths"]["/api/v1/events"]["get"]
    assert "latest 100 jobs" in global_events["description"]
    assert "every 0.5 seconds" in global_events["description"]
    job_events = schema["paths"]["/api/v1/jobs/{job_id}/events"]["get"]["responses"]["200"]
    assert "text/event-stream" in job_events["content"]
    assert job_events["x-event-data-schema"]["$ref"].endswith("/EventJobResponse")
    assert "audio/wav" in schema["paths"]["/v1/audio/speech"]["post"]["responses"]["200"]["content"]
    assert "/api/v1/tts/clone-references" in schema["paths"]
    speech_schema = schema["components"]["schemas"]["OpenAISpeechRequest"]["properties"]
    assert speech_schema["language"]["default"] == "Auto"
    assert speech_schema["compute_device"]["default"] == "gpu"
    assert speech_schema["accelerate_single_task"]["default"] is True
    assert schema["components"]["schemas"]["AsrCapability"]["properties"]["default_language"]["type"] == "string"
    for path in (
        "/api/v1/asr/jobs",
        "/api/v1/tts/clone-references",
        "/api/v1/tts/jobs",
        "/api/v1/voiceprints/people/{person_id}/samples/upload",
    ):
        parameters = schema["paths"][path]["post"]["parameters"]
        key = next(parameter for parameter in parameters if parameter["name"] == "Idempotency-Key")
        assert key["in"] == "header" and key["required"] is True
        key_schema = key["schema"]
        assert key_schema["minLength"] == 8 and key_schema["maxLength"] == 128
        assert key_schema["pattern"] == r"^[A-Za-z0-9._~:+-]{8,128}$"
        responses = schema["paths"][path]["post"]["responses"]
        assert {"200", "202", "400", "409", "429"} <= set(responses)
        assert responses["200"]["headers"]["Idempotency-Replayed"]["schema"]["enum"] == ["true"]
        assert responses["429"]["headers"]["Retry-After"]["schema"]["type"] == "integer"
        assert responses["429"]["content"]["application/problem+json"]["schema"]["$ref"].endswith("/AdmissionProblemDetail")
        assert set(responses["429"]["content"]["application/problem+json"]["examples"]) == {
            "submission_concurrency_limited", "queue_capacity_reached", "insufficient_queue_storage",
        }

    job_status = schema["paths"]["/api/v1/jobs/{job_id}"]["get"]["responses"]
    assert "304" in job_status
    assert {"ETag", "Cache-Control"} <= set(job_status["200"]["headers"])
    assert {"ETag", "Cache-Control"} <= set(job_status["304"]["headers"])
    assert schema["components"]["schemas"]["EstimateState"]["enum"] == ["warming_up", "ready"]
    assert schema["components"]["schemas"]["EstimateConfidence"]["enum"] == ["low", "medium", "high"]
    assert schema["components"]["schemas"]["QueueWaitReason"]["enum"] == ["worker", "gpu"]
    assert "voice_mode=inline_clone" in schema["info"]["description"]
    assert "voice_mode=inline " not in schema["info"]["description"]
    for path in ("/v1/audio/transcriptions", "/v1/audio/speech"):
        responses = schema["paths"][path]["post"]["responses"]
        assert {"200", "400", "409", "429"} <= set(responses)
        assert "Idempotency-Replayed" in responses["200"]["headers"]
        assert "Retry-After" in responses["429"]["headers"]
    for operation_id, path in (
        ("submitAsrJob", "/api/v1/asr/jobs"),
        ("uploadVoiceprintSample", "/api/v1/voiceprints/people/{person_id}/samples/upload"),
        ("createOpenAITranscription", "/v1/audio/transcriptions"),
    ):
        operation = schema["paths"][path]["post"]
        assert operation["operationId"] == operation_id
        body_ref = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        body = schema["components"]["schemas"][body_ref.rsplit("/", 1)[-1]]
        assert body["properties"]["language"]["enum"] == api_module.ASR_LANGUAGES

    for methods in schema["paths"].values():
        for method, operation in methods.items():
            if method not in HTTP_METHODS:
                continue
            for status, response in operation.get("responses", {}).items():
                problem = response.get("content", {}).get("application/problem+json", {})
                if "example" in problem:
                    assert problem["example"]["status"] == int(status)
                for example in problem.get("examples", {}).values():
                    assert example["value"]["status"] == int(status)

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
