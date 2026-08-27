from __future__ import annotations

import math
import os
import tempfile
import time
import uuid
import wave
from pathlib import Path
from typing import Any

import httpx


BASE_URL = os.getenv("AUDIO_INTEL_URL", "http://127.0.0.1:20810").rstrip("/")


def wait_for_job(client: httpx.Client, job_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["state"] == "succeeded":
            return job
        if job["state"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Job {job_id} ended in {job['state']}: {job.get('error_message')}")
        time.sleep(0.25)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout:.0f} seconds")


def write_test_wave(path: Path) -> None:
    rate = 16000
    frames = bytearray()
    for index in range(rate):
        value = int(0.08 * math.sin(2 * math.pi * 220 * index / rate) * 32767)
        frames.extend(value.to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)


def main() -> None:
    headers = {}
    if api_key := os.getenv("AUDIO_INTEL_API_KEY", "").strip():
        headers["Authorization"] = f"Bearer {api_key}"
    with tempfile.TemporaryDirectory(prefix="audio-intel-smoke-") as temporary, httpx.Client(
        base_url=BASE_URL, headers=headers, timeout=30,
    ) as client:
        source = Path(temporary) / "smoke.wav"
        write_test_wave(source)
        health = client.get("/api/v1/health")
        health.raise_for_status()
        capabilities = client.get("/api/v1/capabilities")
        capabilities.raise_for_status()
        asr_capability = capabilities.json()["asr"]
        default_model = asr_capability["default_model"]
        if default_model not in {item["id"] for item in asr_capability["models"]}:
            raise RuntimeError("Default ASR model is missing from capabilities.asr.models")
        tts_capability = capabilities.json()["tts"]
        default_tts_model = tts_capability["default_model"]
        if default_tts_model not in {item["id"] for item in tts_capability["model_capabilities"]}:
            raise RuntimeError("Default TTS model is missing from capabilities.tts.model_capabilities")

        hotword_id: str | None = None
        try:
            hotword_response = client.post(
                "/api/v1/asr/hotword-lists",
                json={
                    "name": f"smoke-{uuid.uuid4().hex[:12]}",
                    "terms": ["Sandevistan-Audio", "Qwen3-ASR"],
                },
            )
            hotword_response.raise_for_status()
            hotword_id = hotword_response.json()["id"]

            with source.open("rb") as audio:
                asr_response = client.post(
                    "/api/v1/asr/jobs",
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                    files={"file": (source.name, audio, "audio/wav")},
                    data={
                        "model": default_model, "hotword_list_ids": hotword_id,
                        "language": "Chinese", "speaker_count": "2", "diarize": "true",
                        "align": "true", "compute_device": "cpu",
                    },
                )
            asr_response.raise_for_status()
            tts_response = client.post(
                "/api/v1/tts/jobs",
                headers={"Idempotency-Key": str(uuid.uuid4())},
                data={
                    "model": default_tts_model, "text": "本地语音合成冒烟测试。", "language": "Chinese", "voice_mode": "preset",
                    "speaker": "Vivian", "response_format": "wav", "compute_device": "cpu",
                },
            )
            tts_response.raise_for_status()

            for response in (asr_response, tts_response):
                job = wait_for_job(client, response.json()["id"])
                result = client.get(job["result_url"])
                result.raise_for_status()
                payload = result.json()
                if not payload.get("artifacts"):
                    raise RuntimeError(f"Job {job['id']} returned no artifacts")
                if response is asr_response:
                    if payload.get("model") != default_model:
                        raise RuntimeError("ASR result did not preserve the selected model")
                    hotwords = payload.get("hotword_context") or {}
                    if not hotwords.get("enabled") or hotword_id not in hotwords.get("list_ids", []):
                        raise RuntimeError("ASR result did not preserve the selected hotword list")
                elif payload.get("model") != default_tts_model:
                    raise RuntimeError("TTS result did not preserve the selected model")
        finally:
            if hotword_id is not None:
                cleanup = client.delete(f"/api/v1/asr/hotword-lists/{hotword_id}")
                cleanup.raise_for_status()
        print("Health, ASR, and TTS mock pipelines completed successfully.")


if __name__ == "__main__":
    main()
