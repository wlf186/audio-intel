from __future__ import annotations

import math
import json
import re
import subprocess
import os
import platform
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Iterator

from audio_intel.config import settings
from audio_intel.gpu import compute_device_name, gpu_lease
from audio_intel.performance import lower_batch_size, resolve_acceleration
from audio_intel.progress import ThrottledProgress
from audio_intel.utils import waveform_peaks
from audio_intel.worker import JobContext


_cpu_models: dict[str, Any] = {}
GPU_TTS_BATCH_SIZE = 2
GPU_TTS_MIN_TOTAL_MIB = 3500
GPU_TTS_MIN_EFFECTIVE_FREE_MIB = 1100
MAX_CLONE_REFERENCE_SECONDS = 15.0
TTS_CODEC_FRAMES_PER_SECOND = 12.5
TTS_INITIAL_CODEC_FRAMES_PER_TEXT_TOKEN = 4.5
MAX_IN_FLIGHT_BATCH_PROGRESS = 0.95


def aligner_python() -> Path:
    configured = os.getenv("AUDIO_INTEL_ALIGNER_PYTHON", "").strip()
    if configured:
        return Path(configured)
    executable = "python.exe" if platform.system() == "Windows" else "python"
    scripts = "Scripts" if platform.system() == "Windows" else "bin"
    return settings.root / ".runtime" / "aligner" / scripts / executable


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


def _gpu_can_microbatch(torch_module: Any) -> bool:
    try:
        free_bytes, total_bytes = torch_module.cuda.mem_get_info()
        reclaimable_bytes = max(
            0,
            torch_module.cuda.memory_reserved() - torch_module.cuda.memory_allocated(),
        )
        mib = 1024 * 1024
        return (
            total_bytes / mib >= GPU_TTS_MIN_TOTAL_MIB
            and (free_bytes + reclaimable_bytes) / mib >= GPU_TTS_MIN_EFFECTIVE_FREE_MIB
        )
    except Exception:
        return False


@contextmanager
def _sequential_speech_decode(model: Any) -> Iterator[None]:
    tokenizer = model.model.speech_tokenizer
    original_decode = tokenizer.decode

    def decode_sequentially(encoded: Any) -> tuple[list[Any], int]:
        items = encoded if isinstance(encoded, list) else [encoded]
        waveforms: list[Any] = []
        sample_rate = 0
        for item in items:
            decoded, item_rate = original_decode([item])
            if sample_rate and item_rate != sample_rate:
                raise RuntimeError("Speech tokenizer returned inconsistent sample rates")
            sample_rate = item_rate
            waveforms.extend(decoded)
        return waveforms, sample_rate

    tokenizer.decode = decode_sequentially
    try:
        yield
    finally:
        tokenizer.decode = original_decode


def _generate_tts_batch(
    model: Any,
    request: dict[str, Any],
    texts: list[str],
    clone_prompt: Any,
    progress_callback: Any | None = None,
) -> tuple[list[Any], int]:
    batched = len(texts) > 1
    text: str | list[str] = texts if batched else texts[0]
    language = request.get("language") or "Auto"
    decode_context = _sequential_speech_decode(model) if batched else nullcontext()
    with _observe_tts_decode(model, progress_callback), decode_context:
        if request["voice_mode"] == "preset":
            return model.generate_custom_voice(
                text=text,
                language=[language] * len(texts) if batched else language,
                speaker=[request["speaker"]] * len(texts) if batched else request["speaker"],
                instruct=[request.get("instruct") or ""] * len(texts) if batched else request.get("instruct") or "",
                non_streaming_mode=True,
            )
        return model.generate_voice_clone(
            text=text,
            language=[language] * len(texts) if batched else language,
            voice_clone_prompt=clone_prompt,
            non_streaming_mode=True,
        )


