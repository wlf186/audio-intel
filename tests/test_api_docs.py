from __future__ import annotations

from dataclasses import replace
import ast
import re
import subprocess
import sys

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
        assert '"docExpansion": "none"' in response.text
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
    assert len(operations) == 43
    assert len({operation["operationId"] for operation in operations}) == len(operations)
    assert all(operation.get("tags") for operation in operations)
    assert all("**English:**" in operation.get("description", "") for operation in operations)
    assert "/api/v1/capabilities.asr" not in schema["info"]["description"]
    assert "/api/v1/jobs/{id}" not in schema["info"]["description"]
    assert "/api/v1/jobs/{job_id}/events" in schema["info"]["description"]
    browser_example = schema["info"]["description"].split(
        "同源浏览器 fetch：HttpOnly 会话", 1,
    )[1].split("</details>", 1)[0]
    assert "cryptoApi.getRandomValues(new Uint8Array(16))" in browser_example
    assert "headers: {'Idempotency-Key': createIdempotencyKey()}" in browser_example
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
    assert "start/end boundaries only" in schema["info"]["description"]
    acceleration = schema["components"]["schemas"]["AccelerationResponse"]["properties"]
    assert {
        "requested", "active", "device", "target_batch_size", "stage_target_batch_sizes",
        "stage_batch_sizes", "batch_penalty_steps", "gpu_memory_total_mib", "physical_cores",
        "available_memory_bytes", "oom_fallbacks",
    } == set(acceleration)
    assert "total GPU memory" in acceleration["gpu_memory_total_mib"]["description"]
    assert "conservative batch-tier reductions" in acceleration["batch_penalty_steps"]["description"]
    assert "EventSnapshot" in schema["components"]["schemas"]
    assert "EventUpdate" in schema["components"]["schemas"]
    assert "JobSummaryResponse" in schema["components"]["schemas"]
    assert "EventJobResponse" in schema["components"]["schemas"]
    assert "AdmissionProblemDetail" in schema["components"]["schemas"]
    assert "OpenAIVerboseTranscription" in schema["components"]["schemas"]
    assert "text/event-stream" in schema["paths"]["/api/v1/events"]["get"]["responses"]["200"]["content"]
    global_events = schema["paths"]["/api/v1/events"]["get"]
    assert "initial `snapshot`" in global_events["description"]
    assert "`update` events only for semantic changes" in global_events["description"]
    global_stream = global_events["responses"]["200"]
    assert global_stream["x-event-data-schemas"]["update"]["$ref"].endswith("/EventUpdate")
    job_events = schema["paths"]["/api/v1/jobs/{job_id}/events"]["get"]["responses"]["200"]
    assert "text/event-stream" in job_events["content"]
    assert job_events["x-event-data-schema"]["$ref"].endswith("/EventJobResponse")
    assert "audio/wav" in schema["paths"]["/v1/audio/speech"]["post"]["responses"]["200"]["content"]
    speech_description = schema["paths"]["/v1/audio/speech"]["post"]["description"]
    assert all(value in speech_description for value in ("0.6B", "1.7B", "instructions", "VoiceDesign"))
    assert "/api/v1/tts/clone-references" in schema["paths"]
    speech_schema = schema["components"]["schemas"]["OpenAISpeechRequest"]["properties"]
    assert speech_schema["language"]["default"] == "Auto"
    assert speech_schema["compute_device"]["default"] == "gpu"
    assert speech_schema["accelerate_single_task"]["default"] is True
    assert speech_schema["instructions"]["maxLength"] == 1000
    assert "1.7B preset" in speech_schema["instructions"]["description"]
    controls_schema = schema["components"]["schemas"]["TtsControlCapability"]["properties"]
    assert set(controls_schema) == {
        "instruction_voice_modes", "instruction_required_voice_modes", "max_instruction_chars",
        "speaking_rate_parameter", "pitch_parameter", "sampling_parameters",
    }
    expected_controls = {
        "instruction_voice_modes": [],
        "instruction_required_voice_modes": [],
        "max_instruction_chars": 1000,
        "speaking_rate_parameter": False,
        "pitch_parameter": False,
        "sampling_parameters": False,
    }
    assert schema["components"]["schemas"]["TtsControlCapability"]["example"] == expected_controls
    assert schema["components"]["schemas"]["TtsCapability"]["example"]["controls"] == expected_controls
    assert schema["components"]["schemas"]["CapabilitiesResponse"]["example"]["tts"]["controls"] == expected_controls
    tts_body_ref = schema["paths"]["/api/v1/tts/jobs"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    tts_body = schema["components"]["schemas"][tts_body_ref.rsplit("/", 1)[-1]]
    assert tts_body["properties"]["instruct"]["maxLength"] == 1000
    assert "voice_design" in tts_body["properties"]["instruct"]["description"]
    for path in ("/api/v1/tts/jobs", "/v1/audio/speech"):
        unsupported = schema["paths"][path]["post"]["responses"]["422"]["content"]["application/problem+json"]["examples"]["unsupported_instruction"]["value"]
        assert unsupported["status"] == 422
        assert unsupported["code"] == "unsupported_tts_control"
        assert "model and voice mode" in unsupported["detail"]
        service_examples = schema["paths"][path]["post"]["responses"]["503"]["content"]["application/problem+json"]["examples"]
        assert set(service_examples) == {"tts_model_unavailable", "gpu_unavailable", "insufficient_gpu_memory"}
    assert schema["components"]["schemas"]["AsrCapability"]["properties"]["default_language"]["type"] == "string"
    asr_capability = schema["components"]["schemas"]["AsrCapability"]["properties"]
    assert {"default_model", "models", "hotword_library"} <= set(asr_capability)
    tts_capability = schema["components"]["schemas"]["TtsCapability"]["properties"]
    assert {"default_model", "model_capabilities", "controls"} <= set(tts_capability)
    assert "Physical checkpoint names retained for compatibility" in tts_capability["models"]["description"]
    assert "Compatibility union" in tts_capability["voice_modes"]["description"]
    assert "default 0.6B model" in tts_capability["compute_devices"]["description"]
    assert "model_capabilities[].controls" in tts_capability["controls"]["description"]
    assert {"voice_modes", "compute_devices", "controls", "checkpoints"} <= set(
        schema["components"]["schemas"]["TtsModelCapability"]["properties"]
    )
    compute_capability = schema["components"]["schemas"]["ComputeCapability"]["properties"]
    assert {"minimum_memory_mib", "total_memory_mib", "unavailable_reason_code"} <= set(compute_capability)
    assert "total GPU memory" in compute_capability["minimum_memory_mib"]["description"]
    hotword_capability = schema["components"]["schemas"]["HotwordLibraryCapability"]["properties"]
    assert set(hotword_capability) == {
        "supported", "max_lists", "max_terms_per_list", "max_selected_lists",
        "max_selected_terms", "max_prompt_chars", "max_name_chars", "max_term_chars",
    }
    assert "scenario hotword library" in asr_capability["hotword_library"]["description"]
    hotword_list = schema["components"]["schemas"]["HotwordListResponse"]["properties"]
    assert hotword_list["kind"]["$ref"].endswith("/HotwordListKind")
    assert "first-occurrence order" in hotword_list["terms"]["description"]
    assert "Number of terms" in hotword_list["term_count"]["description"]
    voiceprint_person = schema["components"]["schemas"]["VoiceprintPersonResponse"]["properties"]
    assert {"note", "include_in_hotword_library"} <= set(voiceprint_person)
    voiceprint_match = schema["components"]["schemas"]["VoiceprintMatch"]["properties"]
    assert "note" in voiceprint_match
    result_properties = schema["components"]["schemas"]["JobResultResponse"]["properties"]
    assert {"model", "model_name", "model_revision", "hotword_context"} <= set(result_properties)
    hotword_context = schema["components"]["schemas"]["HotwordContextResponse"]["properties"]
    assert set(hotword_context) == {"enabled", "list_ids", "list_names", "term_count"}
    result_examples = schema["paths"]["/api/v1/jobs/{job_id}/result"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    assert result_examples["asr"]["value"]["hotword_context"]["enabled"] is True
    assert "qwen3-asr-1.7b" == result_examples["asr"]["value"]["model"]
    assert result_examples["tts"]["value"]["precision"] == "BF16"
    capabilities_example = schema["paths"]["/api/v1/capabilities"]["get"]["responses"]["200"]["content"]["application/json"]["example"]
    assert [item["id"] for item in capabilities_example["asr"]["models"]] == [
        "qwen3-asr-0.6b", "qwen3-asr-1.7b",
    ]
    assert [item["id"] for item in capabilities_example["tts"]["model_capabilities"]] == [
        "qwen3-tts-0.6b", "qwen3-tts-1.7b",
    ]
    large_tts = capabilities_example["tts"]["model_capabilities"][1]
    assert large_tts["controls"]["instruction_voice_modes"] == ["preset", "voice_design"]
    assert next(device for device in large_tts["compute_devices"] if device["id"] == "gpu")["minimum_memory_mib"] == 7936
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
    status_parameters = schema["paths"]["/api/v1/jobs/{job_id}"]["get"]["parameters"]
    if_none_match = next(item for item in status_parameters if item["name"] == "If-None-Match")
    assert if_none_match["in"] == "header" and if_none_match["required"] is False
    source_operation = schema["paths"]["/api/v1/jobs/{job_id}/source"]["get"]
    range_parameter = next(item for item in source_operation["parameters"] if item["name"] == "Range")
    assert range_parameter["in"] == "header" and range_parameter["required"] is False
    assert {"Accept-Ranges", "Content-Length"} <= set(source_operation["responses"]["200"]["headers"])
    assert {"Accept-Ranges", "Content-Length", "Content-Range"} <= set(source_operation["responses"]["206"]["headers"])
    list_operation = schema["paths"]["/api/v1/jobs"]["get"]
    query = next(item for item in list_operation["parameters"] if item["name"] == "q")
    query_string = next(item for item in query["schema"]["anyOf"] if item.get("type") == "string")
    assert query_string["maxLength"] == 128
    list_properties = schema["components"]["schemas"]["JobListResponse"]["properties"]
    assert {"items", "count", "total", "limit", "offset", "has_more"} <= set(list_properties)
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

    for path in (
        "/api/v1/asr/jobs",
        "/api/v1/tts/clone-references",
        "/api/v1/voiceprints/people/{person_id}/samples/upload",
        "/v1/audio/transcriptions",
    ):
        operation = schema["paths"][path]["post"]
        body_ref = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        body = schema["components"]["schemas"][body_ref.rsplit("/", 1)[-1]]
        assert body["properties"]["model"]["default"] == "qwen3-asr-0.6b"
        assert body["properties"]["model"]["enum"] == ["qwen3-asr-0.6b", "qwen3-asr-1.7b"]
        assert body["properties"]["compute_device"]["enum"] == ["cpu", "gpu"]
        service_examples = operation["responses"]["503"]["content"]["application/problem+json"]["examples"]
        assert set(service_examples) == {
            "asr_model_unavailable", "gpu_unavailable", "insufficient_gpu_memory",
        }

    asr_body_ref = schema["paths"]["/api/v1/asr/jobs"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    asr_body = schema["components"]["schemas"][asr_body_ref.rsplit("/", 1)[-1]]
    assert "maximum 8" in asr_body["properties"]["hotword_list_ids"]["description"]
    hotword_create = schema["paths"]["/api/v1/asr/hotword-lists"]["post"]
    assert "NFKC" in hotword_create["description"]
    assert "project_terms" in hotword_create["requestBody"]["content"]["application/json"]["examples"]
    hotword_patch = schema["paths"]["/api/v1/asr/hotword-lists/{item_id}"]["patch"]
    assert "System lists return `403`" in hotword_patch["description"]
    assert "replace_terms" in hotword_patch["requestBody"]["content"]["application/json"]["examples"]
    person_create = schema["paths"]["/api/v1/voiceprints/people"]["post"]
    assert "person_with_note" in person_create["requestBody"]["content"]["application/json"]["examples"]
    person_patch = schema["paths"]["/api/v1/voiceprints/people/{person_id}"]["patch"]
    assert "disable_name_hotword" in person_patch["requestBody"]["content"]["application/json"]["examples"]
    assert "8151 MiB" in schema["info"]["description"]
    assert "recognition hints" in schema["info"]["description"]
    assert "surname-free list" in schema["info"]["description"]
    gpu_snapshot = schema["components"]["schemas"]["GpuSnapshot"]["properties"]
    assert "memory_free_mib" in gpu_snapshot
    assert "memory_system_reserved_mib" in gpu_snapshot
    assert "Device-wide currently free memory" in gpu_snapshot["memory_free_mib"]["description"]
    assert "max(total-used-free, 0)" in gpu_snapshot["memory_system_reserved_mib"]["description"]
    system_description = schema["paths"]["/api/v1/system"]["get"]["description"]
    assert "device-wide used and free memory" in system_description
    assert "total-used-free" in system_description
    assert schema["paths"]["/api/v1/jobs/{job_id}/result"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]["asr"]["value"]["hotword_context"]["list_ids"] == [
        "hotwords_voiceprint_people", "hotwords_voiceprint_people_short",
    ]

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

    for name, definition in schema["components"]["schemas"].items():
        if name in {"HTTPValidationError", "ValidationError"}:
            continue
        assert all(property_schema.get("description") for property_schema in definition.get("properties", {}).values()), name

    request_media = [
        media
        for methods in schema["paths"].values()
        for method, operation in methods.items() if method in HTTP_METHODS
        for media in operation.get("requestBody", {}).get("content", {}).values()
    ]
    assert request_media and all(media.get("examples") for media in request_media)
    assert set(schema["paths"]["/api/v1/tts/jobs"]["post"]["requestBody"]["content"]["multipart/form-data"]["examples"]) >= {
        "preset", "preset_1_7b", "voice_design", "inline_clone", "voiceprint",
    }


def test_documentation_code_blocks_are_self_contained_and_syntactically_valid() -> None:
    from audio_intel.api_docs import API_DESCRIPTION

    bash_blocks = re.findall(r"```bash\n(.*?)```", API_DESCRIPTION, re.S)
    python_blocks = re.findall(r"```python\n(.*?)```", API_DESCRIPTION, re.S)
    javascript_blocks = re.findall(r"```javascript\n(.*?)```", API_DESCRIPTION, re.S)
    assert bash_blocks and python_blocks and javascript_blocks
    for block in bash_blocks:
        assert "BASE_URL=" in block
        if sys.platform != "win32":
            subprocess.run(["bash", "-n"], input=block, text=True, encoding="utf-8", check=True)
    for block in python_blocks:
        ast.parse(block)
        assert "api_key =" in block
    for block in javascript_blocks:
        subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=block, text=True, encoding="utf-8", check=True,
        )
