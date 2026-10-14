"""Bounded-memory, offline speech enhancement without changing the media clock."""
from __future__ import annotations

import math
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from audio_intel.config import settings
from audio_intel.model_registry import asr_enhancement_model, model_installation


RATE = 16000
BLOCK_SECONDS = 30
CONTEXT_SECONDS = 1
FRAME_SAMPLES = 320
TARGET_RMS_DBFS = -24.0
MAX_GAIN_DB = 24.0
CEILING = 10 ** (-1 / 20)
NOISE_RATIO_THRESHOLD = 0.12
WET_RATIO = 0.5


def merge_regions(*groups: list[dict[str, float]]) -> list[dict[str, float]]:
    intervals = sorted((float(x["start"]), float(x["end"])) for group in groups for x in group)
    result: list[dict[str, float]] = []
    for start, end in intervals:
        if end <= start:
            continue
        if result and start <= result[-1]["end"]:
            result[-1]["end"] = max(result[-1]["end"], end)
        else:
            result.append({"start": start, "end": end})
    return result


def load_denoiser() -> Callable[[Any], Any]:
    import numpy as np
    import torch
    from .gtcrn import GTCRN

    model_info = asr_enhancement_model()
    if not model_installation(settings.models_dir, model_info)["installed"]:
        raise FileNotFoundError("Enhancement model revision or checksum is unavailable")
    # This stage has its own process: thread limits never affect diarization or ASR.
    torch.set_num_threads(2)
    model = GTCRN().eval()
    checkpoint = settings.models_dir / model_info["name"] / model_info["required_files"][0]
    model.load_state_dict(torch.load(checkpoint, map_location="cpu", weights_only=True)["model"])
    window = torch.hann_window(512).sqrt()

    def denoise(audio: Any) -> Any:
        if len(audio) < 512:
            return audio.copy()
        # Stable input scale for quiet microphones; restore it before dry/wet mixing.
        rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
        peak = float(np.max(np.abs(audio)))
        gain = min(10 ** (MAX_GAIN_DB / 20), max(1.0, .063 / max(rms, 1e-10)),
                   max(1.0, .7 / max(peak, 1e-10)))
        with torch.inference_mode():
            tensor = torch.from_numpy(np.ascontiguousarray(audio * gain, dtype=np.float32))
            features = torch.view_as_real(torch.stft(tensor, 512, 256, 512, window, return_complex=True))
            output = model(features[None])[0]
            enhanced = torch.istft(torch.view_as_complex(output.contiguous()), 512, 256, 512,
                                  window, length=len(audio)).numpy() / gain
        return enhanced

    return denoise


