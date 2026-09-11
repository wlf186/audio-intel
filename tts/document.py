"""Document synthesis with bounded audio memory and durable section checkpoints."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from audio_intel import document_store as store
from audio_intel.utils import safe_filename


def file_hash(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(256 * 1024):
            value.update(block)
    return value.hexdigest()


def initialize(context: Any) -> None:
    from .pipeline import settings
    manifest = json.loads((settings.jobs_dir / context.job_id / "input/document.json").read_text(encoding="utf-8"))
    if manifest.get("contract_version") != 1:
        raise ValueError("Unsupported document snapshot version")
    store.ensure_sections(context.job_id, manifest["sections"])


def retry_delay(job: dict[str, Any], error: Exception) -> int | None:
    if job["request"].get("purpose") != "tts_document":
        return None
    recoverable = isinstance(error, MemoryError) or (
        type(error).__name__ == "OutOfMemoryError" and type(error).__module__.startswith("torch")
    ) or (isinstance(error, OSError) and error.errno in {errno.ENOMEM, errno.EAGAIN, errno.EBUSY})
    if not recoverable:
        return None
    item = next((p for p in store.sections(job["id"]) if p["state"] != "complete"), None)
    if not item or item["retries"] >= 2:
        return None
    delay = (30, 120)[item["retries"]]
    store.checkpoint(job["id"], item["id"], "pending", retries=item["retries"] + 1)
    return delay


def process_loaded(context: Any, request: dict[str, Any], model: Any, device: str,
                   acceleration: dict[str, Any], model_definition: dict[str, Any], checkpoint: dict[str, Any]) -> dict[str, Any]:
    import av
    import numpy as np
    from . import pipeline as pipeline
    from audio_intel.gpu import compute_device_name
    from audio_intel.performance import lower_batch_size
    from audio_intel.progress import ThrottledProgress
    config = pipeline.settings
    text = (config.jobs_dir / context.job_id / "input/document.txt").read_text(encoding="utf-8")
    if hashlib.sha256(text.encode()).hexdigest() != request["document"]["text_hash"]:
        raise ValueError("Document text snapshot checksum mismatch")
    items = store.sections(context.job_id)
    prompt = None
    if model is not None and request["voice_mode"] not in {"preset", "voice_design"}:
        prompt = model.create_voice_clone_prompt(ref_audio=request["reference_audio_path"], ref_text=request["reference_text"], x_vector_only_mode=False)
    configured = int(acceleration["target_batch_size"]) if acceleration["requested"] else 1
    artifacts = []; results = []; completed_chars = 0; fallbacks = []; actual = 1
    activity_sequence = 0
    total_chars = request["document"]["total_chars"]
    for number, item in enumerate(items, 1):
        count = item["end_offset"] - item["start_offset"]
        artifact = item.get("artifact")
        valid = False
        if item["state"] == "complete" and artifact:
            path = Path(artifact["path"])
            valid = path.parent.resolve() == context.output_dir.resolve() and path.is_file() and path.stat().st_size == artifact["size_bytes"] and file_hash(path) == artifact["sha256"]
        if not valid:
            store.checkpoint(context.job_id, item["id"], "generating")
            title = safe_filename(item["title"], f"section-{number}")
            while len(title.encode("utf-8")) > 140:
                title = title[:-1]
            path = context.output_dir / f"{number:0{max(3, len(str(len(items))))}d}_{title}.mp3"
            partial = context.output_dir / f"document-{item['id']}.partial"
            chunks = pipeline.split_text(text[item["start_offset"]:item["end_offset"]])
            section_fallbacks = []
            index = 0; samples = 0; rate = 24000; section_actual = 1; peaks: list[float] = []
            try:
                with av.open(str(partial), "w", format="mp3") as output:
                    stream = None

                    def write(waveform: Any) -> None:
                        nonlocal stream, samples
                        if stream is None:
                            stream = output.add_stream("libmp3lame", rate=rate)
                            stream.layout = "mono"; stream.bit_rate = 96000
                        values = np.asarray(waveform, dtype=np.float32).reshape(1, -1)
                        samples += values.shape[1]
                        for start in range(0, values.shape[1], 8192):
                            frame = av.AudioFrame.from_ndarray(values[:, start:start + 8192], format="fltp", layout="mono")
                            frame.sample_rate = rate
                            for packet in stream.encode(frame):
                                output.mux(packet)

                    while index < len(chunks):
                        context.progress(.15 + .8 * (completed_chars + count * index / len(chunks)) / total_chars,
                                         "document_synthesis", number - 1, len(items), stage_progress=index / len(chunks), unit="document_section")
                        if shutil.disk_usage(config.data_dir).free < config.min_free_disk_bytes:
                            raise OSError(errno.ENOSPC, "Insufficient disk space for document audio")
                        batch = chunks[index:index + configured]
                        activity_sequence += 1
                        def progress(current: int) -> None:
                            context.progress(.15 + .8 * (completed_chars + count * index / len(chunks)) / total_chars,
                                "document_synthesis", number - 1, len(items), stage_progress=index / len(chunks), unit="document_section",
                                activity={"sequence": activity_sequence, "current": current, "unit": "codec_frame", "basis": "observed"})
                        reporter = ThrottledProgress(lambda value: progress(value["current"]))
                        if config.mock_mode:
                            generated = []
                            for chunk in batch:
                                audio, rate = pipeline.mock_speech(chunk)
                                generated.append(audio)
                        else:
                            try:
                                generated, new_rate = pipeline._generate_tts_batch(model, request, batch, prompt, progress_callback=lambda current: reporter.report({"current": current}))
                                if stream is not None and new_rate != rate:
                                    raise ValueError("TTS sample rate changed inside a document")
                                rate = new_rate
                            except Exception as exc:
                                import torch
                                if not isinstance(exc, torch.OutOfMemoryError) or len(batch) <= 1:
                                    raise
                                reduced = lower_batch_size(len(batch))
                                section_fallbacks.append({"stage": "generation", "from": len(batch), "to": reduced})
                                configured = reduced
                                if device == "gpu":
                                    torch.cuda.empty_cache()
                                continue
                        if len(generated) != len(batch):
                            raise RuntimeError("TTS returned the wrong number of document chunks")
                        for audio in generated:
                            if samples:
                                write(np.zeros(int(rate * .18), dtype=np.float32))
                            write(audio)
                            peaks.append(round(float(np.max(np.abs(audio))), 4))
                            if len(peaks) > 480:
                                peaks = [max(peaks[i:i + 2]) for i in range(0, len(peaks), 2)]
                        actual = max(actual, len(batch)); section_actual = max(section_actual, len(batch))
                        index += len(batch)
                    if stream is None:
                        raise ValueError("Document section contains no speakable text")
                    for packet in stream.encode():
                        output.mux(packet)
                with partial.open("rb") as handle:
                    os.fsync(handle.fileno())
                os.replace(partial, path)
                artifact = {"name": path.name, "path": str(path), "mime_type": "audio/mpeg", "size_bytes": path.stat().st_size,
                            "sha256": file_hash(path), "duration": round(samples / rate, 3), "sample_rate": rate,
                            "waveform": peaks, "batch_size": section_actual, "oom_fallbacks": section_fallbacks}
                store.checkpoint(context.job_id, item["id"], "complete", artifact)
            finally:
                partial.unlink(missing_ok=True)
        actual = max(actual, artifact.get("batch_size", 1))
        fallbacks.extend(artifact.get("oom_fallbacks", []))
        artifacts.append(artifact)
        results.append({"id": item["id"], "index": number, "title": item["title"], "artifact_name": artifact["name"], "duration": artifact["duration"], "sample_rate": artifact["sample_rate"]})
        completed_chars += count
        context.progress(.15 + .8 * completed_chars / total_chars, "document_synthesis", number, len(items), stage_progress=1.0, unit="document_section")
    return {"document": {"contract_version": 1, "title": request["document"]["title"], "sections": results,
                "downloads": {mode: f"/api/v1/jobs/{context.job_id}/document/download?mode={mode}" for mode in ("sections", "complete")}},
            "duration": round(sum(a["duration"] for a in artifacts), 3), "format": "mp3", "sample_rate": artifacts[0]["sample_rate"],
            "model": model_definition["public_id"], "model_name": checkpoint["name"], "model_revision": checkpoint["revision"],
            "language": request["language"], "voice_mode": request["voice_mode"], "speaker": request.get("speaker"),
            "compute_device": device, "compute_device_name": compute_device_name(device, request.get("compute_device_name")),
            "precision": "FP32" if device == "cpu" else "BF16", "quantized": False,
            "acceleration": {**acceleration, "active": actual > 1, "stage_batch_sizes": {"generation": actual, "decoder": 1}, "oom_fallbacks": fallbacks},
            **{key: request.get(key) for key in ("instruct", "voiceprint_person_id", "voiceprint_sample_id", "reference_duration_original", "reference_duration_used", "reference_truncated")},
            "artifacts": artifacts}
