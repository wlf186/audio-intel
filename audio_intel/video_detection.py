"""Versioned, offline slide detection and bounded, rebuildable feature caches."""
from __future__ import annotations

import io
import json
import math
import os
import shutil
import sys
import time
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any, Callable

import av
import numpy as np
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from .video_frames import crop_picture, features, picture_at, save_picture

VERSION = 'presentation-v3'
LEGACY_VERSION = 'presentation-v2'
CACHE_VERSION = 1
CACHE_LIMIT = 512 * 1024**2
CHUNK_SIZE = 60


class SceneDetectionSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    min_change_percent: float = Field(3, ge=.5, le=50, description='最小显著变化面积百分比，越低越敏感 / Minimum changed area percent; lower is more sensitive')
    min_stable_seconds: float = Field(1, ge=.5, le=5, description='新画面及自动截图最短稳定秒数；无达标截图时为空 / Minimum stable seconds for a new picture and automatic screenshot; no qualifying screenshot returns null')


class DetectionLimit(BaseModel):
    min: float
    max: float
    step: float
    default: float


class SceneDetectionCapability(BaseModel):
    detector: str
    preview: bool
    min_change_percent: DetectionLimit
    min_stable_seconds: DetectionLimit


def capability() -> dict:
    return {'detector': VERSION, 'preview': True,
            'min_change_percent': {'min': .5, 'max': 50, 'step': .5, 'default': 3},
            'min_stable_seconds': {'min': .5, 'max': 5, 'step': .1, 'default': 1}}


def thumbnail(frame: Any, roi: dict | None) -> np.ndarray:
    # Scaling in libswscale avoids converting every sample to a 1600px PIL image.
    if not roi and not int(getattr(frame, 'rotation', 0) or 0):
        return frame.to_ndarray(format='rgb24', width=320, height=180)
    return np.asarray(crop_picture(frame, roi).resize((320, 180)), dtype=np.uint8)


def difference(first: np.ndarray, second: np.ndarray) -> float:
    delta = np.abs(first.astype(np.int16) - second.astype(np.int16))
    return float((delta.max(axis=2) > 30).mean())


def blank(value: np.ndarray) -> bool:
    return int(value.max()) - int(value.min()) <= 3 and (value.mean() < 10 or value.mean() > 245)


def incremental(first: np.ndarray, second: np.ndarray) -> bool:
    """Only preserve near-complete old content; replacement text is not a build."""
    a, edges, _ = features(Image.fromarray(first))
    b, new_edges, _ = features(Image.fromarray(second))
    count = int(edges.sum())
    if count < 12 or new_edges.sum() < count * 1.02:
        return False
    expanded = np.pad(new_edges, 1)
    tolerant = new_edges.copy()
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        tolerant |= expanded[1 + dy:181 + dy, 1 + dx:321 + dx]
    retained = (edges & tolerant).sum() / count
    # Dark foreground disappearing is evidence of replacement, not addition.
    removed = ((a < .7) & (b > a + .12)).sum() / max(1, (a < .7).sum())
    return bool(retained >= .97 and removed < .02)


class Detector:
    def __init__(self, settings: dict | SceneDetectionSettings):
        self.settings = settings if isinstance(settings, SceneDetectionSettings) else SceneDetectionSettings(**settings)
        self.threshold = self.settings.min_change_percent / 100
        self.anchor: np.ndarray | None = None
        self.candidate: np.ndarray | None = None
        self.candidate_time = 0.
        self.candidate_build = False
        self.start = 0.
        self.frame_time = 0.
        self.scenes: list[dict] = []

    def feed(self, rgb: np.ndarray, timestamp: float) -> None:
        if self.anchor is None:
            self.anchor = rgb.copy()
            self.frame_time = timestamp
            return
        if self.start == 0 and timestamp <= 1 and blank(self.anchor):
            self.anchor = rgb.copy()
            self.frame_time = timestamp
            self.candidate = None
            return
        change = difference(self.anchor, rgb)
        if change < self.threshold:
            self.candidate = None
            self.frame_time = timestamp
            return
        if self.candidate is None or difference(self.candidate, rgb) >= self.threshold:
            self.candidate = rgb.copy()
            self.candidate_time = timestamp
            self.candidate_build = change < .20 and incremental(self.anchor, rgb)
        if timestamp - self.candidate_time + 1e-6 >= self.settings.min_stable_seconds:
            if not self.candidate_build:
                self.flush(self.candidate_time)
                self.start = self.candidate_time
            self.anchor = rgb.copy()
            self.frame_time = timestamp
            self.candidate = None

    def flush(self, end: float) -> None:
        if self.anchor is None or end <= self.start:
            return
        if len(self.scenes) >= 2000:
            raise ValueError('Video exceeds 2000 presentation sections; increase thresholds or split the source')
        self.scenes.append({'id': f'scene_{len(self.scenes) + 1}', 'start': self.start,
                            'end': end, 'frame_time': max(self.start, min(self.frame_time, math.nextafter(end, 0)))})


