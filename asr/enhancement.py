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
PROCESSING_VERSION = 2
CROSSFADE_SAMPLES = 800
SPEECH_HEAD_SECONDS = .2
SPEECH_TAIL_SECONDS = .4
MIN_PAUSE_SECONDS = .3
PAUSE_RAMP_SECONDS = .1
MAX_PAUSE_ATTENUATION_DB = 6.0


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
                 wet_ratio: float = WET_RATIO, enable_gain: bool = True,
                 include_waveform: bool = True) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    started = time.monotonic()
    model = asr_enhancement_model()
    metadata: dict[str, Any] = {"mode": "auto", "status": "bypassed", "reason": "clean",
        "model": model["name"], "model_revision": model["revision"], "compute_device": "cpu",
        "max_gain_db": 0.0, "denoised_seconds": 0.0, "gain_seconds": 0.0,
        "wet_ratio": wet_ratio, "processing_version": PROCESSING_VERSION}
    noisy_regions: list[dict[str, float]] = []
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
            previous_tail = None
            previous_changed = False
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
                    changed = stats["denoised"] or stats["gain_db"] > .01
                    begin_write = start
                    half = CROSSFADE_SAMPLES // 2
                    if previous_tail is not None:
                        overlap_begin, overlap_end = start - half, min(total, start + half)
                        head = processed[overlap_begin - left:overlap_end - left]
                        if changed or previous_changed:
                            ramp = np.linspace(0, 1, len(head), dtype=np.float32)
                            overlap = np.clip(previous_tail * (1 - ramp) + head * ramp, -CEILING, CEILING)
                        else:
                            # Exact bypass, including original peaks and PCM samples.
                            overlap = head
                        writer.write(overlap)
                        begin_write = overlap_end
                    end_write = end - half if end < total else end
                    writer.write(processed[begin_write - left:max(begin_write, end_write) - left])
                    previous_tail = (processed[end - half - left:min(total, end + half) - left].copy()
                                     if end < total else None)
                    previous_changed = changed
                    seconds = (end - start) / RATE
                    if stats["denoised"]:
                        metadata["denoised_seconds"] += seconds
                        noisy_regions.append({"start": start / RATE, "end": end / RATE})
                    if stats["gain_db"] > .01:
                        metadata["gain_seconds"] += seconds
                    metadata["max_gain_db"] = max(metadata["max_gain_db"], stats["gain_db"])
                    report(end, total)
            if metadata["denoised_seconds"] or metadata["gain_seconds"]:
                metadata.update(status="applied", reason="noise_and_gain" if metadata["denoised_seconds"] and metadata["gain_seconds"] else "noise" if metadata["denoised_seconds"] else "quiet_speech")
                temporary.replace(target)
                if include_waveform:
                    metadata["waveform"] = _waveform(target)
        metadata["_noisy_regions"] = merge_regions(noisy_regions)
        metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return metadata
    finally:
        temporary.unlink(missing_ok=True)


def _waveform(path: Path) -> list[float]:
    # Waveform generation is optional; always call after the final signal pass.
    try:
        from audio_intel.waveforms import retrieve
        return retrieve(path)["waveform"]
    except Exception:
        return []


def pause_regions(regions: list[dict[str, float]], noisy_regions: list[dict[str, float]],
                  duration: float) -> list[dict[str, float]]:
    """Conservative gaps in the union of both VAD passes, restricted to noisy blocks."""
    if not regions or not noisy_regions:
        return []
    protected = merge_regions([
        {"start": max(0.0, float(item["start"]) - SPEECH_HEAD_SECONDS),
         "end": min(duration, float(item["end"]) + SPEECH_TAIL_SECONDS)}
        for item in regions
        if math.isfinite(float(item["start"])) and math.isfinite(float(item["end"]))
        and float(item["end"]) > float(item["start"])
        and float(item["end"]) > 0 and float(item["start"]) < duration
    ])
    if not protected:
        return []
    gaps = []
    cursor = 0.0
    for item in protected:
        if item["start"] > cursor:
            gaps.append({"start": cursor, "end": item["start"]})
        cursor = max(cursor, item["end"])
    if cursor < duration:
        gaps.append({"start": cursor, "end": duration})
    pauses = []
    for gap in gaps:
        for noise in noisy_regions:
            start, end = max(gap["start"], noise["start"]), min(gap["end"], noise["end"])
            if end - start >= MIN_PAUSE_SECONDS - 1e-9:
                pauses.append({"start": start, "end": end})
    return merge_regions(pauses)


