"""Offline presentation-frame analysis; sampled images stay in memory."""
from __future__ import annotations

import hashlib
import io
import math
import os
import time
from pathlib import Path
from typing import Any

import av
import numpy as np
from PIL import Image


def crop_picture(frame: Any, roi: dict[str, float] | None) -> Image.Image:
    picture = frame.to_image()
    rotation = int(getattr(frame, 'rotation', 0) or 0)
    if rotation:
        picture = picture.rotate(rotation, expand=True)
    if roi:
        width, height = picture.size
        picture = picture.crop((round(roi['x'] * width), round(roi['y'] * height),
                                round((roi['x'] + roi['width']) * width), round((roi['y'] + roi['height']) * height)))
    picture.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    return picture.convert('RGB')


def save_picture(picture: Image.Image, directory: Path) -> str:
    buffer = io.BytesIO()
    picture.save(buffer, format='JPEG', quality=85, optimize=False)
    payload = buffer.getvalue()
    name = hashlib.sha256(payload).hexdigest() + '.jpg'
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    if not target.exists():
        partial = directory / (name + '.partial')
        try:
            partial.write_bytes(payload)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
    return name


def features(picture: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    plane = np.asarray(picture.resize((320, 180)).convert('L'), dtype=np.float32) / 255
    edges = np.zeros_like(plane, dtype=bool)
    edges[:, 1:] |= np.abs(np.diff(plane, axis=1)) > .10
    edges[1:, :] |= np.abs(np.diff(plane, axis=0)) > .10
    small = np.asarray(picture.resize((32, 32)).convert('L'), dtype=np.float32)
    # Low-frequency DCT perceptual hash, using numpy already in both runtimes.
    transform = np.cos(np.pi / 32 * (np.arange(32) + .5)[None, :] * np.arange(8)[:, None])
    low = transform @ small @ transform.T
    hash_value = low.flatten()[1:] > np.median(low.flatten()[1:])
    return plane, edges, hash_value


def same_page(old: tuple, new: tuple) -> bool:
    old_plane, old_edges, old_hash = old
    new_plane, new_edges, new_hash = new
    changed = np.abs(old_plane - new_plane) > .12
    if float(changed.mean()) < .004:
        return True
    edge_count = int(old_edges.sum())
    if edge_count < 12:
        # Blank lead-in / fade-in adopts its first useful slide.
        return float(old_plane.std()) < .015
    # A one-pixel tolerance avoids treating rasterization noise as removed text.
    expanded = new_edges.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        expanded |= np.roll(new_edges, (dy, dx), axis=(0, 1))
    retained = float((old_edges & expanded).sum()) / edge_count
    incremental = retained >= .87 and new_edges.sum() >= edge_count * .94 and changed.mean() < .20
    near_duplicate = float((old_hash != new_hash).mean()) < .08 and retained >= .78 and changed.mean() < .025
    return bool(incremental or near_duplicate)


class PresentationDetector:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.scenes: list[dict[str, Any]] = []
        self.previous: tuple | None = None
        self.current: tuple | None = None
        self.picture: Image.Image | None = None
        self.frame_time = 0.0
        self.start = 0.0
        self.change_time = 0.0
        self.stable_since = 0.0
        self.graph: Any = None

    def feed(self, picture: Image.Image, timestamp: float) -> None:
        value = features(picture)
        # FFmpeg cuts complement small text/edge changes that scdet alone misses.
        from fractions import Fraction
        frame = av.VideoFrame.from_ndarray((value[0] * 255).astype(np.uint8), format='gray')
        frame.pts = round(timestamp * 1000)
        frame.time_base = Fraction(1, 1000)
        if self.graph is None:
            self.graph = av.filter.Graph()
            source = self.graph.add_buffer(template=frame)
            detector = self.graph.add('scdet', 'threshold=8:sc_pass=1')
            sink = self.graph.add('buffersink')
            source.link_to(detector)
            detector.link_to(sink)
            self.graph.configure()
        self.graph.push(frame)
        cut = False
        try:
            while True:
                self.graph.pull()
                cut = True
        except (BlockingIOError, EOFError):
            pass
        stable = self.previous is not None and float((np.abs(value[0] - self.previous[0]) > .12).mean()) < .008
        if not stable:
            self.stable_since = timestamp
        if self.current is None:
            self.current, self.picture, self.frame_time = value, picture, timestamp
        elif cut or not same_page(self.current, value):
            if self.change_time == 0:
                self.change_time = timestamp
            if stable and timestamp - self.stable_since >= .5:
                self.flush(self.change_time)
                self.start = self.change_time
                self.current, self.picture, self.frame_time = value, picture, timestamp
                self.change_time = 0
        elif stable:
            self.change_time = 0
            # Last stable full build of a slide is the representative frame.
            self.current, self.picture, self.frame_time = value, picture, timestamp
        self.previous = value

    def flush(self, end: float) -> None:
        if self.picture is None or end <= self.start:
            return
        if len(self.scenes) >= 2000:
            raise ValueError('Video exceeds 2000 presentation sections; split the source video')
        name = save_picture(self.picture, self.directory)
        self.scenes.append({'id': f'scene_{len(self.scenes) + 1}', 'start': self.start,
                            'end': end, 'frame_time': min(self.frame_time, end), 'image': name})


def probe(source: Path) -> dict[str, Any]:
    with av.open(str(source)) as container:
        videos = [s for s in container.streams.video if not (s.disposition & av.stream.Disposition.attached_pic)]
        if not videos or not container.streams.audio:
            raise ValueError('Video requires a decodable picture and audio track / 视频需包含画面和音轨')
        stream = videos[0]
        # Opening a decoder catches unavailable codecs before enqueueing.
        stream.codec_context.open()
        container.streams.audio[0].codec_context.open()
        return {'width': stream.width, 'height': stream.height, 'duration': (container.duration or 0) / av.time_base}


def decode(source: Path, normalized: Path, frame_directory: Path, roi: dict | None, context: Any,
           detection_settings: dict | None = None, detector_version: str | None = None) -> tuple[Any, int, dict]:
    import soundfile as sf

    detector = PresentationDetector(frame_directory)
    writer = None
    if detection_settings is not None:
        from .video_detection import Detector, FeatureWriter, thumbnail
        detector = Detector(detection_settings)
        from .config import settings
        writer = FeatureWriter(frame_directory.parent / 'scene-features', source, roi, settings.min_free_disk_bytes)
    samples: list[np.ndarray] = []
    sample_count = 0
    last_sample = -1.0
    video_end = 0.0
    last_progress = 0.0
    with av.open(str(source)) as container:
        video = next(s for s in container.streams.video if not (s.disposition & av.stream.Disposition.attached_pic))
        audio = container.streams.audio[0]
        origin = float(container.start_time or 0) / av.time_base
        duration = float(container.duration or 0) / av.time_base
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=16000)

        def add_audio(converted: Any) -> None:
            nonlocal sample_count
            array = converted.to_ndarray().reshape(-1)
            position = round((float(converted.time) - origin) * 16000) if converted.time is not None else sample_count
            if position > sample_count:
                samples.append(np.zeros(position - sample_count, dtype=np.float32))
                sample_count = position
            if position < sample_count:
                array = array[min(len(array), sample_count - position):]
            if len(array):
                samples.append(array)
                sample_count += len(array)

        for packet in container.demux(video, audio):
            for frame in packet.decode():
                if isinstance(frame, av.AudioFrame):
                    for converted in resampler.resample(frame):
                        add_audio(converted)
                else:
                    timestamp = max(0.0, float(frame.time or 0) - origin)
                    video_end = max(video_end, timestamp + float(frame.duration or 0) * float(frame.time_base))
                    if timestamp - last_sample >= .49:
                        if writer is not None:
                            rgb = thumbnail(frame, roi)
                            detector.feed(rgb, timestamp)
                            writer.add(rgb, timestamp)
                        else:
                            detector.feed(crop_picture(frame, roi), timestamp)
                        last_sample = timestamp
                    if time.monotonic() - last_progress >= 2:
                        context.progress(.04 + .05 * min(1, timestamp / max(duration, timestamp, 1)), 'video_analysis',
                                         completed=int(timestamp), total=math.ceil(duration) if duration else None, unit='media_second')
                        last_progress = time.monotonic()
        for converted in resampler.resample(None):
            add_audio(converted)
    if not samples or (detector.anchor is None if writer is not None else detector.picture is None):
        raise ValueError('Video has no decodable audio or picture')
    end = max(video_end, sample_count / 16000)
    padding = round(end * 16000) - sample_count
    if padding > 0:
        samples.append(np.zeros(padding, dtype=np.float32))
    values = np.concatenate(samples)
    del samples
    peak = float(np.max(np.abs(values)))
    if peak > 1:
        values /= peak
    normalized.parent.mkdir(parents=True, exist_ok=True)
    sf.write(normalized, values, 16000, format='WAV', subtype='PCM_16')
    detector.flush(end)
    if writer is not None:
        from .video_detection import LEGACY_VERSION, VERSION, materialize, stable_representatives
        writer.finish(end, origin)
        strict = detector_version != LEGACY_VERSION
        if strict:
            stable_representatives(source, frame_directory.parent / 'scene-features', roi, detector.scenes,
                                   detector.settings.min_stable_seconds, lambda *args: None)
        materialize(source, detector.scenes, frame_directory, roi, origin, lambda *args: None)
        return values, 16000, {'contract_version': 2 if strict else 1, 'revision': 1, 'duration': end, 'time_origin': origin,
                              'roi': roi, 'detector': VERSION if strict else LEGACY_VERSION, 'scene_detection': detection_settings,
                              'scenes': detector.scenes, 'automatic_scenes': [dict(s) for s in detector.scenes],
                              'original_scenes': [dict(s) for s in detector.scenes]}
    return values, 16000, {'contract_version': 1, 'revision': 1, 'duration': end, 'time_origin': origin,
                          'roi': roi, 'detector': 'presentation-v1', 'scenes': detector.scenes,
                          'original_scenes': [dict(scene) for scene in detector.scenes]}


def picture_at(source: Path, timestamp: float, roi: dict | None, origin: float = 0) -> Image.Image:
    with av.open(str(source)) as container:
        stream = next(s for s in container.streams.video if not (s.disposition & av.stream.Disposition.attached_pic))
        target = timestamp + origin
        container.seek(max(0, int(target / float(stream.time_base))), stream=stream, backward=True)
        last = None
        for frame in container.decode(stream):
            last = frame
            if float(frame.time or 0) >= target - .001:
                return crop_picture(frame, roi)
        if last is not None:
            return crop_picture(last, roi)
    raise ValueError('No frame at this timestamp')