def identity(source: Path, roi: dict | None) -> dict:
    stat = source.stat()
    return {'version': CACHE_VERSION, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'roi': roi, 'sample_seconds': .5, 'shape': [180, 320, 3]}


class FeatureWriter:
    def __init__(self, folder: Path, source: Path, roi: dict | None, min_free: int = 0):
        self.folder = folder
        self.staging = folder.with_name(folder.name + '.partial-' + uuid.uuid4().hex)
        self.identity = identity(source, roi)
        self.min_free = min_free
        self.arrays: list[np.ndarray] = []
        self.times: list[float] = []
        self.chunks = 0
        self.bytes = 0
        self.enabled = True
        try:
            self.staging.mkdir(parents=True)
        except OSError:
            self.enabled = False

    def add(self, rgb: np.ndarray, timestamp: float) -> None:
        if not self.enabled:
            return
        self.arrays.append(rgb.copy())
        self.times.append(timestamp)
        if len(self.arrays) == CHUNK_SIZE:
            self._chunk()

    def _chunk(self) -> None:
        if not self.arrays or not self.enabled:
            return
        try:
            data = np.stack(self.arrays)
            data[1:] = np.bitwise_xor(data[1:], data[:-1])
            buffer = io.BytesIO()
            np.savez_compressed(buffer, frames=data, times=np.asarray(self.times, dtype=np.float64))
            payload = buffer.getvalue()
            if self.bytes + len(payload) > CACHE_LIMIT or shutil.disk_usage(self.staging).free - len(payload) < self.min_free:
                raise OSError('Feature cache budget exhausted')
            (self.staging / f'{self.chunks:06d}.npz').write_bytes(payload)
            self.bytes += len(payload)
            self.chunks += 1
        except OSError:
            self.abort()
        finally:
            self.arrays.clear()
            self.times.clear()

    def finish(self, duration: float, origin: float) -> bool:
        self._chunk()
        if not self.enabled:
            return False
        try:
            (self.staging / 'manifest.json').write_text(json.dumps({
                **self.identity, 'chunks': self.chunks, 'duration': duration, 'time_origin': origin}), encoding='utf-8')
            # Readers/builders for a task are serialized by the preview manager.
            if self.folder.exists():
                shutil.rmtree(self.folder)
            os.replace(self.staging, self.folder)
            return True
        except OSError:
            self.abort()
            return False

    def abort(self) -> None:
        self.enabled = False
        shutil.rmtree(self.staging, ignore_errors=True)


def cached_samples(folder: Path, source: Path, roi: dict | None):
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    if any(manifest.get(k) != v for k, v in identity(source, roi).items()):
        raise ValueError('Feature cache identity mismatch')
    count = manifest['chunks']
    if not isinstance(count, int) or not 0 < count <= math.ceil(manifest['duration'] / .49 / CHUNK_SIZE) + 1:
        raise ValueError('Invalid feature cache length')
    previous = -1.
    for index in range(count):
        path = folder / f'{index:06d}.npz'
        with zipfile.ZipFile(path) as archive:
            if set(archive.namelist()) != {'frames.npy', 'times.npy'} or any(
                entry.file_size > CHUNK_SIZE * 180 * 320 * 3 + 4096 for entry in archive.infolist()
            ):
                raise ValueError('Invalid feature cache chunk')
        with np.load(path, allow_pickle=False) as chunk:
            frames, times = chunk['frames'], chunk['times']
        if frames.dtype != np.uint8 or frames.ndim != 4 or frames.shape[1:] != (180, 320, 3) or not 0 < len(frames) <= CHUNK_SIZE or times.shape != (len(frames),):
            raise ValueError('Invalid feature cache shape')
        np.bitwise_xor.accumulate(frames, axis=0, out=frames)
        for timestamp, rgb in zip(times, frames):
            if not np.isfinite(timestamp) or timestamp <= previous or timestamp > manifest['duration']:
                raise ValueError('Invalid feature cache timestamp')
            previous = float(timestamp)
            yield float(timestamp), rgb


def scan(source: Path, roi: dict | None, detector: Detector, writer: FeatureWriter, progress: Callable) -> tuple[float, float]:
    last = -1.
    end = 0.
    try:
        with av.open(str(source)) as container:
            stream = next(s for s in container.streams.video if not s.disposition & av.stream.Disposition.attached_pic)
            origin = float(container.start_time or 0) / av.time_base
            duration = float(container.duration or 0) / av.time_base
            for frame in container.decode(stream):
                timestamp = max(0., float(frame.time or 0) - origin)
                end = max(end, timestamp + float(frame.duration or 0) * float(frame.time_base))
                if timestamp - last >= .49:
                    rgb = thumbnail(frame, roi)
                    detector.feed(rgb, timestamp)
                    writer.add(rgb, timestamp)
                    last = timestamp
                    progress('scanning', timestamp, duration)
            end = max(end, duration)
        writer.finish(end, origin)
        return end, origin
    except BaseException:
        writer.abort()
        raise


