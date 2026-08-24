from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def transcribe(payload: dict[str, Any]) -> dict[str, Any]:
    import torch
    from qwen_asr import Qwen3ASRModel

    compute_device = payload.get("compute_device", "gpu")
    dtype = torch.float32 if compute_device == "cpu" else torch.bfloat16
    device_map = "cpu" if compute_device == "cpu" else "cuda:0"
    model = Qwen3ASRModel.from_pretrained(
        payload["model_path"], dtype=dtype, device_map=device_map,
        attn_implementation="sdpa", max_inference_batch_size=1, max_new_tokens=1024,
        local_files_only=True,
    )
    output = []
    forced_language = None if payload.get("language") in {None, "", "Auto"} else payload["language"]
    for item in payload["chunks"]:
        result = model.transcribe(
            audio=item["path"], context=payload.get("context", ""),
            language=forced_language, return_time_stamps=False,
        )[0]
        output.append({**item, "text": result.text.strip(), "language": result.language or forced_language or "Unknown"})
    return {"chunks": output}


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
    output = []
    for item in payload["chunks"]:
        if not item.get("text"):
            output.append({**item, "words": []})
            continue
        result = model.align(audio=item["path"], text=item["text"], language=item["language"])[0]
        words = [
            {"text": token.text, "start": round(item["start"] + token.start_time, 3),
             "end": round(item["start"] + token.end_time, 3)}
            for token in result
        ]
        output.append({**item, "words": words})
    return {"chunks": output}


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
