"""Upload, preview and synthesize an offline document. Run with the API runtime.

Example: .runtime/api/bin/python scripts/tts_document.py manuscript.epub --device gpu
Office preview: .runtime/api/bin/python scripts/tts_document.py data.xlsx --preview-only
Supported: EPUB, TXT, Markdown, text PDF, DOCX, XLSX, PPTX.
Downloads are streamed to the client; no server-side full export is created.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, nargs="?")
    parser.add_argument("--import-id", help="Reuse a retained import")
    parser.add_argument("--list-imports", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--retry-parse", action="store_true", help="Explicitly retry a failed retained import")
    parser.add_argument("--download-mode", choices=("sections", "complete"), default="sections")
    parser.add_argument("--base-url", default="http://127.0.0.1:20810")
    parser.add_argument("--device", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--model", default="qwen3-tts-0.6b")
    parser.add_argument("--speaker", default="Vivian")
    parser.add_argument("--mode", choices=("auto", "length"), default="auto")
    parser.add_argument("--target", type=int, default=10000)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional local ZIP destination")
    args = parser.parse_args()
    if not args.list_imports and bool(args.document) == bool(args.import_id):
        parser.error("Provide a document file or --import-id")
    if args.retry_parse and not args.import_id:
        parser.error("--retry-parse requires --import-id")
    headers = {"Authorization": "Bearer " + os.environ["AUDIO_INTEL_API_KEY"]} if os.getenv("AUDIO_INTEL_API_KEY") else {}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120) as client:
        def read(path: str) -> dict:
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        if args.list_imports:
            response = client.get("/api/v1/tts/document-imports", params={"offset": args.offset, "limit": 20})
            response.raise_for_status()
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))
            return
        if args.import_id:
            imported = read("/api/v1/tts/document-imports/" + args.import_id)
            if args.retry_parse:
                response = client.post(f"/api/v1/tts/document-imports/{args.import_id}/retry")
                response.raise_for_status()
                imported = response.json()
        else:
            key = uuid.uuid4().hex
            with args.document.open("rb") as source:
                for attempt in range(3):
                    source.seek(0)
                    try:
                        response = client.post("/api/v1/tts/document-imports", files={"file": (args.document.name, source)}, headers={"Idempotency-Key": key})
                    except httpx.TransportError:
                        if attempt == 2:
                            raise
                        time.sleep(1)
                        continue
                    if response.status_code != 429 or attempt == 2:
                        response.raise_for_status()
                        break
                    time.sleep(max(1, int(response.headers.get("Retry-After", "1"))))
            imported = response.json()
        while imported["state"] in {"queued", "running"}:
            time.sleep(1)
            imported = read("/api/v1/tts/document-imports/" + imported["id"])
        if imported["state"] != "ready":
            raise RuntimeError(imported.get("error"))
        response = client.post(f"/api/v1/tts/document-imports/{imported['id']}/preview", json={"segmentation_mode": args.mode, "target_section_chars": args.target})
        response.raise_for_status()
        preview = response.json()
        for warning in preview["warnings"]:
            print("Warning:", warning)
        for section in preview["sections"]:
            print(section["index"], section["title"], section["char_count"], section["basis"])
        if args.preview_only:
            return
        response = client.post("/api/v1/tts/document-jobs", headers={"Idempotency-Key": uuid.uuid4().hex}, data={
            "document_import_id": imported["id"], "preview_revision": preview["preview_revision"],
            "segmentation_mode": args.mode, "target_section_chars": args.target,
            "section_ids": [s["id"] for s in preview["sections"]], "model": args.model,
            "voice_mode": "preset", "speaker": args.speaker, "compute_device": args.device,
            "display_name": Path(imported["name"]).stem,
        })
        response.raise_for_status()
        job = response.json()
        print("Job:", job["id"], flush=True)
        while job["state"] in {"queued", "running"}:
            time.sleep(2)
            job = read("/api/v1/jobs/" + job["id"])
        if job["state"] != "succeeded":
            raise RuntimeError(f"{job['state']}: {job.get('error_message')}; retry this job to retain completed sections")
        if args.output:
            with client.stream("GET", f"/api/v1/jobs/{job['id']}/document/download", params={"mode": args.download_mode}) as response:
                response.raise_for_status()
                with args.output.open("wb") as target:
                    for chunk in response.iter_bytes(256 * 1024):
                        target.write(chunk)
        print("Complete:", job["id"])


if __name__ == "__main__":
    main()
