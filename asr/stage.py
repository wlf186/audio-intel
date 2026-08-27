from __future__ import annotations

import argparse
import json
import os
import math
from pathlib import Path
from typing import Any

from audio_intel.performance import lower_batch_size
from audio_intel.progress import ThrottledProgress
from audio_intel.utils import atomic_json


ASR_INITIAL_OUTPUT_TOKENS_PER_SECOND = 3.0
ASR_ENCODER_PROGRESS_SHARE = 0.20
MAX_IN_FLIGHT_BATCH_PROGRESS = 0.95


def _progress(
    payload: dict[str, Any], stage: str, completed: int, total: int,
    *, stage_progress: float | None = None, unit: str = "audio_chunk",
    basis: str = "observed", activity: dict[str, Any] | None = None,
) -> None:
    path = payload.get("progress_path")
    if path:
        atomic_json(Path(path), {
            "stage": stage, "completed": completed, "total": total,
            "stage_progress": stage_progress, "unit": unit,
            "basis": basis, "activity": activity,
        })


def _module_layers(model: Any, path: tuple[str, ...]) -> list[Any]:
    current = model
    for name in path:
        current = getattr(current, name, None)
        if current is None:
            return []
    try:
        return list(current)
    except TypeError:
        return []


def _remove_hooks(handles: list[Any]) -> None:
    for handle in handles:
        handle.remove()


def _clear_cuda(torch_module: Any) -> None:
    if torch_module.cuda.is_available():
        torch_module.cuda.empty_cache()


def _stage_acceleration(
    stage: str,
    target: int,
    actual_sizes: list[int],
    fallbacks: list[dict[str, int]],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "target_batch_size": target,
        "effective_batch_size": max(actual_sizes, default=1),
        "fallbacks": fallbacks,
    }


def transcribe(payload: dict[str, Any]) -> dict[str, Any]:
    import torch
    from qwen_asr import Qwen3ASRModel

    compute_device = payload.get("compute_device", "gpu")
    dtype = torch.float32 if compute_device == "cpu" else torch.bfloat16
    device_map = "cpu" if compute_device == "cpu" else "cuda:0"
    target_batch_size = max(1, int(payload.get("batch_size", 1)))
    model = Qwen3ASRModel.from_pretrained(
        payload["model_path"], dtype=dtype, device_map=device_map,
        attn_implementation="sdpa", max_inference_batch_size=target_batch_size, max_new_tokens=1024,
        local_files_only=True,
    )
    output = []
    forced_language = None if payload.get("language") in {None, "", "Auto"} else payload["language"]
    chunks = list(payload["chunks"])
    actual_sizes: list[int] = []
    fallbacks: list[dict[str, int]] = []
    index = 0
    batch_size = target_batch_size
    activity_sequence = 0
    observed_output_tokens = 0
    observed_audio_seconds = 0.0
    _progress(payload, "transcription", 0, len(chunks), stage_progress=0.0)
    while index < len(chunks):
        current = chunks[index:index + batch_size]
        activity_sequence += 1
        durations = [
            max(0.0, float(item.get("end", 1.0)) - float(item.get("start", 0.0)))
            for item in current
        ]
        tokens_per_second = (
            observed_output_tokens / observed_audio_seconds
            if observed_audio_seconds else ASR_INITIAL_OUTPUT_TOKENS_PER_SECOND
        )
        expected_tokens = max(1, math.ceil(max(durations, default=0.0) * tokens_per_second))
        audio_layers = _module_layers(model, ("model", "thinker", "audio_tower", "layers"))
        audio_layer_total = max(1, len(audio_layers) * len(current))
        audio_layer_current = 0
        thinker_calls = 0
        handles: list[Any] = []

        def emit(batch_fraction: float, current_count: int, total_count: int, unit: str, basis: str) -> None:
            stage_progress = (
                index + len(current) * min(MAX_IN_FLIGHT_BATCH_PROGRESS, batch_fraction)
            ) / max(len(chunks), 1)
            _progress(
                payload, "transcription", index, len(chunks),
                stage_progress=stage_progress, basis="estimated", activity={
                    "sequence": activity_sequence, "current": current_count,
                    "total": total_count, "unit": unit, "basis": basis,
                },
            )

        encoder_reporter = ThrottledProgress(
            lambda item: emit(
                ASR_ENCODER_PROGRESS_SHARE * int(item["current"]) / audio_layer_total,
                int(item["current"]), audio_layer_total, "model_layer", "observed",
            )
        )
        token_reporter = ThrottledProgress(
            lambda item: emit(
                ASR_ENCODER_PROGRESS_SHARE
                + (MAX_IN_FLIGHT_BATCH_PROGRESS - ASR_ENCODER_PROGRESS_SHARE)
                * min(1.0, int(item["current"]) / expected_tokens),
                int(item["current"]), expected_tokens, "output_token", "estimated",
            )
        )

        def audio_hook(_module: Any, _args: Any, _output: Any) -> None:
            nonlocal audio_layer_current
            audio_layer_current += 1
            encoder_reporter.report({"current": audio_layer_current})

        def thinker_hook(_module: Any, _args: Any, _output: Any) -> None:
            nonlocal thinker_calls
            thinker_calls += 1
            generated = max(0, thinker_calls - 1)
            if generated:
                token_reporter.report({"current": generated})

        for layer in audio_layers:
            handles.append(layer.register_forward_hook(audio_hook))
        thinker = getattr(getattr(model, "model", None), "thinker", None)
        if callable(getattr(thinker, "register_forward_hook", None)):
            handles.append(thinker.register_forward_hook(thinker_hook))
        try:
            audio_input = [item["path"] for item in current]
            context_input = [payload.get("context", "")] * len(current)
            language_input = [forced_language] * len(current) if forced_language else None
            results = model.transcribe(
                audio=audio_input[0] if len(current) == 1 else audio_input,
                context=context_input[0] if len(current) == 1 else context_input,
                language=language_input[0] if language_input and len(current) == 1 else language_input,
                return_time_stamps=False,
            )
        except torch.OutOfMemoryError:
            if batch_size <= 1:
                raise
            reduced = lower_batch_size(batch_size)
            fallbacks.append({"from": batch_size, "to": reduced})
            batch_size = reduced
            if compute_device == "gpu":
                _clear_cuda(torch)
            continue
        finally:
            _remove_hooks(handles)
        actual_sizes.append(len(current))
        output.extend(
            {**item, "text": result.text.strip(), "language": result.language or forced_language or "Unknown"}
            for item, result in zip(current, results)
        )
        generated_tokens = max(0, thinker_calls - 1)
        if generated_tokens:
            observed_output_tokens += generated_tokens
            observed_audio_seconds += max(durations, default=0.0)
        index += len(current)
        _progress(
            payload, "transcription", index, len(chunks),
            stage_progress=index / max(len(chunks), 1),
        )
    return {
        "chunks": output,
        "acceleration": _stage_acceleration("transcription", target_batch_size, actual_sizes, fallbacks),
    }


