"""Upload, preview and synthesize an offline document. Run with the API runtime.

Example: .runtime/api/bin/python scripts/tts_document.py manuscript.epub --device gpu
Office preview: .runtime/api/bin/python scripts/tts_document.py data.xlsx --preview-only
Supported: EPUB, TXT, Markdown, text PDF, DOCX, XLSX, PPTX.
Downloads are streamed to the client; no server-side full export is created.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path, nargs="?")
    parser.add_argument("--import-id", help="Reuse a retained import")
    parser.add_argument("--list-imports", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--retry-parse", action="store_true", help="Explicitly retry a failed retained import")
    parser.add_argument("--section-id", help="Section for html_section (defaults to the first selected section)")
    parser.add_argument("--download-mode", choices=("sections", "complete", "html_complete", "html_sections", "html_section"), default="sections")
    parser.add_argument("--base-url", default="http://127.0.0.1:20810")
    parser.add_argument("--device", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--model", default="qwen3-tts-0.6b")
    parser.add_argument("--speaker", default="Vivian")
    parser.add_argument("--voiceprint-sample-id", help="Use an eligible voiceprint sample instead of a preset speaker")
    parser.add_argument("--reference-start-seconds", type=float)
    parser.add_argument("--reference-end-seconds", type=float)
    parser.add_argument("--mode", choices=("auto", "length"), default="auto")
    parser.add_argument("--target", type=int, default=10000)
    parser.add_argument("--preview-only", action="store_true")
    parser.add_argument("--reanalyze", action="store_true", help="Reanalyze a retained import without changing existing jobs")
    parser.add_argument("--narration-options", type=Path, help="UTF-8 JSON narration options, including optional range overrides")
    parser.add_argument("--no-skip", action="store_true", help="Read auxiliary text too")
    parser.add_argument("--no-natural-pauses", action="store_true", help="Use legacy 180 ms chunk gaps")
    parser.add_argument("--waveform", action="store_true", help="Print the first section waveform using the authenticated artifact API")
    parser.add_argument("--output", type=Path, help="Local destination for the selected HTML, MP3 or ZIP download")
    parser.add_argument("--inspect-tags", action="store_true", help="Read MP3 metadata from the saved download (requires --output)")
    args = parser.parse_args()
    reference = {}
    if args.reference_start_seconds is not None or args.reference_end_seconds is not None:
        start, end = args.reference_start_seconds, args.reference_end_seconds
        if not args.voiceprint_sample_id or start is None or end is None or not all(math.isfinite(v) for v in (start,end)) or start < 0 or not 3 <= end-start <= 30:
            parser.error("Provide --voiceprint-sample-id and both reference endpoints defining 3–30 seconds")
        reference = {"reference_start_seconds": start, "reference_end_seconds": end}
    voice = {"voice_mode":"voiceprint", "voiceprint_sample_id":args.voiceprint_sample_id, **reference} if args.voiceprint_sample_id else {"voice_mode":"preset", "speaker":args.speaker}
    if args.inspect_tags and (not args.output or args.preview_only or args.list_imports):
        parser.error("--inspect-tags requires --output and synthesis/download")
    if not args.list_imports and bool(args.document) == bool(args.import_id):
        parser.error("Provide a document file or --import-id")
    if args.retry_parse and not args.import_id:
        parser.error("--retry-parse requires --import-id")
    if args.reanalyze and not args.import_id:
        parser.error("--reanalyze requires --import-id")
    narration = json.loads(args.narration_options.read_text(encoding="utf-8")) if args.narration_options else {}
    if args.no_skip: narration['skip_enabled'] = False
    if args.no_natural_pauses: narration['natural_pauses'] = False
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
            if args.reanalyze:
                response = client.post(f"/api/v1/tts/document-imports/{args.import_id}/reanalyze", headers={"Idempotency-Key":uuid.uuid4().hex})
                response.raise_for_status()
                imported = response.json()
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
        response = client.post(f"/api/v1/tts/document-imports/{imported['id']}/preview", json={"segmentation_mode": args.mode, "target_section_chars": args.target, **({"narration_options":narration} if narration else {})})
        response.raise_for_status()
        preview = response.json()
        for warning in preview["warnings"]:
            print("Warning:", warning)
        for section in preview["sections"]:
            print(section["index"], section["title"], section["char_count"], section["basis"])
        if args.preview_only:
            return
        if not any(s.get("speakable", True) for s in preview["sections"]):
            raise RuntimeError("No speakable sections; change narration options")
        response = client.post("/api/v1/tts/document-jobs", headers={"Idempotency-Key": uuid.uuid4().hex}, data={
            "document_import_id": imported["id"], "preview_revision": preview["preview_revision"],
            "segmentation_mode": args.mode, "target_section_chars": args.target,
            "section_ids": [s["id"] for s in preview["sections"] if s.get("speakable", True)],
            **({"narration_options":json.dumps(preview["narration_options"])} if preview.get("narration_options") else {}), "model": args.model,
            **voice, "compute_device": args.device,
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
        if args.waveform:
            name = job["result"]["document"]["sections"][0]["artifact_name"]
            print(json.dumps(read(f"/api/v1/jobs/{job['id']}/artifacts/{quote(name, safe='')}/waveform")))
        if args.output:
            with client.stream("GET", f"/api/v1/jobs/{job['id']}/document/download", params={"mode": args.download_mode, **({"section_id": args.section_id or job["result"]["document"]["sections"][0]["id"]} if args.download_mode=="html_section" else {})}) as response:
                response.raise_for_status()
                with args.output.open("wb") as target:
                    for chunk in response.iter_bytes(256 * 1024):
                        target.write(chunk)
            if args.inspect_tags and args.download_mode in {"sections", "complete"}:
                import av
                import zipfile
                if args.download_mode == "sections":
                    with zipfile.ZipFile(args.output) as archive:
                        with archive.open(archive.namelist()[0]) as handle, av.open(handle) as audio:
                            print(json.dumps(dict(audio.metadata), ensure_ascii=False, indent=2))
                else:
                    with av.open(str(args.output)) as audio:
                        print(json.dumps(dict(audio.metadata), ensure_ascii=False, indent=2))
        print("Complete:", job["id"])


if __name__ == "__main__":
    main()