@contextmanager
def _observe_tts_decode(model: Any, callback: Any | None) -> Iterator[None]:
    if callback is None:
        yield
        return
    talker = getattr(getattr(model, "model", None), "talker", None)
    register = getattr(talker, "register_forward_hook", None)
    if not callable(register):
        yield
        return

    def observe(_module: Any, _args: Any, output: Any) -> None:
        step = getattr(output, "generation_step", None)
        if isinstance(step, int) and step >= 0:
            callback(step + 1)

    handle = register(observe)
    try:
        yield
    finally:
        handle.remove()


def _tts_text_token_counts(model: Any, texts: list[str]) -> list[int]:
    try:
        prompt = int(model.processor(
            text=model._build_assistant_text(""), return_tensors="pt", padding=True,
        )["input_ids"].shape[-1])
        return [
            max(1, int(model.processor(
                text=model._build_assistant_text(text), return_tensors="pt", padding=True,
            )["input_ids"].shape[-1]) - prompt)
            for text in texts
        ]
    except (AttributeError, IndexError, TypeError, ValueError):
        return [max(1, len(text)) for text in texts]


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
    acceleration = resolve_acceleration(bool(request.get("accelerate_single_task", False)), compute_device)
    context.progress(0.05, "loading_tts_model")
    lease = gpu_lease(lambda: context.progress(0.05, "waiting_for_gpu")) if compute_device == "gpu" and not settings.mock_mode else None
    if lease is not None:
        lease.__enter__()
    model = None
    try:
        if request["voice_mode"] != "preset":
            _prepare_clone_reference(context, request, compute_device)
        model = None if settings.mock_mode else load_model(request["voice_mode"], compute_device)
        return _process_loaded(context, request, chunks, model, compute_device, acceleration)
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
    acceleration: dict[str, Any],
) -> dict[str, Any]:
    waveforms, rate = [], 24000
    clone_prompt = None
    if model is not None and request["voice_mode"] != "preset":
        context.progress(0.12, "preparing_voice_clone")
        clone_prompt = model.create_voice_clone_prompt(
            ref_audio=request["reference_audio_path"], ref_text=request["reference_text"],
            x_vector_only_mode=False,
        )
    torch_module = None
    if model is not None:
        import torch
        torch_module = torch
    configured_batch_size = 1
    if acceleration["requested"]:
        configured_batch_size = int(acceleration["target_batch_size"])
    elif compute_device == "gpu" and model is not None and _gpu_can_microbatch(torch_module):
        configured_batch_size = GPU_TTS_BATCH_SIZE
    actual_batch_sizes: list[int] = []
    fallbacks: list[dict[str, int]] = []
    token_counts = _tts_text_token_counts(model, chunks) if model is not None else [max(1, len(text)) for text in chunks]
    observed_codec_frames = 0.0
    observed_text_tokens = 0
    activity_sequence = 0
    index = 0
    context.progress(
        0.15, f"synthesizing_1_of_{len(chunks)}", 0, len(chunks),
        stage_progress=0.0, unit="text_chunk",
    )
    while index < len(chunks):
        batch_size = min(configured_batch_size, len(chunks) - index)
        if (
            not acceleration["requested"] and compute_device == "gpu" and batch_size > 1
            and not _gpu_can_microbatch(torch_module)
        ):
            batch_size = 1
        texts = chunks[index:index + batch_size]
        current_token_counts = token_counts[index:index + batch_size]
        frames_per_token = (
            observed_codec_frames / observed_text_tokens
            if observed_text_tokens else TTS_INITIAL_CODEC_FRAMES_PER_TEXT_TOKEN
        )
        expected_frames = max(1, math.ceil(max(current_token_counts) * frames_per_token))
        activity_sequence += 1

        def emit_activity(payload: dict[str, Any]) -> None:
            current = int(payload["current"])
            batch_fraction = min(MAX_IN_FLIGHT_BATCH_PROGRESS, current / expected_frames)
            stage_progress = (index + len(texts) * batch_fraction) / max(len(chunks), 1)
            context.progress(
                0.15 + 0.72 * stage_progress,
                f"synthesizing_{index + 1}_of_{len(chunks)}", index, len(chunks),
                stage_progress=stage_progress, unit="text_chunk", basis="estimated",
                activity={
                    "sequence": activity_sequence, "current": current,
                    "total": expected_frames, "unit": "codec_frame", "basis": "estimated",
                },
            )

        reporter = ThrottledProgress(emit_activity)
        if settings.mock_mode:
            generated = []
            for text in texts:
                audio, rate = mock_speech(text)
                generated.append(audio)
        else:
            try:
                generated, rate = _generate_tts_batch(
                    model, request, texts, clone_prompt,
                    progress_callback=lambda current: reporter.report({"current": current}),
                )
            except Exception as exc:
                if torch_module is None or not isinstance(exc, torch_module.OutOfMemoryError):
                    raise
                if batch_size <= 1:
                    raise
                reduced = lower_batch_size(batch_size)
                fallbacks.append({"from": batch_size, "to": reduced})
                configured_batch_size = reduced
                import gc
                gc.collect()
                if compute_device == "gpu" and torch_module.cuda.is_available():
                    torch_module.cuda.empty_cache()
                continue
        if len(generated) != len(texts):
            raise RuntimeError(f"TTS model returned {len(generated)} waveforms for {len(texts)} texts")
        if settings.mock_mode:
            waveforms.extend(list(audio) for audio in generated)
        else:
            import numpy as np
            waveforms.extend(np.asarray(audio, dtype=np.float32) for audio in generated)
        actual_batch_sizes.append(len(texts))
        index += len(texts)
        observed_text_tokens += sum(current_token_counts)
        observed_codec_frames += sum(
            len(audio) / max(rate, 1) * TTS_CODEC_FRAMES_PER_SECOND for audio in generated
        )
        completed_stage_progress = index / max(len(chunks), 1)
        context.progress(
            0.15 + 0.72 * completed_stage_progress,
            f"synthesizing_{min(index + 1, len(chunks))}_of_{len(chunks)}", index, len(chunks),
            stage_progress=completed_stage_progress, unit="text_chunk",
        )
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
    context.progress(0.94, "writing_audio")
    path = encode(context.output_dir / f"speech.{output_format}", merged, rate, output_format)
    mime = {"wav": "audio/wav", "flac": "audio/flac", "mp3": "audio/mpeg"}[output_format]
    return {
        "duration": round(len(merged) / rate, 3), "sample_rate": rate, "format": output_format,
        "language": request.get("language") or "Auto",
        "voice_mode": request["voice_mode"],
        "speaker": request.get("speaker") or request.get("voiceprint_person_name") or request.get("voice_profile_id"),
        "voiceprint_person_id": request.get("voiceprint_person_id"),
        "voiceprint_sample_id": request.get("voiceprint_sample_id"),
        "reference_job_id": request.get("reference_job_id"),
        "reference_language": request.get("reference_language"),
        "reference_duration_original": request.get("reference_duration_original"),
        "reference_duration_used": request.get("reference_duration_used"),
        "reference_truncated": bool(request.get("reference_truncated")),
        "compute_device": compute_device,
        "compute_device_name": compute_device_name(compute_device, request.get("compute_device_name")),
        "precision": "FP32" if compute_device == "cpu" else "BF16",
        "quantized": False,
        "acceleration": {
            **acceleration,
            "active": bool(acceleration["requested"] and max(actual_batch_sizes, default=1) > 1),
            "stage_batch_sizes": {"generation": max(actual_batch_sizes, default=1), "decoder": 1},
            "oom_fallbacks": [{"stage": "generation", **fallback} for fallback in fallbacks],
        },
        "waveform": waveform_peaks(merged, 240),
        "artifacts": [{"name": path.name, "path": str(path), "mime_type": mime, "size_bytes": path.stat().st_size}],
    }