def suppress_pauses(path: Path, regions: list[dict[str, float]], noisy_regions: list[dict[str, float]],
                    report: Callable[[int, int], None]) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    stats = {"pause_attenuated_seconds": 0.0, "max_pause_attenuation_db": 0.0,
             "pause_suppression_reason": "no_long_pauses" if regions and noisy_regions
             else "no_speech" if not regions else "no_noise"}
    temporary = path.with_suffix(".pause.partial.wav")
    try:
        with sf.SoundFile(path) as reader:
            if reader.samplerate != RATE or reader.channels != 1:
                raise ValueError("Pause suppression requires normalized mono audio")
            total = len(reader)
            pauses = pause_regions(regions, noisy_regions, total / RATE)
            if not pauses:
                report(total, total)
                return stats
            if shutil.disk_usage(path.parent).free < settings.min_free_disk_bytes + total * 2 + 4096:
                raise OSError("Insufficient disk space for pause suppression")
            intervals = [(round(x["start"] * RATE), round(x["end"] * RATE)) for x in pauses]
            attenuated = 0
            maximum = 0.0
            with sf.SoundFile(temporary, "w", samplerate=RATE, channels=1, subtype="PCM_16") as writer:
                for start in range(0, total, BLOCK_SECONDS * RATE):
                    audio = reader.read(min(BLOCK_SECONDS * RATE, total - start), dtype="float32")
                    attenuation = np.zeros(len(audio), dtype=np.float32)
                    for begin, end in intervals:
                        left, right = max(start, begin), min(start + len(audio), end)
                        if left >= right:
                            continue
                        positions = np.arange(left, right, dtype=np.float64)
                        fraction = np.minimum(np.minimum((positions - begin) / (PAUSE_RAMP_SECONDS * RATE),
                                                         (end - 1 - positions) / (PAUSE_RAMP_SECONDS * RATE)), 1.0)
                        curve = MAX_PAUSE_ATTENUATION_DB * (.5 - .5 * np.cos(np.pi * np.maximum(0, fraction)))
                        attenuation[left - start:right - start] = curve
                    changed = attenuation > 1e-6
                    audio[changed] *= np.power(10.0, -attenuation[changed] / 20)
                    if not np.isfinite(audio).all():
                        raise ValueError("Invalid pause suppression output")
                    attenuated += int(np.count_nonzero(changed))
                    maximum = max(maximum, float(np.max(attenuation, initial=0)))
                    writer.write(audio)
                    report(start + len(audio), total)
        # Close both handles before replacement, including on native Windows.
        temporary.replace(path)
        stats.update(pause_attenuated_seconds=attenuated / RATE, max_pause_attenuation_db=maximum,
                     pause_suppression_reason="applied")
        return stats
    finally:
        temporary.unlink(missing_ok=True)


def enhance_audio(source: Path, target: Path, regions: list[dict[str, float]],
                  report: Callable[[int, int], None], vad_report: Callable[[int, int], None],
                  pause_report: Callable[[int, int], None], *, denoiser: Callable | None = None,
                  wet_ratio: float = WET_RATIO, enable_gain: bool = True,
                  enable_pauses: bool = True, speech_detector: Callable | None = None) -> dict[str, Any]:
    """Shared production/benchmark order: enhancement, VAD union, protected pauses, waveform."""
    completed = False
    try:
        result = enhance_file(source, target, regions, report, denoiser=denoiser,
                              wet_ratio=wet_ratio, enable_gain=enable_gain, include_waveform=False)
        noisy = result.pop("_noisy_regions")
        result.update(pause_attenuated_seconds=0.0, max_pause_attenuation_db=0.0,
                      pause_suppression_reason="disabled" if not enable_pauses else "bypassed")
        if result["status"] == "applied":
            detector = speech_detector or enhanced_speech_regions
            try:
                detected = detector(target, vad_report)
                combined = merge_regions(regions, detected)
            except (OSError, ValueError, RuntimeError):
                combined = regions
                result["pause_suppression_reason"] = "vad_unavailable"
            else:
                if enable_pauses:
                    started = time.monotonic()
                    result.update(suppress_pauses(target, combined, noisy, pause_report))
                    result["elapsed_seconds"] += time.monotonic() - started
            result["speech_regions"] = combined
            started = time.monotonic()
            result["waveform"] = _waveform(target)
            result["elapsed_seconds"] += time.monotonic() - started
        result["elapsed_seconds"] = round(result["elapsed_seconds"], 3)
        completed = True
        return result
    finally:
        if not completed:
            target.unlink(missing_ok=True)


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