def enhance_block(audio: Any, denoised: Any, speech: Any, *, wet_ratio: float = WET_RATIO,
                  enable_gain: bool = True) -> tuple[Any, dict[str, Any]]:
    import numpy as np

    if len(denoised) != len(audio) or not np.isfinite(denoised).all():
        raise ValueError("Invalid enhancement output")
    nframes = math.ceil(len(audio) / FRAME_SAMPLES)
    padded = np.pad(audio, (0, nframes * FRAME_SAMPLES - len(audio)))
    frames = padded.reshape(nframes, FRAME_SAMPLES)
    raw_rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    speech = np.asarray(speech, dtype=bool)
    if speech.shape != (nframes,):
        raise ValueError("Invalid speech mask")
    energy = float(np.sum(audio.astype(np.float64) ** 2))
    residual = float(np.sum((audio.astype(np.float64) - denoised.astype(np.float64)) ** 2))
    noise_ratio = residual / max(energy, 1e-20)
    noisy = bool(noise_ratio >= NOISE_RATIO_THRESHOLD and energy > 1e-12)
    output = ((1 - wet_ratio) * audio + wet_ratio * denoised) if noisy else audio.copy()
    out_frames = np.pad(output, (0, nframes * FRAME_SAMPLES - len(output))).reshape(nframes, FRAME_SAMPLES)
    rms = np.sqrt(np.mean(out_frames.astype(np.float64) ** 2, axis=1))
    gain_db = np.zeros(nframes)
    # One-second speech RMS estimates keep vowel/consonant dynamics intact.
    for begin in range(0, nframes, 50):
        end = min(begin + 50, nframes)
        context_begin, context_end = max(0, begin - 50), min(nframes, end + 50)
        supported = speech[context_begin:context_end] & (raw_rms[context_begin:context_end] > 1e-4)
        if not supported.any():
            continue
        context_rms = float(np.quantile(rms[context_begin:context_end][supported], .7))
        voiced = speech[begin:end] & (raw_rms[begin:end] > max(1e-4, context_rms / 20))
        if np.count_nonzero(voiced) < 10:
            continue
        level = 20 * math.log10(max(float(np.quantile(rms[begin:end][voiced], .7)), 1e-10))
        # An audible recording is left untouched; gain is upward only.
        if enable_gain and level < -30 and context_rms < 10 ** (-30 / 20):
            gain_db[begin:end] = np.where(voiced, min(MAX_GAIN_DB, max(0.0, TARGET_RMS_DBFS - level)), 0)
    positions = np.arange(-20, 21, dtype=np.float64)
    kernel = np.exp(-.5 * (positions / 5) ** 2)
    kernel /= np.sum(kernel)
    gain_db = np.convolve(np.pad(gain_db, (20, 20), mode="edge"), kernel, mode="valid")
    gain = np.interp(np.arange(len(audio)), np.arange(nframes) * FRAME_SAMPLES + FRAME_SAMPLES / 2,
                     10 ** (gain_db / 20)).astype(np.float32)
    if not noisy and not np.any(gain_db > .01):
        return audio.copy(), {"denoised": False, "gain_db": 0.0, "noise_ratio": noise_ratio}
    # A local lookahead peak ceiling handles transient knocks without globally
    # reducing the gain of a multi-hour recording.
    # Linear-time sliding maximum using prefix/suffix maxima of 161-sample
    # blocks. This keeps the pure signal-control tests in the API environment.
    padded_peaks = np.pad(np.abs(output), (80, 80), mode="edge")
    padded_peaks = np.pad(padded_peaks, (0, (-len(padded_peaks)) % 161), mode="edge")
    peak_blocks = padded_peaks.reshape(-1, 161)
    prefix = np.maximum.accumulate(peak_blocks, axis=1).reshape(-1)
    suffix = np.maximum.accumulate(peak_blocks[:, ::-1], axis=1)[:, ::-1].reshape(-1)
    peaks = np.maximum(suffix[:len(output)], prefix[160:160 + len(output)])
    gain = np.minimum(gain, CEILING / np.maximum(peaks, 1e-10))
    output = np.clip(output * gain, -CEILING, CEILING).astype(np.float32)
    applied_gain = min(MAX_GAIN_DB, max(0.0, float(20 * np.log10(max(float(np.max(gain)), 1e-10)))))
    return output, {"denoised": noisy, "gain_db": applied_gain, "noise_ratio": noise_ratio}