def align(payload: dict[str, Any]) -> dict[str, Any]:
    import torch
    from qwen_asr import Qwen3ForcedAligner

    compute_device = payload.get("compute_device", "gpu")
    dtype = torch.float32 if compute_device == "cpu" else torch.bfloat16
    device_map = "cpu" if compute_device == "cpu" else "cuda:0"
    model = Qwen3ForcedAligner.from_pretrained(
        payload["model_path"], dtype=dtype, device_map=device_map,
        attn_implementation="sdpa", local_files_only=True,
    )
    target_batch_size = max(1, int(payload.get("batch_size", 1)))
    chunks = list(payload["chunks"])
    output: list[dict[str, Any] | None] = [None] * len(chunks)
    pending = [(index, item) for index, item in enumerate(chunks) if item.get("text")]
    for index, item in enumerate(chunks):
        if not item.get("text"):
            output[index] = {**item, "words": []}
    actual_sizes: list[int] = []
    fallbacks: list[dict[str, int]] = []
    offset = 0
    batch_size = target_batch_size
    activity_sequence = 0
    _progress(payload, "alignment", 0, len(pending), stage_progress=0.0)
    while offset < len(pending):
        current = pending[offset:offset + batch_size]
        activity_sequence += 1
        audio_layers = _module_layers(model, ("model", "thinker", "audio_tower", "layers"))
        text_layers = _module_layers(model, ("model", "thinker", "model", "layers"))
        layer_total = max(1, len(audio_layers) * len(current) + len(text_layers))
        layer_current = 0
        handles: list[Any] = []

        def emit_alignment(item: dict[str, Any]) -> None:
            current_layers = int(item["current"])
            batch_fraction = MAX_IN_FLIGHT_BATCH_PROGRESS * min(1.0, current_layers / layer_total)
            stage_progress = (
                offset + len(current) * batch_fraction
            ) / max(len(pending), 1)
            _progress(
                payload, "alignment", offset, len(pending),
                stage_progress=stage_progress, basis="estimated", activity={
                    "sequence": activity_sequence, "current": current_layers,
                    "total": layer_total, "unit": "model_layer", "basis": "observed",
                },
            )

        reporter = ThrottledProgress(emit_alignment)

        def layer_hook(_module: Any, _args: Any, _output: Any) -> None:
            nonlocal layer_current
            layer_current += 1
            reporter.report({"current": layer_current})

        for layer in [*audio_layers, *text_layers]:
            handles.append(layer.register_forward_hook(layer_hook))
        try:
            audio_input = [item["path"] for _, item in current]
            text_input = [item["text"] for _, item in current]
            language_input = [item["language"] for _, item in current]
            results = model.align(
                audio=audio_input[0] if len(current) == 1 else audio_input,
                text=text_input[0] if len(current) == 1 else text_input,
                language=language_input[0] if len(current) == 1 else language_input,
            )
        except torch.OutOfMemoryError:
            if batch_size <= 1:
                raise
            reduced = lower_batch_size(batch_size)
            fallbacks.append({"from": batch_size, "to": reduced})
            batch_size = reduced
            if compute_device == "gpu":
                _clear_cuda(torch)
            continue
        finally:
            _remove_hooks(handles)
        actual_sizes.append(len(current))
        for (index, item), result in zip(current, results):
            words = [
                {"text": token.text, "start": round(item["start"] + token.start_time, 3),
                 "end": round(item["start"] + token.end_time, 3)}
                for token in result
            ]
            output[index] = {**item, "words": words}
        offset += len(current)
        _progress(
            payload, "alignment", offset, len(pending),
            stage_progress=offset / max(len(pending), 1),
        )
    return {
        "chunks": [item for item in output if item is not None],
        "acceleration": _stage_acceleration("alignment", target_batch_size, actual_sizes, fallbacks),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("transcribe", "align"))
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = transcribe(payload) if args.operation == "transcribe" else align(payload)
    args.output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
