#!/usr/bin/env python3
"""Paired local-only enhancement/ASR checks. Private transcripts stay in the report."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from audio_intel.config import settings
from audio_intel.model_registry import asr_models, model_installation, resolve_asr_model


def forbid_network(event: str, args: tuple) -> None:
    if event == "socket.connect" and args[0].family in {socket.AF_INET, socket.AF_INET6}:
        raise RuntimeError("Network connections are disabled during local audio benchmarking")


def text_units(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def character_error_rate(reference: str, actual: str) -> float:
    target, observed = text_units(reference), text_units(actual)
    previous = list(range(len(observed) + 1))
    for i, expected in enumerate(target, 1):
        current = [i]
        for j, found in enumerate(observed, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (expected != found)))
        previous = current
    return previous[-1] / max(1, len(target))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-json", type=Path, help="Local JSON list of {id,path,start?,end?,reference?}")
    parser.add_argument("--job-id", action="append", default=[], help="Read a historical job without modifying it")
    parser.add_argument("--full", action="store_true", help="Use complete job audio instead of representative excerpts")
    parser.add_argument("--fixed-vad", action="store_true", help="Isolate signal processing by holding original VAD boundaries fixed")
    parser.add_argument("--baseline-report", type=Path, help="Frozen original/auto report with matching cases; reuse original transcription")
    parser.add_argument("--model", choices=[x["public_id"] for x in asr_models()], default="qwen3-asr-1.7b")
    parser.add_argument("--device", choices=["cpu", "gpu"], default=settings.default_compute_device)
    parser.add_argument("--modes", default="off,gain_only,denoise_only,wet25,auto,wet75")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    sys.addaudithook(forbid_network)
    if not args.cases_json and not args.job_id:
        parser.error("Supply --cases-json or --job-id")
    output = args.output_dir.resolve()
    if not any(output.is_relative_to(ROOT / directory) for directory in ("logs", "tmp", "testdata/real")):
        parser.error("Reports and sensitive audio must stay in ignored local logs/, tmp/, or testdata/real/")
    modes = args.modes.split(",")
    allowed = {"off", "gain_only", "denoise_only", "wet25", "auto", "wet75", "mix50", "mix60", "mix65", "mix75", "no_pauses", "pause_gate", "legacy_gain"}
    if modes[0] != "off" or not set(modes) <= allowed:
        parser.error("Modes must start with off and use the documented comparison modes")
    if args.device == "gpu" and os.environ.get("AUDIO_INTEL_ENHANCEMENT_BENCH_LEASE") != "1":
        from audio_intel.gpu import gpu_lease
        from asr.pipeline import _stop_stage_process
        # Hold the normal GPU lock until the inference child's complete exit.
        # Parent cancellation also terminates that child before releasing it.
        with gpu_lease():
            env = {**os.environ, "AUDIO_INTEL_ENHANCEMENT_BENCH_LEASE": "1"}
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], env=env)
            try:
                raise SystemExit(child.wait())
            finally:
                if child.poll() is None:
                    _stop_stage_process(child)
    cases = json.loads(args.cases_json.read_text(encoding="utf-8")) if args.cases_json else []
    if args.job_id:
        with closing(sqlite3.connect(settings.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            for index, jid in enumerate(args.job_id):
                row = db.execute("SELECT request_json,result_json FROM jobs WHERE id=? AND kind='asr'", (jid,)).fetchone()
                if row is None:
                    parser.error("Requested local ASR job is unavailable")
                request, result = json.loads(row[0]), json.loads(row[1] or "{}")
                path = settings.jobs_dir / jid / "output/playback.wav"
                if not path.is_file():
                    path = Path(request["input_path"])
                duration = float(result["duration"])
                windows = [(0, duration)] if args.full else [(max(0, duration * x - 15), min(duration, duration * x + 15)) for x in (.2, .5, .8)]
                cases.extend({"id": f"private-{index + 1}-{k + 1}", "path": str(path), "start": a, "end": b}
                             for k, (a, b) in enumerate(windows))
    # No media is read until all inference imports and network blocking are set.
    import numpy as np
    import soundfile as sf
    import torch
    from qwen_asr import Qwen3ASRModel
    from asr.enhancement import enhance_audio, load_denoiser
    from asr.pipeline import decode_audio, run_vad, combine_vad
    from audio_intel.media import extract_audio_clip

    selected = resolve_asr_model(args.model)
    if not model_installation(settings.models_dir, selected)["installed"]:
        raise RuntimeError("Pinned ASR model is unavailable")
    model = Qwen3ASRModel.from_pretrained(str(settings.models_dir / selected["name"]),
        dtype=torch.float32 if args.device == "cpu" else torch.bfloat16,
        device_map="cpu" if args.device == "cpu" else "cuda:0", attn_implementation="sdpa",
        local_files_only=True, max_inference_batch_size=1, max_new_tokens=512)
    denoiser = load_denoiser()
    frozen = json.loads(args.baseline_report.read_text(encoding="utf-8")) if args.baseline_report else None
    if frozen:
        if frozen["model"] != args.model or frozen["device"] != args.device or len(frozen["cases"]) != len(cases):
            parser.error("Baseline model, device and cases must match")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"model": args.model, "model_revision": selected["revision"],
                            "device": args.device, "network_disabled": True, "cases": []}
    for index, case in enumerate(cases):
        directory = output / f"case-{index + 1:03d}"
        directory.mkdir(exist_ok=True)
        source = directory / "original.wav"
        if "end" in case:
            extract_audio_clip(Path(case["path"]), source, float(case.get("start", 0)), float(case["end"]))
            audio, rate = sf.read(source, dtype="float32")
        else:
            audio, rate = decode_audio(Path(case["path"]), source)
        vad = run_vad(audio, rate)
        frozen_case = frozen["cases"][index] if frozen else None
        if frozen_case and (frozen_case["id"] != str(case.get("id", index + 1)) or
                            abs(frozen_case["duration"] - len(audio) / rate) > 1 / rate):
            raise ValueError("Frozen case identity or duration differs")
        if frozen_case:
            original = args.baseline_report.parent / f"case-{index + 1:03d}" / "original.wav"
            with source.open("rb") as candidate, original.open("rb") as previous:
                if hashlib.file_digest(candidate, "sha256").digest() != hashlib.file_digest(previous, "sha256").digest():
                    raise ValueError("Frozen normalized audio differs")
        entry: dict[str, Any] = {"id": str(case.get("id", index + 1)), "duration": len(audio) / rate,
            "has_reference": "reference" in case, "modes": {}}
        baseline = ""
        for mode in modes:
            if mode == "off" and frozen_case:
                item = frozen_case["modes"]["off"].copy()
                entry["modes"][mode] = item
                baseline = item["text"]
                continue
            target = source
            metadata = {"status": "off"}
            if mode != "off":
                target = directory / (mode + ".wav")
                ratio = {"wet25": .25, "wet75": .75, "mix50": .5, "mix60": .6,
                         "mix65": .65, "mix75": .75}.get(mode)
                controls = {"wet_ratio": ratio} if ratio is not None else {}
                metadata = enhance_audio(source, target, vad, lambda *_: None, lambda *_: None, lambda *_: None,
                    denoiser=(lambda x: x) if mode == "gain_only" else denoiser,
                    enable_gain=mode != "denoise_only", enable_pauses=mode == "pause_gate",
                    gain_policy="legacy" if mode == "legacy_gain" else "continuous",
                    **controls)
                if metadata["status"] != "applied":
                    target = source
            enhanced_vad = metadata.pop("speech_regions", vad)
            mode_vad = vad if args.fixed_vad else enhanced_vad
            mode_chunks = combine_vad(mode_vad, len(audio) / rate)
            started = time.monotonic()
            texts = []
            with sf.SoundFile(target) as reader:
                for chunk in mode_chunks:
                    # Use the production chunk rules; --fixed-vad isolates the signal arm.
                    left, right = int(chunk["start"] * rate), int(chunk["end"] * rate)
                    for begin in range(left, right, 30 * rate):
                        reader.seek(begin)
                        samples = reader.read(min(30 * rate, right - begin), dtype="float32")
                        prediction = model.transcribe(audio=(samples, rate), language=None)
                        texts.append(prediction[0].text)
            text = "".join(texts)
            if mode == "off":
                baseline = text
            item: dict[str, Any] = {"enhancement": metadata, "transcription_seconds": round(time.monotonic() - started, 3),
                "vad_regions": mode_vad,
                "text": text, "text_chars": len(text), "sample_count": sf.info(target).frames,
                "agreement_with_baseline": difflib.SequenceMatcher(None, text_units(baseline), text_units(text), autojunk=False).ratio()}
            if "reference" in case:
                item["cer"] = character_error_rate(case["reference"], text)
            if frozen_case:
                current = frozen_case["modes"]["auto"]
                item["agreement_with_current"] = difflib.SequenceMatcher(
                    None, text_units(current["text"]), text_units(text), autojunk=False).ratio()
                if "cer" in item and "cer" in current:
                    item["cer_change_from_current"] = item["cer"] - current["cer"]
            entry["modes"][mode] = item
        report["cases"].append(entry)
        path = output / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Only anonymous metrics reach stdout. Transcripts remain in the local report.
        print(json.dumps({"case": index + 1, "duration": entry["duration"], "has_reference": entry["has_reference"],
                          "metrics": {k: {field: value for field, value in v.items() if field in {"cer", "text_chars", "sample_count", "transcription_seconds", "agreement_with_baseline"}}
                                      for k, v in entry["modes"].items()}}, ensure_ascii=False), flush=True)
    print(json.dumps({"local_report": str(output / "report.json"), "cases": len(cases)}), flush=True)


if __name__ == "__main__":
    main()