def enhance_file(source: Path, target: Path, regions: list[dict[str, float]],
                 report: Callable[[int, int], None], *, denoiser: Callable | None = None,
                 wet_ratio: float = WET_RATIO, enable_gain: bool = True) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    started = time.monotonic()
    model = asr_enhancement_model()
    metadata: dict[str, Any] = {"mode": "auto", "status": "bypassed", "reason": "clean",
        "model": model["name"], "model_revision": model["revision"], "compute_device": "cpu",
        "max_gain_db": 0.0, "denoised_seconds": 0.0, "gain_seconds": 0.0,
        "wet_ratio": wet_ratio}
    temporary = target.with_suffix(".partial.wav")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sf.SoundFile(source) as reader:
            if reader.samplerate != RATE or reader.channels != 1:
                raise ValueError("Enhancement requires normalized 16 kHz mono audio")
            total = len(reader)
            if shutil.disk_usage(target.parent).free < settings.min_free_disk_bytes + total * 2 + 4096:
                raise OSError("Insufficient disk space for enhanced playback")
            run = denoiser if denoiser is not None else load_denoiser()
            metadata["input_samples"] = total
            with sf.SoundFile(temporary, "w", samplerate=RATE, channels=1, subtype="PCM_16") as writer:
                for start in range(0, total, BLOCK_SECONDS * RATE):
                    end = min(total, start + BLOCK_SECONDS * RATE)
                    left, right = max(0, start - CONTEXT_SECONDS * RATE), min(total, end + CONTEXT_SECONDS * RATE)
                    reader.seek(left)
                    original = reader.read(right - left, dtype="float32")
                    denoised = run(original)
                    count = math.ceil(len(original) / FRAME_SAMPLES)
                    mask = np.zeros(count, dtype=bool)
                    for region in regions:
                        begin = max(0, math.floor((region["start"] * RATE - left) / FRAME_SAMPLES))
                        finish = min(count, math.ceil((region["end"] * RATE - left) / FRAME_SAMPLES))
                        if finish > begin:
                            mask[begin:finish] = True
                    processed, stats = enhance_block(original, denoised, mask, wet_ratio=wet_ratio, enable_gain=enable_gain)
                    core = processed[start - left:end - left].copy()
                    # Blend a short margin towards the original at processing
                    # block boundaries, avoiding a discontinuity when modes change.
                    margin = min(800, len(core) // 2)
                    if margin and (stats["denoised"] or stats["gain_db"] > .01):
                        raw_core = original[start - left:end - left]
                        ramp = np.linspace(0, 1, margin, dtype=np.float32)
                        if start:
                            core[:margin] = raw_core[:margin] * (1 - ramp) + core[:margin] * ramp
                        if end < total:
                            core[-margin:] = core[-margin:] * (1 - ramp) + raw_core[-margin:] * ramp
                    writer.write(core)
                    seconds = (end - start) / RATE
                    if stats["denoised"]:
                        metadata["denoised_seconds"] += seconds
                    if stats["gain_db"] > .01:
                        metadata["gain_seconds"] += seconds
                    metadata["max_gain_db"] = max(metadata["max_gain_db"], stats["gain_db"])
                    report(end, total)
            if metadata["denoised_seconds"] or metadata["gain_seconds"]:
                metadata.update(status="applied", reason="noise_and_gain" if metadata["denoised_seconds"] and metadata["gain_seconds"] else "noise" if metadata["denoised_seconds"] else "quiet_speech")
                temporary.replace(target)
                # Optional waveform failure must never fail inference.
                try:
                    from audio_intel.waveforms import retrieve
                    metadata["waveform"] = retrieve(target)["waveform"]
                except Exception:
                    metadata["waveform"] = []
        metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)


def enhanced_speech_regions(path: Path, report: Callable[[int, int], None] | None = None) -> list[dict[str, float]]:
    """Second VAD pass uses bounded windows and absolute media-clock offsets."""
    import soundfile as sf
    from funasr import AutoModel

    model = AutoModel(model=str(settings.models_dir / "FSMN-VAD"), device="cpu",
                      disable_update=True, disable_pbar=True)
    regions = []
    with sf.SoundFile(path) as reader:
        total = len(reader)
        for start in range(0, total, 60 * RATE):
            left = max(0, start - RATE)
            reader.seek(left)
            samples = reader.read(min(total - left, 62 * RATE), dtype="float32")
            result = model.generate(input=samples, fs=RATE)
            raw = (result[0].get("value") or result[0].get("timestamp") or []) if result else []
            regions.extend({"start": left / RATE + float(x[0]) / 1000,
                            "end": min(total / RATE, left / RATE + float(x[1]) / 1000)} for x in raw)
            if report is not None:
                report(min(total, start + 60 * RATE), total)
    return merge_regions(regions)
