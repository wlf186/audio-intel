from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from audio_intel.performance import lower_batch_size


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
    while index < len(chunks):
        current = chunks[index:index + batch_size]
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
        actual_sizes.append(len(current))
        output.extend(
            {**item, "text": result.text.strip(), "language": result.language or forced_language or "Unknown"}
            for item, result in zip(current, results)
        )
        index += len(current)
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
    while offset < len(pending):
        current = pending[offset:offset + batch_size]
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
        actual_sizes.append(len(current))
        for (index, item), result in zip(current, results):
            words = [
                {"text": token.text, "start": round(item["start"] + token.start_time, 3),
                 "end": round(item["start"] + token.end_time, 3)}
                for token in result
            ]
            output[index] = {**item, "words": words}
        offset += len(current)
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
