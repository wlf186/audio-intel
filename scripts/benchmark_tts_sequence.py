#!/usr/bin/env python3
"""Compare ordered sequence TTS with equivalent sequential single-item jobs."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


DEFAULT_TEXTS = [
    "欢迎收听，本期我们从一个具体问题开始。",
    "先说明材料中的核心事实，再讨论它意味着什么。",
    "这里需要区分直接证据和进一步推断。",
    "最后把结论收束到可验证的范围内。",
]


def wait_for_job(client: httpx.Client, base_url: str, headers: dict[str, str], job_id: str) -> dict[str, Any]:
    for _ in range(7200):
        response = client.get(f"{base_url}/api/v1/jobs/{job_id}", headers=headers)
        response.raise_for_status()
        job = response.json()
        if job.get("state") in {"succeeded", "completed", "done"}:
            return job
        if job.get("state") in {"failed", "cancelled", "error"}:
            raise RuntimeError(job.get("error_message") or job.get("error") or "TTS job failed")
        time.sleep(max(0.5, min(float(job.get("poll_after_seconds") or 1), 5)))
    raise TimeoutError(f"Timed out waiting for {job_id}")


def validate_wav_artifacts(
    client: httpx.Client, base_url: str, headers: dict[str, str], job: dict[str, Any], names: list[str],
) -> None:
    job_id = str(job.get("id") or job.get("job_id") or "")
    if not job_id:
        raise RuntimeError("Completed job did not include an ID")
    for name in names:
        response = client.get(f"{base_url}/api/v1/jobs/{job_id}/artifacts/{name}", headers=headers)
        response.raise_for_status()
        if not response.headers.get("content-type", "").startswith("audio/"):
            raise RuntimeError(f"Artifact {name} did not use an audio content type")
        if len(response.content) < 128 or not response.content.startswith(b"RIFF"):
            raise RuntimeError(f"Artifact {name} is not a valid non-empty WAV")


def load_items(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.items_json or args.candidate_json:
        payload = json.loads(Path(args.items_json or args.candidate_json).read_text(encoding="utf-8"))
        if args.candidate_json:
            turns = payload.get("turns") if isinstance(payload, dict) else None
            if not isinstance(turns, list) or not turns:
                raise ValueError("--candidate-json must contain a non-empty turns list")
            speakers = {"HOST_A": args.speaker_a, "HOST_B": args.speaker_b}
            source = [
                {
                    "id": f"turn-{index:03d}", "text": item.get("text"),
                    "speaker": speakers.get(str(item.get("speaker") or "")),
                }
                for index, item in enumerate(turns[:args.limit]) if isinstance(item, dict)
            ]
        else:
            source = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(source, list) or not source:
            raise ValueError("Benchmark JSON must contain a non-empty item list")
        items = [
            {
                "id": str(item.get("id") or f"item-{index:03d}"),
                "text": str(item.get("text") or "").strip(),
                "speaker": str(item.get("speaker") or "").strip(),
            }
            for index, item in enumerate(source)
            if isinstance(item, dict)
        ]
        if len(items) != len(source) or any(not item["text"] or not item["speaker"] for item in items):
            raise ValueError("Every benchmark item must contain text and speaker")
        if len({item["id"] for item in items}) != len(items):
            raise ValueError("Benchmark item IDs must be unique")
        return items
    texts = args.text or DEFAULT_TEXTS
    speakers = [args.speaker_a, args.speaker_b]
    return [
        {"id": f"item-{index:03d}", "text": text, "speaker": speakers[index % 2]}
        for index, text in enumerate(texts)
    ]


def submit_single(
    client: httpx.Client, base_url: str, headers: dict[str, str], text: str, speaker: str,
    model: str, device: str,
) -> str:
    response = client.post(
        f"{base_url}/api/v1/tts/jobs",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        data={
            "text": text, "language": "Chinese", "voice_mode": "preset", "speaker": speaker,
            "model": model, "response_format": "wav", "compute_device": device,
            "accelerate_single_task": "true",
        },
    )
    response.raise_for_status()
    return str(response.json().get("id") or response.json().get("job_id"))


def run(args: argparse.Namespace) -> dict[str, Any]:
    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    items = load_items(args)
    job_ids: list[str] = []
    single_timings: list[float] = []
    sequence_timings: list[float] = []
    sequence_jobs: list[dict[str, Any]] = []
    with httpx.Client(timeout=300) as client:
        capabilities = client.get(f"{base_url}/api/v1/capabilities", headers=headers)
        capabilities.raise_for_status()
        sequence_capability = (capabilities.json().get("tts") or {}).get("sequence_jobs") or {}
        if not sequence_capability.get("supported") or sequence_capability.get("contract_version") != 1:
            raise RuntimeError("Service does not advertise TTS sequence contract v1")
        try:
            if not args.no_warmup:
                warmup_id = submit_single(
                    client, base_url, headers, items[0]["text"], items[0]["speaker"], args.model, args.device,
                )
                job_ids.append(warmup_id)
                warmup = wait_for_job(client, base_url, headers, warmup_id)
                warmup_names = [str(item["name"]) for item in (warmup.get("result") or {}).get("artifacts") or []]
                validate_wav_artifacts(client, base_url, headers, warmup, warmup_names)

            def run_singles() -> float:
                started = time.perf_counter()
                for item in items:
                    job_id = submit_single(
                        client, base_url, headers, item["text"], item["speaker"], args.model, args.device,
                    )
                    job_ids.append(job_id)
                    job = wait_for_job(client, base_url, headers, job_id)
                    names = [str(artifact["name"]) for artifact in (job.get("result") or {}).get("artifacts") or []]
                    if len(names) != 1:
                        raise RuntimeError("Single-item TTS did not return exactly one artifact")
                    validate_wav_artifacts(client, base_url, headers, job, names)
                return time.perf_counter() - started

            def run_sequence() -> float:
                started = time.perf_counter()
                response = client.post(
                    f"{base_url}/api/v1/tts/sequence-jobs",
                    headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
                    json={
                        "model": args.model, "language": "Chinese", "voice_mode": "preset",
                        "compute_device": args.device, "items": items,
                    },
                )
                response.raise_for_status()
                sequence_id = str(response.json().get("id") or response.json().get("job_id"))
                job_ids.append(sequence_id)
                sequence_job = wait_for_job(client, base_url, headers, sequence_id)
                sequence_jobs.append(sequence_job)
                result_items = ((sequence_job.get("result") or {}).get("sequence") or {}).get("items") or []
                expected = [item["id"] for item in items]
                if [str(item.get("id") or "") for item in result_items] != expected:
                    raise RuntimeError("Sequence result item order does not match the request")
                validate_wav_artifacts(
                    client, base_url, headers, sequence_job,
                    [str(item.get("artifact_name") or "") for item in result_items],
                )
                return time.perf_counter() - started

            for repetition in range(args.repetitions):
                if repetition % 2:
                    sequence_timings.append(run_sequence())
                    single_timings.append(run_singles())
                else:
                    single_timings.append(run_singles())
                    sequence_timings.append(run_sequence())
        finally:
            if not args.keep_jobs:
                for job_id in job_ids:
                    try:
                        client.delete(f"{base_url}/api/v1/jobs/{job_id}", params={"purge": "true"}, headers=headers)
                    except httpx.HTTPError:
                        pass
    single_seconds = statistics.median(single_timings)
    sequence_seconds = statistics.median(sequence_timings)
    improvement = 1 - sequence_seconds / single_seconds
    sequence_result = sequence_jobs[-1].get("result") or {}
    return {
        "model": args.model, "device": args.device, "items": len(items),
        "repetitions": args.repetitions,
        "single_timings": [round(value, 3) for value in single_timings],
        "sequence_timings": [round(value, 3) for value in sequence_timings],
        "single_seconds": round(single_seconds, 3), "sequence_seconds": round(sequence_seconds, 3),
        "speedup": round(single_seconds / sequence_seconds, 3),
        "improvement_ratio": round(improvement, 4), "passes_15_percent_gate": improvement >= 0.15,
        "sequence_result_items": len((sequence_result.get("sequence") or {}).get("items") or []),
        "acceleration": sequence_result.get("acceleration") or {},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:20810")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="qwen3-tts-0.6b")
    parser.add_argument("--device", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--speaker-a", default="Vivian")
    parser.add_argument("--speaker-b", default="Dylan")
    parser.add_argument("--text", action="append", help="Repeat to supply the ordered benchmark script")
    parser.add_argument("--items-json", help="JSON list (or object with items) containing id, text and speaker")
    parser.add_argument("--candidate-json", help="Quick Read Podcast candidate JSON; HOST_A/B map to --speaker-a/b")
    parser.add_argument("--limit", type=int, default=12, help="Maximum candidate turns used with --candidate-json")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--keep-jobs", action="store_true")
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if sum(bool(value) for value in (args.items_json, args.candidate_json, args.text)) > 1:
        parser.error("--items-json, --candidate-json and --text are mutually exclusive")
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passes_15_percent_gate"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
