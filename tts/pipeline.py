from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from audio_intel.config import settings
from audio_intel.gpu import compute_device_name, gpu_lease
from audio_intel.utils import waveform_peaks
from audio_intel.worker import JobContext


_cpu_models: dict[str, Any] = {}


def split_text(text: str, limit: int = 300) -> list[str]:
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;.!])\s*", text) if item.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences or [text]:
        if current and len(current) + len(sentence) > limit:
            chunks.append(current); current = ""
        while len(sentence) > limit:
            chunks.append(sentence[:limit]); sentence = sentence[limit:]
        current += sentence
    if current:
        chunks.append(current)
    return chunks


def load_model(mode: str, compute_device: str) -> Any:
    key = "custom" if mode == "preset" else "base"
    import torch
    from qwen_tts import Qwen3TTSModel
    model_name = "Qwen3-TTS-12Hz-0.6B-CustomVoice" if key == "custom" else "Qwen3-TTS-12Hz-0.6B-Base"
    if compute_device == "gpu":
        return Qwen3TTSModel.from_pretrained(
            str(settings.models_dir / model_name), device_map="cuda:0", dtype=torch.bfloat16,
            attn_implementation="sdpa", local_files_only=True,
        )
    if key not in _cpu_models:
        _cpu_models.clear()  # one CPU full-precision model resident at a time
        _cpu_models[key] = Qwen3TTSModel.from_pretrained(
            str(settings.models_dir / model_name), device_map="cpu", dtype=torch.float32,
            attn_implementation="sdpa", local_files_only=True,
        )
    return _cpu_models[key]


def mock_speech(text: str, rate: int = 24000) -> tuple[Any, int]:
    duration = max(1.5, min(30.0, len(text) * 0.095))
    audio = []
    for index in range(int(duration * rate)):
        t = index / rate
        envelope = min(1.0, t * 8) * min(1.0, (duration - t) * 8)
        audio.append(.10 * math.sin(2 * math.pi * (170 + 15 * math.sin(t * 2)) * t) * envelope)
    return audio, rate


def encode(path: Path, audio: Any, rate: int, output_format: str) -> Path:
    if settings.mock_mode and output_format == "wav":
        import wave
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(rate)
            handle.writeframes(b"".join(int(max(-1, min(1, float(x))) * 32767).to_bytes(2, "little", signed=True) for x in audio))
        return path
    import soundfile as sf
    if output_format in {"wav", "flac"}:
        sf.write(path, audio, rate, subtype="PCM_16")
        return path
    temporary = path.with_suffix(".wav")
    sf.write(temporary, audio, rate, subtype="PCM_16")
    import av
    with av.open(str(temporary)) as source, av.open(str(path), "w") as target:
        stream = target.add_stream("libmp3lame", rate=rate)
        for frame in source.decode(audio=0):
            for packet in stream.encode(frame): target.mux(packet)
        for packet in stream.encode(): target.mux(packet)
    temporary.unlink(missing_ok=True)
    return path


def process_job(context: JobContext) -> dict[str, Any]:
    request = context.job["request"]
    compute_device = request.get("compute_device", "cpu")
    chunks = split_text(request["text"])
    context.progress(0.05, "loading_tts_model")
    lease = gpu_lease(lambda: context.progress(0.05, "waiting_for_gpu")) if compute_device == "gpu" and not settings.mock_mode else None
    if lease is not None:
        lease.__enter__()
    model = None
    try:
        model = None if settings.mock_mode else load_model(request["voice_mode"], compute_device)
        return _process_loaded(context, request, chunks, model, compute_device)
    finally:
        if compute_device == "gpu" and model is not None:
            import gc
            import torch
            del model
            gc.collect()
            torch.cuda.empty_cache()
        if lease is not None:
            lease.__exit__(None, None, None)


def _process_loaded(
    context: JobContext,
    request: dict[str, Any],
    chunks: list[str],
    model: Any,
    compute_device: str,
) -> dict[str, Any]:
    waveforms, rate = [], 24000
    clone_prompt = None
    if model is not None and request["voice_mode"] != "preset":
        context.progress(0.12, "preparing_voice_clone")
        clone_prompt = model.create_voice_clone_prompt(
            ref_audio=request["reference_audio_path"], ref_text=request["reference_text"],
            x_vector_only_mode=False,
        )
    for index, text in enumerate(chunks):
        context.progress(0.15 + 0.72 * index / max(len(chunks), 1), f"synthesizing_{index + 1}_of_{len(chunks)}")
        if settings.mock_mode:
            audio, rate = mock_speech(text)
        elif request["voice_mode"] == "preset":
            generated, rate = model.generate_custom_voice(
                text=text, language=request.get("language") or "Auto", speaker=request["speaker"],
                instruct=request.get("instruct") or "", non_streaming_mode=True,
            )
            audio = generated[0]
        else:
            generated, rate = model.generate_voice_clone(
                text=text, language=request.get("language") or "Auto", voice_clone_prompt=clone_prompt,
                non_streaming_mode=True,
            )
            audio = generated[0]
        if settings.mock_mode:
            waveforms.append(list(audio))
        else:
            import numpy as np
            waveforms.append(np.asarray(audio, dtype=np.float32))
    if settings.mock_mode:
        silence = [0.0] * int(rate * 0.18)
        merged = []
        for index, item in enumerate(waveforms):
            if index: merged.extend(silence)
            merged.extend(item)
    else:
        import numpy as np
        silence = np.zeros(int(rate * 0.18), dtype=np.float32)
        merged = np.concatenate([part for index, item in enumerate(waveforms) for part in ((silence,) if index else ()) + (item,)])
    output_format = request.get("response_format", "wav")
    path = encode(context.output_dir / f"speech.{output_format}", merged, rate, output_format)
    context.progress(0.94, "writing_audio")
    mime = {"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg"}[output_format]
    return {
        "duration": round(len(merged) / rate, 3), "sample_rate": rate, "format": output_format,
        "voice_mode": request["voice_mode"], "speaker": request.get("speaker") or request.get("voice_profile_id"),
        "compute_device": compute_device,
        "compute_device_name": compute_device_name(compute_device, request.get("compute_device_name")),
        "precision": "FP32" if compute_device == "cpu" else "BF16",
        "quantized": False,
        "waveform": waveform_peaks(merged, 240),
        "artifacts": [{"name": path.name, "path": str(path), "mime_type": mime, "size_bytes": path.stat().st_size}],
    }
