#!/usr/bin/env python3
"""Compare one ASR or TTS task with single-task acceleration off and on."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any

import httpx


TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


def wait_for_job(client: httpx.Client, job_id: str, poll_seconds: float) -> dict[str, Any]:
    while True:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()
        if job["state"] in TERMINAL_STATES:
            if job["state"] != "succeeded":
                raise RuntimeError(f"job {job_id} ended as {job['state']}: {job.get('error_message')}")
            return job
        time.sleep(poll_seconds)


def submit(
    client: httpx.Client,
    kind: str,
    device: str,
    accelerated: bool,
    audio: Path | None,
    text: str,
) -> dict[str, Any]:
    common = {
        "compute_device": device,
        "accelerate_single_task": str(accelerated).lower(),
    }
    if kind == "asr":
        assert audio is not None
        with audio.open("rb") as stream:
            response = client.post(
                "/api/v1/asr/jobs",
                data={**common, "language": "Auto", "speaker_count": "auto", "diarize": "true", "align": "true"},
                files={"file": (audio.name, stream, "application/octet-stream")},
            )
    else:
        response = client.post(
            "/api/v1/tts/jobs",
            data={**common, "text": text, "language": "Chinese", "voice_mode": "preset", "speaker": "Vivian"},
        )
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("asr", "tts"))
    parser.add_argument("--device", choices=("cpu", "gpu"), required=True)
    parser.add_argument("--audio", type=Path, help="ASR input; required when kind=asr")
    parser.add_argument("--text", default="这是单任务加速基准。" * 80, help="TTS input text")
    parser.add_argument("--base-url", default="http://127.0.0.1:20810")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.kind == "asr" and (args.audio is None or not args.audio.is_file()):
        parser.error("--audio must name an existing file when kind=asr")
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    headers = {}
    api_key = os.getenv("AUDIO_INTEL_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    runs: list[dict[str, Any]] = []
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120) as client:
        for repetition in range(args.repeat):
            # Alternating order reduces model warm-up and thermal bias.
            for accelerated in ((False, True) if repetition % 2 == 0 else (True, False)):
                queued = submit(client, args.kind, args.device, accelerated, args.audio, args.text)
                job = wait_for_job(client, queued["id"], args.poll_seconds)
                result = job.get("result") or {}
                runs.append({
                    "job_id": job["id"],
                    "accelerated": accelerated,
                    "processing_seconds": job["processing_seconds"],
                    "duration": result.get("duration"),
                    "text_length": len(result.get("text", "")),
                    "acceleration": result.get("acceleration"),
                })

    baseline = [float(run["processing_seconds"]) for run in runs if not run["accelerated"]]
    accelerated = [float(run["processing_seconds"]) for run in runs if run["accelerated"]]
    baseline_median = statistics.median(baseline)
    accelerated_median = statistics.median(accelerated)
    report = {
        "kind": args.kind,
        "device": args.device,
        "repeat": args.repeat,
        "baseline_median_seconds": baseline_median,
        "accelerated_median_seconds": accelerated_median,
        "speedup": baseline_median / accelerated_median if accelerated_median else None,
        "time_saved_percent": (1 - accelerated_median / baseline_median) * 100 if baseline_median else None,
        "runs": runs,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