def _word_text_end(text: str, words: list[dict[str, Any]], last_index: int) -> int | None:
    cursor = 0
    end_offset = None
    for index, word in enumerate(words):
        token = str(word.get("text", ""))
        if not token:
            continue
        offset = text.find(token, cursor)
        if offset < 0:
            return None
        cursor = offset + len(token)
        if index == last_index:
            end_offset = cursor
            break
    return end_offset


def _align_reference(
    context: JobContext,
    request: dict[str, Any],
    duration: float,
    compute_device: str,
) -> list[dict[str, Any]]:
    language = request.get("reference_language") or request.get("language")
    if language in {None, "", "Auto"}:
        raise ValueError("An explicit reference language is required to align an overlong clone sample")
    input_path = context.work_dir / "clone-align-input.json"
    output_path = context.work_dir / "clone-align-output.json"
    payload = {
        "model_path": str(settings.models_dir / "Qwen3-ForcedAligner-0.6B"),
        "compute_device": compute_device,
        "chunks": [{
            "path": request["reference_audio_path"], "text": request["reference_text"],
            "language": language, "start": 0.0, "end": duration,
        }],
    }
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    environment = {
        **__import__("os").environ,
        "PYTHONPATH": str(settings.root), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
    }
    context.progress(0.08, f"aligning_clone_reference_{compute_device}")
    subprocess.run(
        [str(aligner_python()), "-m", "asr.stage", "align", str(input_path), str(output_path)],
        check=True, env=environment,
    )
    result = json.loads(output_path.read_text(encoding="utf-8"))
    return result["chunks"][0].get("words", [])


