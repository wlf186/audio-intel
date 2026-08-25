from __future__ import annotations

from pathlib import Path


def extract_audio_clip(source: Path, target: Path, start: float, end: float) -> float:
    """Decode an exact time range to a project-owned 16 kHz mono PCM WAV."""
    if start < 0 or end <= start:
        raise ValueError("Invalid audio clip range")
    import av
    import numpy as np
    import soundfile as sf

    samples = []
    rate = 16000
    with av.open(str(source)) as container:
        if not container.streams.audio:
            raise ValueError("Source contains no audio stream")
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
        container.seek(int(max(0.0, start - 1.0) * av.time_base), backward=True)
        for frame in container.decode(stream):
            frame_start = float(frame.time or 0.0)
            frame_end = frame_start + float(frame.samples) / float(frame.sample_rate)
            if frame_end <= start:
                continue
            if frame_start >= end:
                break
            for converted in resampler.resample(frame):
                converted_start = float(converted.time if converted.time is not None else frame_start)
                data = converted.to_ndarray().reshape(-1)
                left = max(0, int(round((start - converted_start) * rate)))
                right = min(len(data), int(round((end - converted_start) * rate)))
                if right > left:
                    samples.append(data[left:right])
        for converted in resampler.resample(None):
            converted_start = float(converted.time or end)
            data = converted.to_ndarray().reshape(-1)
            left = max(0, int(round((start - converted_start) * rate)))
            right = min(len(data), int(round((end - converted_start) * rate)))
            if right > left:
                samples.append(data[left:right])
    if not samples:
        raise ValueError("Selected range contains no decodable audio")
    audio = np.concatenate(samples).astype(np.float32, copy=False)
    expected = max(1, int(round((end - start) * rate)))
    audio = audio[:expected]
    target.parent.mkdir(parents=True, exist_ok=True)
    sf.write(target, audio, rate, subtype="PCM_16")
    return len(audio) / rate

