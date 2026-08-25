#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DIALOGUE = [
    ("A", "Vivian", "大家先看一下今天的议程。"),
    ("B", "Dylan", "我先说结论，可以吗？"),
    ("C", "Uncle_Fu", "可以，你先来。"),
    ("A", "Vivian", "等等，预算数字还没确认。"),
    ("B", "Dylan", "我刚核过，是四十八万。"),
    ("C", "Uncle_Fu", "包含测试费用吗？"),
    ("B", "Dylan", "包含，但不含差旅。"),
    ("A", "Vivian", "那上线时间要不要顺延？"),
    ("C", "Uncle_Fu", "不用，我这边能按周五交付。"),
    ("B", "Dylan", "接口也能一起给吗？"),
    ("C", "Uncle_Fu", "接口文档今晚发。"),
    ("A", "Vivian", "好，那风险还有什么？"),
    ("B", "Dylan", "供应商可能晚一天。"),
    ("C", "Uncle_Fu", "我来跟他们确认。"),
    ("A", "Vivian", "行，下午三点再同步。"),
]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def synthesize(output: Path, compute_device: str) -> None:
    import numpy as np
    import soundfile as sf
    import torch

    from tts.pipeline import _sequential_speech_decode, load_model

    output.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(20810)
    np.random.seed(20810)
    print(f"Loading TTS model on {compute_device}...", flush=True)
    model = load_model("preset", compute_device)
    waveforms = []
    rate = 24000
    for base in range(0, len(DIALOGUE), 2):
        batch = DIALOGUE[base:base + 2]
        with _sequential_speech_decode(model):
            generated, item_rate = model.generate_custom_voice(
                text=[item[2] for item in batch],
                language=["Chinese"] * len(batch),
                speaker=[item[1] for item in batch],
                instruct=["语速自然，像会议中简短发言。"] * len(batch),
                non_streaming_mode=True,
            )
        if item_rate != rate:
            raise RuntimeError(f"Unexpected sample rate: {item_rate}")
        waveforms.extend(np.asarray(item, dtype=np.float32) for item in generated)
        print(f"Synthesized {min(base + 2, len(DIALOGUE))}/{len(DIALOGUE)} turns", flush=True)

    def compose(name: str, count: int, overlap: bool = False, fixed_gap: float | None = None) -> None:
        starts = []
        cursor = 0
        overlap_indices = {2, 5, 8, 11, 14}
        for index, waveform in enumerate(waveforms[:count]):
            if index == 0:
                start = 0
            elif overlap and index in overlap_indices:
                start = max(0, cursor - int(0.32 * rate))
            else:
                gap = fixed_gap if fixed_gap is not None else (0.10, 0.18, 0.25)[(index - 1) % 3]
                start = cursor + int(gap * rate)
            starts.append(start)
            cursor = max(cursor, start + len(waveform))

        mixed = np.zeros(cursor, dtype=np.float32)
        reference = []
        for index, (start, waveform) in enumerate(zip(starts, waveforms[:count])):
            mixed[start:start + len(waveform)] += waveform
            speaker, voice, text = DIALOGUE[index]
            reference.append({
                "speaker": speaker,
                "voice": voice,
                "text": text,
                "start": round(start / rate, 3),
                "end": round((start + len(waveform)) / rate, 3),
            })
        peak = float(np.max(np.abs(mixed)))
        if peak > 0.98:
            mixed *= 0.98 / peak
        sf.write(output / f"{name}.wav", mixed, rate, subtype="PCM_16")
        write_json(output / f"{name}.reference.json", reference)
        print(f"Wrote {name}.wav ({len(mixed) / rate:.2f}s)", flush=True)

    compose("rapid-clean", len(DIALOGUE))
    compose("short-known3", 4, fixed_gap=0.12)
    compose("rapid-overlap", len(DIALOGUE), overlap=True)
    write_json(output / "manifest.json", {"sample_rate": rate, "voices": ["Vivian", "Dylan", "Uncle_Fu"]})