def _prepare_clone_reference(
    context: JobContext,
    request: dict[str, Any],
    compute_device: str,
) -> None:
    import soundfile as sf

    path = str(request["reference_audio_path"])
    try:
        info = sf.info(path)
        duration = float(info.frames) / float(info.samplerate)
    except Exception:
        import av
        with av.open(path) as container:
            duration = float(container.duration or 0) / float(av.time_base)
    request["reference_duration_original"] = round(duration, 3)
    request["reference_duration_used"] = round(duration, 3)
    request["reference_truncated"] = False
    if duration <= MAX_CLONE_REFERENCE_SECONDS:
        return
    words = list(request.get("reference_words") or [])
    if not words:
        words = _align_reference(context, request, duration, compute_device)
        request["reference_words"] = words
        if request.get("voiceprint_sample_id"):
            from audio_intel.db import update_voiceprint_sample
            update_voiceprint_sample(request["voiceprint_sample_id"], words_json=words)
    eligible = [index for index, word in enumerate(words) if float(word.get("end", 0)) <= MAX_CLONE_REFERENCE_SECONDS]
    if not eligible:
        raise ValueError("Clone reference contains no complete aligned word within 15 seconds")
    last_index = eligible[-1]
    cutoff = float(words[last_index]["end"])
    text_end = _word_text_end(request["reference_text"], words, last_index)
    if text_end is None:
        words = _align_reference(context, request, duration, compute_device)
        last_index = max(
            (index for index, word in enumerate(words) if float(word.get("end", 0)) <= MAX_CLONE_REFERENCE_SECONDS),
            default=-1,
        )
        text_end = _word_text_end(request["reference_text"], words, last_index) if last_index >= 0 else None
        if text_end is None:
            raise ValueError("Aligned clone reference text does not match the stored transcript")
        cutoff = float(words[last_index]["end"])
    from audio_intel.media import extract_audio_clip
    clipped = context.work_dir / "clone-reference.wav"
    extract_audio_clip(Path(path), clipped, 0.0, cutoff)
    request["reference_audio_path"] = str(clipped)
    request["reference_text"] = request["reference_text"][:text_end]
    request["reference_duration_used"] = round(cutoff, 3)
    request["reference_truncated"] = True
