from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import wave
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from audio_intel.config import settings
from audio_intel.gpu import compute_device_name, gpu_lease
from audio_intel.utils import atomic_json, timecode, waveform_peaks
from audio_intel.worker import JobContext


ALIGNER_LANGUAGES = {"Chinese", "English", "Cantonese", "French", "German", "Italian", "Japanese", "Korean", "Portuguese", "Russian", "Spanish"}
GPU_DIARIZATION_BATCH_SIZE = 16
GPU_DIARIZATION_MIN_CPUS = 8


def decode_audio(source: Path, output: Path) -> tuple[Any, int]:
    import av
    import numpy as np

    samples: list[Any] = []
    with av.open(str(source)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        for frame in container.decode(stream):
            converted = resampler.resample(frame)
            for audio_frame in converted:
                samples.append(audio_frame.to_ndarray().reshape(-1))
        for audio_frame in resampler.resample(None):
            samples.append(audio_frame.to_ndarray().reshape(-1))
    if not samples:
        raise ValueError("Audio contains no decodable samples")
    audio = np.concatenate(samples).astype(np.float32)
    peak = float(np.max(np.abs(audio)))
    if peak > 1.0:
        audio /= peak
    import soundfile as sf
    sf.write(output, audio, 16000, subtype="PCM_16")
    return audio, 16000


def _mock_audio(source: Path, output: Path) -> tuple[list[float], int]:
    try:
        with wave.open(str(source), "rb") as handle:
            duration = handle.getnframes() / max(handle.getframerate(), 1)
    except (wave.Error, EOFError):
        duration = 18.0
    duration = max(2.0, duration)
    rate = 16000
    audio = [0.06 * math.sin(2 * math.pi * 180 * i / rate) for i in range(int(duration * rate))]
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(rate)
        handle.writeframes(b"".join(int(max(-1, min(1, x)) * 32767).to_bytes(2, "little", signed=True) for x in audio))
    return audio, rate


def run_vad(audio: Any, sample_rate: int) -> list[dict[str, float]]:
    from funasr import AutoModel

    model = AutoModel(model=str(settings.models_dir / "FSMN-VAD"), device="cpu", disable_update=True)
    result = model.generate(input=audio, fs=sample_rate)
    raw = (result[0].get("value") or result[0].get("timestamp") or []) if result else []
    segments = [{"start": float(item[0]) / 1000, "end": float(item[1]) / 1000} for item in raw]
    return segments


def combine_vad(segments: list[dict[str, float]], duration: float, target: float = 45.0) -> list[dict[str, float]]:
    if not segments:
        return [{"start": 0.0, "end": duration}] if duration else []
    chunks: list[dict[str, float]] = []
    current = dict(segments[0])
    for segment in segments[1:]:
        if segment["end"] - current["start"] <= target and segment["start"] - current["end"] < 2.0:
            current["end"] = segment["end"]
        else:
            chunks.append(current); current = dict(segment)
    chunks.append(current)
    split: list[dict[str, float]] = []
    for chunk in chunks:
        start = chunk["start"]
        while chunk["end"] - start > 60:
            split.append({"start": start, "end": start + 55}); start += 55
        split.append({"start": start, "end": chunk["end"]})
    return split


def write_chunks(audio: Any, rate: int, chunks: list[dict[str, float]], directory: Path) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    output = []
    for index, chunk in enumerate(chunks):
        path = directory / f"chunk-{index:04d}.wav"
        samples = audio[int(chunk["start"] * rate):int(chunk["end"] * rate)]
        if settings.mock_mode:
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(rate)
                handle.writeframes(b"".join(int(max(-1, min(1, x)) * 32767).to_bytes(2, "little", signed=True) for x in samples))
        else:
            import soundfile as sf
            sf.write(path, samples, rate, subtype="PCM_16")
        output.append({"index": index, "path": str(path), **chunk})
    return output


def diarize(
    audio: Any,
    vad: list[dict[str, float]],
    speakers: int | None,
    batch_size: int = 1,
) -> list[dict[str, Any]]:
    import torch
    from funasr import AutoModel
    from funasr.models.campplus.cluster_backend import ClusterBackend
    from funasr.models.campplus.utils import postprocess, sv_chunk

    model = AutoModel(
        model=str(settings.models_dir / "CAM++"),
        device="cpu",
        disable_update=True,
        disable_pbar=True,
    )
    vad_with_audio = [[item["start"], item["end"], audio[int(item["start"] * 16000):int(item["end"] * 16000)]] for item in vad]
    chunks = sv_chunk(vad_with_audio)
    if not chunks:
        return [{**item, "speaker": "Speaker_0"} for item in vad]
    embeddings = model.generate(
        input=[chunk[2] for chunk in chunks],
        cache={},
        is_final=True,
        batch_size=batch_size,
    )
    vectors = torch.cat([item["spk_embedding"] for item in embeddings], dim=0).detach().cpu().numpy()
    cluster = ClusterBackend(merge_thr=0.78).cpu()
    labels = cluster(vectors, oracle_num=speakers)
    try:
        diarized = postprocess(chunks, vad_with_audio, labels, vectors)
        return [{"start": float(item[0]), "end": float(item[1]), "speaker": f"Speaker_{int(item[2])}"} for item in diarized]
    except Exception:
        return [{"start": float(chunk[0]), "end": float(chunk[1]), "speaker": f"Speaker_{int(label)}"} for chunk, label in zip(chunks, labels)]


def run_stage(operation: str, payload: dict[str, Any], directory: Path) -> dict[str, Any]:
    input_path, output_path = directory / f"{operation}-input.json", directory / f"{operation}-output.json"
    atomic_json(input_path, payload)
    environment = os.environ.copy()
    environment.update({"PYTHONPATH": str(settings.root), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    subprocess.run([sys.executable, "-m", "asr.stage", operation, str(input_path), str(output_path)], check=True, env=environment)
    return json.loads(output_path.read_text(encoding="utf-8"))


def run_model_stage(
    context: JobContext,
    operation: str,
    payload: dict[str, Any],
    directory: Path,
    compute_device: str,
    progress: float,
) -> dict[str, Any]:
    payload["compute_device"] = compute_device
    if compute_device == "cpu":
        return run_stage(operation, payload, directory)
    with gpu_lease(lambda: context.progress(progress, "waiting_for_gpu")):
        return run_stage(operation, payload, directory)


def _parallel_diarization_enabled(compute_device: str) -> bool:
    return compute_device == "gpu" and (os.cpu_count() or 1) >= GPU_DIARIZATION_MIN_CPUS


def speaker_at(start: float, end: float, diarization: list[dict[str, Any]]) -> str:
    midpoint = (start + end) / 2
    containing = [item for item in diarization if item["start"] <= midpoint <= item["end"]]
    if containing:
        return containing[0]["speaker"]
    scored = [(max(0.0, min(end, item["end"]) - max(start, item["start"])), item["speaker"]) for item in diarization]
    return max(scored, default=(0, "Speaker_0"))[1]


def assemble(chunks: list[dict[str, Any]], diarization: list[dict[str, Any]], duration: float, aligned: bool) -> dict[str, Any]:
    segments = []
    for chunk in chunks:
        words = chunk.get("words") or []
        speaker = speaker_at(chunk["start"], chunk["end"], diarization)
        if words:
            word_items = [{**word, "speaker": speaker_at(word["start"], word["end"], diarization)} for word in words]
            speaker = Counter(word["speaker"] for word in word_items).most_common(1)[0][0]
        else:
            word_items = []
        segments.append({
            "id": len(segments), "start": round(chunk["start"], 3), "end": round(chunk["end"], 3),
            "speaker": speaker, "speaker_label": speaker.replace("_", " "), "text": chunk.get("text", ""),
            "words": word_items,
        })
    speaker_ids = sorted({segment["speaker"] for segment in segments}) or ["Speaker_0"]
    language = Counter(chunk.get("language", "Unknown") for chunk in chunks).most_common(1)[0][0] if chunks else "Unknown"
    return {
        "text": "".join(segment["text"] for segment in segments), "language": language,
        "duration": round(duration, 3), "timestamp_precision": "word_or_character" if aligned else "segment",
        "diarization_mode": "single_active_speaker", "speakers": [{"id": item, "label": item.replace("_", " ")} for item in speaker_ids],
        "segments": segments,
    }


def write_asr_exports(job_id: str, result: dict[str, Any], formats: list[str]) -> dict[str, Any]:
    output = settings.jobs_dir / job_id / "output"
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    def add(name: str, content: str, mime: str) -> None:
        path = output / name; path.write_text(content, encoding="utf-8")
        artifacts.append({"name": name, "path": str(path), "mime_type": mime, "size_bytes": path.stat().st_size})
    if "json" in formats:
        payload = {key: value for key, value in result.items() if key != "artifacts"}
        add("transcript.json", json.dumps(payload, ensure_ascii=False, indent=2), "application/json")
    if "txt" in formats:
        add("transcript.txt", "\n".join(f"[{timecode(x['start'])} - {timecode(x['end'])}] {x['speaker_label']}: {x['text']}" for x in result["segments"]), "text/plain")
    if "srt" in formats:
        add("transcript.srt", "\n\n".join(f"{i}\n{timecode(x['start'], ',')} --> {timecode(x['end'], ',')}\n{x['speaker_label']}: {x['text']}" for i, x in enumerate(result["segments"], 1)), "application/x-subrip")
    if "vtt" in formats:
        add("transcript.vtt", "WEBVTT\n\n" + "\n\n".join(f"{timecode(x['start'])} --> {timecode(x['end'])}\n<v {x['speaker_label']}>{x['text']}" for x in result["segments"]), "text/vtt")
    result["artifacts"] = artifacts
    return result


def process_job(context: JobContext) -> dict[str, Any]:
    request = context.job["request"]
    compute_device = request.get("compute_device", "gpu")
    source = Path(request["input_path"])
    normalized = context.work_dir / "normalized.wav"
    context.progress(0.04, "decoding_audio")
    audio, rate = _mock_audio(source, normalized) if settings.mock_mode else decode_audio(source, normalized)
    duration = len(audio) / rate
    context.progress(0.10, "voice_activity_detection")
    if settings.mock_mode:
        vad = [{"start": 0.0, "end": duration}]
    else:
        vad = run_vad(audio, rate)
    chunks = write_chunks(audio, rate, combine_vad(vad, duration), context.work_dir / "chunks")
    context.progress(0.20, "speaker_diarization")
    diarization_executor = None
    diarization_future = None
    if request.get("diarize") and not settings.mock_mode and _parallel_diarization_enabled(compute_device):
        diarization_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr-diarization")
        diarization_future = diarization_executor.submit(
            diarize,
            audio,
            vad,
            request.get("speaker_count"),
            GPU_DIARIZATION_BATCH_SIZE,
        )
        diarization = []
    elif request.get("diarize") and not settings.mock_mode:
        diarization = diarize(audio, vad, request.get("speaker_count"), batch_size=1)
    elif settings.mock_mode:
        midpoint = duration * 0.52
        diarization = [{"start": 0, "end": midpoint, "speaker": "Speaker_0"}, {"start": midpoint, "end": duration, "speaker": "Speaker_1"}]
    else:
        diarization = [{"start": 0, "end": duration, "speaker": "Speaker_0"}]
    try:
        context.progress(0.32, f"qwen3_asr_{compute_device}")
        if settings.mock_mode:
            examples = ["欢迎使用完全本地化的语音智能工作台。", "识别、说话人和时间戳都会保存在当前项目中。"]
            transcribed = {"chunks": [{**item, "text": examples[i % len(examples)], "language": "Chinese"} for i, item in enumerate(chunks)]}
        else:
            transcribed = run_model_stage(context, "transcribe", {"model_path": str(settings.models_dir / "Qwen3-ASR-0.6B"), "chunks": chunks, "language": request.get("language"), "context": request.get("context", "")}, context.work_dir, compute_device, 0.32)
        items = transcribed["chunks"]
        language = next((item.get("language") for item in items if item.get("language")), request.get("language", "Unknown"))
        should_align = bool(request.get("align")) and language in ALIGNER_LANGUAGES
        context.progress(0.68, f"qwen3_forced_alignment_{compute_device}" if should_align else "building_segment_timestamps")
        if should_align and settings.mock_mode:
            for item in items:
                text = item["text"]
                units = list(text)
                step = (item["end"] - item["start"]) / max(len(units), 1)
                item["words"] = [{"text": unit, "start": round(item["start"] + i * step, 3), "end": round(item["start"] + (i + 1) * step, 3)} for i, unit in enumerate(units)]
        elif should_align:
            items = run_model_stage(context, "align", {"model_path": str(settings.models_dir / "Qwen3-ForcedAligner-0.6B"), "chunks": items}, context.work_dir, compute_device, 0.68)["chunks"]
        if diarization_future is not None:
            diarization = diarization_future.result()
    finally:
        if diarization_executor is not None:
            diarization_executor.shutdown(wait=True)
    context.progress(0.88, "merging_speakers_and_timestamps")
    result = assemble(items, diarization, duration, should_align)
    result.update({
        "compute_device": compute_device,
        "compute_device_name": compute_device_name(compute_device, request.get("compute_device_name")),
        "precision": "FP32" if compute_device == "cpu" else "BF16",
        "quantized": False,
    })
    try:
        result["waveform"] = waveform_peaks(audio, 240)
    except Exception:
        result["waveform"] = []
    context.progress(0.96, "writing_exports")
    return write_asr_exports(context.job_id, result, request.get("export_formats", ["json", "srt", "vtt", "txt"]))