def score_result(reference: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    predicted = result["segments"]
    reference_speakers = sorted({item["speaker"] for item in reference})
    predicted_speakers = sorted({item["speaker"] for item in predicted})
    end = max([item["end"] for item in reference] + [item["end"] for item in predicted])
    times = np.arange(0.0, end, 0.01)
    overlap = np.zeros((len(predicted_speakers), len(reference_speakers)))
    for timestamp in times:
        active_reference = [item["speaker"] for item in reference if item["start"] <= timestamp < item["end"]]
        active_prediction = [item["speaker"] for item in predicted if item["start"] <= timestamp < item["end"]]
        for predicted_speaker in active_prediction[:1]:
            for reference_speaker in active_reference:
                overlap[predicted_speakers.index(predicted_speaker), reference_speakers.index(reference_speaker)] += 0.01

    mapping = {}
    if predicted_speakers and reference_speakers:
        rows, columns = linear_sum_assignment(-overlap)
        mapping = {predicted_speakers[row]: reference_speakers[column] for row, column in zip(rows, columns)}

    single_speaker_frames = 0
    correct_frames = 0
    overlap_frames = 0
    for timestamp in times:
        active_reference = [item["speaker"] for item in reference if item["start"] <= timestamp < item["end"]]
        active_prediction = [item["speaker"] for item in predicted if item["start"] <= timestamp < item["end"]]
        if len(active_reference) == 1:
            single_speaker_frames += 1
            if active_prediction and mapping.get(active_prediction[0]) == active_reference[0]:
                correct_frames += 1
        elif len(active_reference) > 1:
            overlap_frames += 1

    reference_changes = [
        item["start"] for index, item in enumerate(reference)
        if index and item["speaker"] != reference[index - 1]["speaker"]
    ]
    predicted_changes = [
        item["start"] for index, item in enumerate(predicted)
        if index and item["speaker"] != predicted[index - 1]["speaker"]
    ]
    used_reference = set()
    boundary_hits = 0
    for predicted_change in predicted_changes:
        candidates = [
            (abs(predicted_change - reference_change), index)
            for index, reference_change in enumerate(reference_changes)
            if index not in used_reference and abs(predicted_change - reference_change) <= 0.5
        ]
        if candidates:
            _, index = min(candidates)
            used_reference.add(index)
            boundary_hits += 1
    precision = boundary_hits / len(predicted_changes) if predicted_changes else 0.0
    recall = boundary_hits / len(reference_changes) if reference_changes else 0.0
    boundary_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "speaker_mapping": mapping,
        "speaker_count": len(result.get("speakers", [])),
        "segment_count": len(predicted),
        "single_speaker_accuracy": round(correct_frames / max(single_speaker_frames, 1), 4),
        "turn_boundary_f1_500ms": round(boundary_f1, 4),
        "overlap_seconds": round(overlap_frames * 0.01, 3),
        "text_preserved": "".join(item["text"] for item in predicted) == result["text"],
    }


def evaluate(output: Path, compute_device: str) -> None:
    from audio_intel.config import settings
    from asr.pipeline import assemble, combine_vad, decode_audio, diarize, run_stage, run_vad, write_chunks

    cases = {
        "rapid-clean": [None, 3],
        "short-known3": [None, 3],
        "rapid-overlap": [3],
    }
    report = {}
    failures = []
    for name, speaker_counts in cases.items():
        work = output / "work" / name
        work.mkdir(parents=True, exist_ok=True)
        audio, rate = decode_audio(output / f"{name}.wav", work / "normalized.wav")
        duration = len(audio) / rate
        vad = run_vad(audio, rate)
        chunks = write_chunks(audio, rate, combine_vad(vad, duration), work / "chunks")
        transcribed = run_stage("transcribe", {
            "model_path": str(settings.models_dir / "Qwen3-ASR-0.6B"),
            "chunks": chunks,
            "language": "Chinese",
            "context": "",
            "compute_device": compute_device,
        }, work)["chunks"]
        aligned = run_stage("align", {
            "model_path": str(settings.models_dir / "Qwen3-ForcedAligner-0.6B"),
            "chunks": transcribed,
            "compute_device": compute_device,
        }, work)["chunks"]
        reference = json.loads((output / f"{name}.reference.json").read_text(encoding="utf-8"))
        for speaker_count in speaker_counts:
            mode = "auto" if speaker_count is None else f"known{speaker_count}"
            diarization = diarize(audio, vad, speaker_count, batch_size=16)
            result = assemble(aligned, diarization, duration, True)
            write_json(output / "results" / f"{name}-{mode}.json", result)
            metrics = score_result(reference, result)
            diagnostic = name == "rapid-overlap" or (name == "rapid-clean" and speaker_count is None)
            expected_segments = 14 if name == "rapid-clean" else 4
            passed = metrics["text_preserved"]
            if not diagnostic:
                passed = passed and metrics["speaker_count"] == 3
                passed = passed and metrics["segment_count"] >= expected_segments
                passed = passed and metrics["single_speaker_accuracy"] >= 0.90
                if name == "rapid-clean":
                    passed = passed and metrics["turn_boundary_f1_500ms"] >= 0.85
            metrics["diagnostic_only"] = diagnostic
            metrics["passed"] = passed
            report[f"{name}-{mode}"] = metrics
            if not passed:
                failures.append(f"{name}-{mode}")
            print(f"{name}-{mode}: {json.dumps(metrics, ensure_ascii=False)}", flush=True)
    write_json(output / "metrics.json", report)
    if failures:
        raise SystemExit(f"Diarization benchmark failed: {', '.join(failures)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and evaluate a local multi-speaker diarization corpus.")
    parser.add_argument("action", choices=("synthesize", "evaluate"))
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / "diarization-eval")
    parser.add_argument("--compute-device", choices=("cpu", "gpu"), default="gpu")
    args = parser.parse_args()
    if args.action == "synthesize":
        synthesize(args.output.resolve(), args.compute_device)
    else:
        evaluate(args.output.resolve(), args.compute_device)


if __name__ == "__main__":
    main()
