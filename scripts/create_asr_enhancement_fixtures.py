#!/usr/bin/env python3
"""Generate reproducible public speech/noise controls in the separate TTS runtime."""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from audio_intel.config import settings
from audio_intel.model_registry import resolve_tts_model, resolve_tts_checkpoint, model_installation


PHRASES = [
    ("Vivian", "我们在火车站服务台见面，今天下午三点半出发，请带好身份证和车票。"),
    ("Dylan", "咖啡厅的位置在地铁站附近，我会提前十分钟到达，到了以后给你打电话。"),
    ("Vivian", "请确认明天会议的时间和地点，测试结果将在星期五上午发给大家。"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", choices=["cpu", "gpu"], default=settings.default_compute_device)
    parser.add_argument("--speech-dir", type=Path, help="Reuse the three public phrase WAVs instead of generating speech")
    parser.add_argument("--extended", action="store_true", help="Include long/short pauses, hum, transients and overlapping speakers")
    args = parser.parse_args()
    target = args.output_dir.resolve()
    if not target.is_relative_to(ROOT / "testdata/real"):
        parser.error("Generated speech must stay under ignored testdata/real/")
    def forbid_network(event, values):
        if event == "socket.connect" and values[0].family in {socket.AF_INET, socket.AF_INET6}:
            raise RuntimeError("Network disabled during fixture generation")
    sys.addaudithook(forbid_network)
    if not args.speech_dir and args.device == "gpu" and os.environ.get("AUDIO_INTEL_ENHANCEMENT_FIXTURE_LEASE") != "1":
        from audio_intel.gpu import gpu_lease
        from asr.pipeline import _stop_stage_process
        with gpu_lease():
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                env={**os.environ, "AUDIO_INTEL_ENHANCEMENT_FIXTURE_LEASE": "1"})
            try:
                raise SystemExit(child.wait())
            finally:
                if child.poll() is None:
                    _stop_stage_process(child)
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly, lfilter
    from tts.pipeline import load_model, _sequential_speech_decode

    model = None
    if not args.speech_dir:
        definition = resolve_tts_model("qwen3-tts-0.6b")
        checkpoint = resolve_tts_checkpoint(definition, "preset")
        if not model_installation(settings.models_dir, checkpoint)["installed"]:
            raise RuntimeError("Pinned TTS preset model is unavailable")
        torch.manual_seed(20810)
        model = load_model(definition, "preset", args.device)
    target.mkdir(parents=True, exist_ok=True)
    parts = []
    for index, (speaker, text) in enumerate(PHRASES):
        if args.speech_dir:
            audio, rate = sf.read(args.speech_dir / f"phrase-{index + 1:02d}.wav", dtype="float32")
            if rate != 16000 or audio.ndim != 1:
                raise ValueError("Public phrase inputs must be normalized mono audio")
        else:
            with _sequential_speech_decode(model):
                waves, rate = model.generate_custom_voice(text=text, language="Chinese", speaker=speaker, max_new_tokens=512)
            audio = resample_poly(np.asarray(waves[0], np.float32), 16000, rate).astype(np.float32)
        if not len(audio) or not np.isfinite(audio).all():
            raise RuntimeError("Invalid fixture speech")
        rms, peak = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))), float(np.max(np.abs(audio)))
        audio *= min(.1 / max(rms, 1e-10), .8 / max(peak, 1e-10))
        sf.write(target / f"phrase-{index + 1:02d}.wav", audio, 16000, subtype="PCM_16")
        parts.extend([audio, np.zeros(8000, np.float32)])
    clean = np.concatenate(parts)
    noise = lfilter([1], [1, -.65], np.random.default_rng(20810).normal(size=len(clean))).astype(np.float32)
    noise *= float(np.sqrt(np.mean(clean ** 2))) / float(np.sqrt(np.mean(noise ** 2)))
    variants = {"clean": clean, "quiet": clean * .04, "noisy": (clean + noise) * .2,
                "noisy_quiet": (clean + noise * 10 ** (-5 / 20)) * .04}
    cases = []
    for name, values in variants.items():
        values = values.copy()
        if name in {"quiet", "noisy_quiet"}:
            values[len(values) // 2] = .94
        path = target / (name + ".wav")
        sf.write(path, values, 16000, subtype="PCM_16")
        cases.append({"id": name, "path": str(path), "reference": "".join(text for _, text in PHRASES)})
    if args.extended:
        # Keep clean references and exact phrase ranges for signal/ASR measurements.
        phrases = parts[::2]
        speech = np.concatenate([np.zeros(32000, np.float32), phrases[0], np.zeros(48000, np.float32),
                                 phrases[1], np.zeros(12800, np.float32), phrases[2], np.zeros(48000, np.float32)])
        positions = []
        cursor = 32000
        for phrase, gap in zip(phrases, [48000, 12800, 48000], strict=True):
            positions.append({"start": cursor / 16000, "end": (cursor + len(phrase)) / 16000})
            cursor += len(phrase) + gap
        generator = np.random.default_rng(20811)
        hiss = lfilter([1], [1, -.65], generator.normal(size=len(speech))).astype(np.float32)
        hiss /= max(float(np.sqrt(np.mean(hiss.astype(np.float64) ** 2))), 1e-10)
        clock = np.arange(len(speech)) / 16000
        hum = (np.sin(2 * np.pi * 50 * clock) + .3 * np.sin(2 * np.pi * 150 * clock)).astype(np.float32)
        hum /= max(float(np.sqrt(np.mean(hum.astype(np.float64) ** 2))), 1e-10)
        clicks = np.zeros(len(speech), np.float32)
        for second in [1.2, 4.2, 9.1, 16.7, len(speech) / 16000 - 1.5]:
            begin = int(second * 16000)
            count = min(1600, len(clicks) - begin)
            if count > 0:
                clicks[begin:begin + count] += generator.normal(0, 1, count) * np.exp(-np.arange(count) / 180)
        extended = {
            "pauses_clean": (speech, speech),
            "pauses_quiet": (speech * .04, speech * .04),
            "pauses_noise": ((speech + hiss * .035) * .2, speech * .2),
            "pauses_noise_quiet": ((speech + hiss * .035) * .04, speech * .04),
            "hum": ((speech + hum * .035) * .2, speech * .2),
            "transients": ((speech + hiss * .012 + clicks * .15) * .2, speech * .2),
        }
        # A second speaker overlaps the middle phrase; preservation is checked by
        # clean-reference audio and local listening rather than an ordered text CER.
        overlap = speech.copy()
        begin = round(positions[1]["start"] * 16000)
        count = min(len(phrases[0]), len(overlap) - begin)
        overlap[begin:begin + count] += phrases[0][:count] * .6
        extended["overlap_noise"] = ((overlap + hiss * .02) * .2, overlap * .2)
        for name, (values, reference_audio) in extended.items():
            path, reference_path = target / (name + ".wav"), target / (name + "-clean.wav")
            sf.write(path, values, 16000, subtype="PCM_16")
            sf.write(reference_path, reference_audio, 16000, subtype="PCM_16")
            case = {"id": name, "path": str(path), "clean_path": str(reference_path), "speech_regions": positions}
            if name != "overlap_noise":
                case["reference"] = "".join(text for _, text in PHRASES)
            cases.append(case)
    (target / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(cases), "duration_seconds": len(clean) / 16000, "cases_json": str(target / "cases.json")}))


if __name__ == "__main__":
    main()
