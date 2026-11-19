"""Streaming selection of chronological, stable presentation pictures."""
from __future__ import annotations

import numpy as np

from .video_auto import AutoDetector, VERSION as AUTO_VERSION

LEGACY_VERSION = 'presentation-v5'
VERSION = 'presentation-v6'
LEVELS = ('concise', 'balanced', 'detailed')


def structure(frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    # Average neighbouring samples to suppress compression ringing around letters.
    a = frame.reshape(270, 2, 480, 2).mean((1, 3), dtype=np.float32)
    edges = np.zeros(a.shape, bool)
    edges[1:] |= np.abs(np.diff(a, axis=0)) > 20
    edges[:, 1:] |= np.abs(np.diff(a, axis=1)) > 20
    return a, edges


def caption_band(a: np.ndarray, activity: np.ndarray) -> np.ndarray:
    # Look for a centered, solid dark caption backing with a bright text interior.
    mask = np.zeros(a.shape, bool)
    cx = a.shape[1] // 2
    for y in range(195, 263):
        if a[y, cx] > 19 or activity[min(y//10, 26)] < .025:
            continue
        bright = np.flatnonzero(a[y] > 20)
        left, right = bright[bright < cx], bright[bright > cx]
        if left.size and right.size:
            x0, x1 = left[-1]+1, right[0]
            if 38 <= x1-x0 <= 405 and abs((x0+x1)/2-cx) < 10:
                # Only mask the box itself, preserving material beside/below it.
                mask[y, x0:x1] = True
    rows = np.flatnonzero(mask.any(axis=1))
    for top, bottom in zip(rows, rows[1:]):
        if 1 < bottom-top <= 19 and (a[top:bottom][..., cx-32:cx+32] > 90).any():
            mask[top:bottom] |= mask[top] & mask[bottom]
    return mask


Picture = tuple[np.ndarray, np.ndarray, np.ndarray]


def minimum_detail(previous: Picture, picture: Picture) -> int:
    """Return the lowest detail tier that needs a new section (3 merges everywhere)."""
    plane, edges, overlay = picture
    before, old, prior_overlay = previous
    content = ~(prior_overlay | overlay)
    delta = (np.abs(plane-before) > 25) & content
    old = old & content
    added = (edges & ~old & delta).sum()
    lost = (old & ~edges & delta).sum()
    change = delta.sum()
    ys, xs = np.nonzero(delta)
    width, height = (np.percentile(v, 95)-np.percentile(v, 5) if len(v) else 0 for v in (xs, ys))
    if change < 6:
        return 3
    if change > .45*plane.size:
        return 0
    if change < 180 and lost >= 3 and lost >= .32*added:
        return 1  # discrete small replacement, such as a changed digit
    if width > 85 and (height < 15 or height > 65) and added > 250 and lost < .2*added:
        # Highlighted text runs and links between distant diagram nodes
        # can change the explanation while preserving most old edges.
        return 1
    if width > 125 and height < 55 and lost < .45*added:
        return 2
    if width > 125 and height < 65 and lost < .65*added and change < .025*plane.size:
        return 1
    if lost < max(18, .22*added) or lost < .012*max(1, old.sum()):
        return 2  # the new state still contains the earlier material
    if lost < .06*old.sum() and change < .05*plane.size:
        return 1
    return 0


class DetailDetector(AutoDetector):
    def __init__(self, start: float = 0.) -> None:
        super().__init__(start)
        self.outputs: dict[str, list[dict]] = {level: [] for level in LEVELS}
        self.previous_picture = None

    def replaces_content(self, value: np.ndarray, changed: np.ndarray, quiet: np.ndarray) -> bool:
        # Additive builds also enter the candidate stream. Tier selection happens
        # after a stable representative has been observed on each side.
        return True

    def compare(self, previous: Picture, current: Picture) -> tuple[int, bool]:
        return minimum_detail(previous, current), True

    def flush(self, end: float) -> None:
        if end <= self.start:
            return
        picture = None
        rank = 0
        replace_picture = True
        if self.anchor is not None:
            plane, edges = structure(self.anchor)
            overlay = caption_band(plane, self.row_activity)
            content_edges = (edges & ~overlay)[15:-24, 40:-40]
            if (self.previous_picture is not None and end-self.start < 4
                    and np.count_nonzero(content_edges) < 20 and np.median(plane) < 64):
                for scenes in self.outputs.values():
                    if scenes:
                        scenes[-1]['end'] = end
                return
            picture = plane, edges, overlay
            if self.previous_picture is not None:
                rank, replace_picture = self.compare(self.previous_picture, picture)
        for index, level in enumerate(LEVELS):
            scenes = self.outputs[level]
            if len(scenes) > 2000:
                continue
            if scenes and index < rank and picture is not None:
                scenes[-1]['end'] = end
                if replace_picture:
                    scenes[-1]['frame_time'] = self.frame_time
            else:
                scenes.append(dict(id=f'scene_{len(scenes)+1}', start=self.start, end=end, frame_time=self.frame_time))
        self.previous_picture = picture

    def result(self, level: str) -> list[dict]:
        return selected(self.outputs, level)


def selected(outputs: dict[str, list[dict]], level: str) -> list[dict]:
    scenes = outputs[level]
    if len(scenes) > 2000:
        raise ValueError('Video exceeds 2000 presentation sections for this detail level')
    return [dict(s) for s in scenes]


def same_viewport(before: np.ndarray, after: np.ndarray) -> bool:
    """Verify a small pan or zoom against the last retained viewport."""
    # Work on a compact thumbnail; compare textured content so a common
    # blank background cannot hide a changed chart or a newly opened panel.
    def thumbnail(plane: np.ndarray) -> np.ndarray:
        return plane[24:232, 24:456].reshape(52, 4, 108, 4).mean((1, 3))

    ref, view = thumbnail(before), thumbnail(after)
    if ref.std() < 12 or view.std() < 12:
        return False
    y, x = np.indices(view.shape)
    view_transform = np.fft.rfft2(view - view.mean())
    for scale in (1., .96, 1.04, .92, 1.08):
        sy = np.rint((y - 25.5) / scale + 25.5).astype(int)
        sx = np.rint((x - 53.5) / scale + 53.5).astype(int)
        scaled = ref[sy.clip(0, 51), sx.clip(0, 107)]
        corr = view_transform * np.fft.rfft2(scaled - scaled.mean()).conj()
        corr /= np.maximum(abs(corr), 1e-6)
        scores = np.fft.irfft2(corr, s=view.shape, axes=(0, 1))
        dy, dx = np.unravel_index(np.argmax(scores), scores.shape)
        dy = (int(dy) + 26) % 52 - 26
        dx = (int(dx) + 54) % 108 - 54
        if abs(dx) > 7 or abs(dy) > 4 or (scale == 1 and dx == dy == 0):
            continue
        ry, rx = sy - round(dy / scale), sx - round(dx / scale)
        valid = (ry >= 0) & (ry < 52) & (rx >= 0) & (rx < 108)
        projected = ref[ry.clip(0, 51), rx.clip(0, 107)]
        texture = np.maximum(abs(np.gradient(view, axis=1)), abs(np.gradient(view, axis=0))) > 8
        # Persistent browser chrome and a corner presenter inset move
        # independently of the viewport during a pan.
        texture[:3] = False
        texture[-8:, -23:] = False
        overlap = valid & texture
        if (overlap.sum() >= 200 and (~valid & texture).sum() < .045 * texture.sum()
                and (abs(view[overlap] - projected[overlap]) > 24).mean()
                < min(.15, .55 * (abs(view[overlap] - ref[overlap]) > 24).mean())):
            return True
    return False


class ReadingDetector(DetailDetector):
    """Choose a readable representative while retaining a detailed build timeline."""

    viewport_anchor: np.ndarray | None = None

    def compare(self, previous: Picture, current: Picture) -> tuple[int, bool]:
        plane, edges, overlay = current
        before, old_edges, old_overlay = previous
        # Exclude player chrome and active caption boxes. Retain side columns,
        # where scrolling exposes text and application panels open or close.
        region = np.s_[20:235, 20:-20]
        valid = ~(overlay | old_overlay)[region]
        delta = (np.abs(plane[region] - before[region]) > 22) & valid
        change = delta.mean()
        old = old_edges[region] & valid
        new = edges[region] & valid
        # Dense photographic texture with a stationary surround and motion in
        # an already textured subject. Flat text/graph backgrounds fail this gate.
        if (old.mean() > .35 and new.mean() > .35 and .008 < change < .17
                and max(delta[:, :75].mean(), delta[:, -75:].mean()) < .015
                and abs(float(old.mean()) - new.mean()) < .045):
            return 3, False
        # A presenter inset sometimes changes over an otherwise still page.
        # Require motion to be confined to a textured corner.
        for columns in (slice(0, 95), slice(-95, None)):
            inset = np.s_[-50:, columns]
            if (old[inset].mean() > .22 and new[inset].mean() > .22
                    and delta[inset].sum() > .97 * delta.sum() and delta[inset].sum() > 80):
                return 3, False
        if .025 < change < .50:
            reference = self.viewport_anchor if self.viewport_anchor is not None else before
            if same_viewport(reference, plane):
                self.viewport_anchor = reference
                return 3, False
        self.viewport_anchor = None
        if not delta.any():
            return minimum_detail(previous, current), False
        added = int((new & ~old & delta).sum())
        lost = int((old & ~new & delta).sum())
        # A reveal followed by an exit should leave the readable middle state.
        if .003 < change < .035 and lost > 100 and added < .16 * lost:
            return 3, False
        tier = minimum_detail(previous, current)
        # Faint outlines preceding a legible reveal are scaffolding.
        if (tier == 0 and change < .04 and added > 2.3 * lost and added > 280
                and old_edges[28:224, 35:-35].sum() < 1650):
            return 3, True
        if tier == 0 and .003 < change < .035 and added > 100 and lost < .16 * added:
            return max(tier, 2), True
        return tier, True


# Requests persist detector identities; cache formats are independently versioned.
DETECTORS = {AUTO_VERSION: AutoDetector, LEGACY_VERSION: DetailDetector, VERSION: ReadingDetector}
INDEX_VERSIONS = {AUTO_VERSION: 1, LEGACY_VERSION: 2, VERSION: 3}