def materialize(source: Path, scenes: list[dict], folder: Path, roi: dict | None, origin: float, progress: Callable, existing: list[dict] | None = None, existing_folder: Path | None = None) -> None:
    reusable = {s['frame_time']: s['image'] for s in existing or [] if s.get('image') and s.get('frame_time') is not None}
    for index, scene in enumerate(scenes):
        if scene['frame_time'] is None:
            scene['image'] = None
            progress('images', index + 1, len(scenes))
            continue
        name = reusable.get(scene['frame_time'])
        if name and existing_folder and (existing_folder / name).is_file():
            folder.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(existing_folder / name, folder / name)
        else:
            name = save_picture(picture_at(source, scene['frame_time'], roi, origin), folder)
        scene['image'] = name
        progress('images', index + 1, len(scenes))


def select_representatives(samples, scenes: list[dict], hold: float, progress: Callable) -> None:
    """Confirm an actual sampled frame inside one section, never an end-of-run flash.

    The fixed reference prevents gradual drift from accumulating into a new picture.
    A separate 0.4% noise tolerance keeps the section-area setting out of selection.
    Only observed timestamps count; EOF and the next section cannot extend a run.
    """
    for scene in scenes:
        scene.update(frame_time=None, image=None)
    index = 0
    reference = None
    since = 0.
    for timestamp, rgb in samples:
        while index < len(scenes) and timestamp >= scenes[index]['end']:
            index += 1
            reference = None
        if index == len(scenes):
            break
        if timestamp < scenes[index]['start']:
            continue
        if reference is None or difference(reference, rgb) >= .004:
            reference = rgb.copy()
            since = timestamp
        if timestamp - since >= hold:
            scenes[index]['frame_time'] = since
        progress('selecting', timestamp, scenes[-1]['end'])


def source_samples(source: Path, roi: dict | None):
    last = -1.
    with av.open(str(source)) as container:
        stream = next(s for s in container.streams.video if not s.disposition & av.stream.Disposition.attached_pic)
        origin = float(container.start_time or 0) / av.time_base
        for frame in container.decode(stream):
            timestamp = max(0., float(frame.time or 0) - origin)
            if timestamp - last >= .49:
                yield timestamp, thumbnail(frame, roi)
                last = timestamp


def stable_representatives(source: Path, cache: Path, roi: dict | None, scenes: list[dict], hold: float, progress: Callable) -> None:
    try:
        select_representatives(cached_samples(cache, source, roi), scenes, hold, progress)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, EOFError, zlib.error):
        # Restart selection completely if a cache fails after yielding some samples.
        select_representatives(source_samples(source, roi), scenes, hold, progress)


def preview(request: dict, folder: Path, progress: Callable) -> dict:
    source, cache = Path(request['source']), Path(request['cache'])
    roi = request['roi']
    detector = Detector(request['settings'])
    hit = False
    try:
        for timestamp, rgb in cached_samples(cache, source, roi):
            detector.feed(rgb, timestamp)
            progress('detecting', timestamp, request['duration'])
        hit = True
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, EOFError, zlib.error):
        detector = Detector(request['settings'])
        writer = FeatureWriter(cache, source, roi, request['min_free'])
        scan(source, roi, detector, writer, progress)
    detector.flush(request['duration'])
    stable_representatives(source, cache, roi, detector.scenes, detector.settings.min_stable_seconds, progress)
    def image_progress(phase, completed, total):
        if shutil.disk_usage(folder).free < request['min_free']:
            raise OSError('Insufficient storage')
        progress(phase, completed, total)
    materialize(source, detector.scenes, folder / 'frames', roi, request['time_origin'], image_progress,
                request.get('existing_scenes'), Path(request['existing_frames']))
    return {'scenes': detector.scenes, 'settings': detector.settings.model_dump(), 'detector': VERSION, 'contract_version': 2, 'cache_hit': hit}


def main() -> None:
    folder = Path(sys.argv[1])
    request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
    number = 0
    last = 0.

    def progress(phase: str, completed: float, total: float):
        nonlocal number, last
        if time.monotonic() - last < .25 and completed < total:
            return
        number += 1
        last = time.monotonic()
        # Immutable progress files avoid replacing files open by Windows readers.
        try:
            (folder / f'progress-{number:08d}.json').write_text(
                json.dumps({'phase': phase, 'completed': completed, 'total': total}), encoding='utf-8')
        except OSError:
            pass
    try:
        result = preview(request, folder, progress)
        (folder / 'result.json').write_text(json.dumps(result), encoding='utf-8')
    except Exception:
        # Never put private media paths or decoded content in API error messages.
        (folder / 'error.json').write_text(json.dumps({'error': 'Video analysis failed; retry the preview'}), encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
