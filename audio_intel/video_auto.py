"""Streaming presentation detector. Only confirmed representative times survive the scan."""
from __future__ import annotations

from typing import Any

import numpy as np

VERSION = 'presentation-v4'


def sample(frame: Any, roi: dict | None) -> np.ndarray:
    if roi or getattr(frame, 'rotation', 0):
        from .video_frames import crop_picture
        rgb = np.asarray(crop_picture(frame, roi).resize((960, 540)))
    else:
        rgb = frame.to_ndarray(format='rgb24', width=960, height=540)
    # Interleave channels so equal-luminance colour edits survive local comparison.
    plane = rgb[:, :, 1].copy()
    plane[:, ::3] = rgb[:, ::3, 0]
    plane[:, 2::3] = rgb[:, 2::3, 2]
    return plane


class AutoDetector:
    def __init__(self, start: float = 0.) -> None:
        self.start = start
        self.scenes: list[dict] = []
        self.anchor = None
        self.candidate = None
        self.previous = None
        self.confirmed = False
        self.pending = None
        self.row_activity = np.zeros(27, dtype=np.float32)
        self.noise = np.zeros((27, 48), dtype=np.float32)
        self.since = start
        self.frame_time = None

    @staticmethod
    def tiles(mask: np.ndarray) -> np.ndarray:
        return mask.reshape(27, 20, 48, 20).sum(axis=(1, 3))

    def changed_tiles(self, first: np.ndarray, second: np.ndarray, quiet: np.ndarray,
                      changed: np.ndarray | None = None) -> np.ndarray:
        if changed is None:
            changed = (self.tiles(np.abs(first - second) > 24) >= 9) & quiet
        if 0 < changed.sum() <= 16:
            # A tiny isolated mark moving across uniform background is common
            # in slide recordings. Preserve changes embedded in text or figures.
            for y, x in zip(*changed.nonzero()):
                tile = np.s_[y*20:y*20+20, x*20:x*20+20]
                delta = np.abs(first[tile] - second[tile])
                if (delta > 48).sum() < 3 and (delta > 24).sum() < 24:
                    # Sparse low-amplitude ringing around a compressed static edge.
                    changed[y, x] = False
                    continue
                region = (slice(max(0, y*20-16), y*20+36), slice(max(0, x*20-16), x*20+36))
                a, b = first[region], second[region]
                background = np.median(a[::6, ::6])
                old = np.abs(a-background) > 40
                new = np.abs(b-background) > 40
                if max(old.sum(), new.sum()) <= 65 and (
                        (old & new).sum() < 6 or (old ^ new).sum() < 12):
                    changed[y, x] = False
        return changed

    def small_scroll(self, value: np.ndarray) -> bool:
        """Match textured rows around the last retained viewport, excluding chrome."""
        first = self.anchor[30:-30:4, 36:-36:12]
        second = value[30:-30:4, 36:-36:12]
        textured = np.ptp(first, axis=1) > 45
        for shift in range(-10, 11):
            if shift == 0:
                continue
            left = slice(max(0, -shift), min(len(first), len(first)-shift))
            right = slice(max(0, shift), min(len(first), len(first)+shift))
            rows = textured[left]
            if rows.sum() >= 8 and (np.abs(first[left][rows]-second[right][rows]) < 16).mean() > .97:
                return True
        return False

    @staticmethod
    def contours(plane: np.ndarray, contrast: int) -> np.ndarray:
        edges = np.zeros_like(plane, dtype=bool)
        edges[:, 1:] |= np.abs(np.diff(plane, axis=1)) > contrast
        edges[1:, :] |= np.abs(np.diff(plane, axis=0)) > contrast
        return edges

    def replaces_content(self, value: np.ndarray, changed: np.ndarray, quiet: np.ndarray) -> bool:
        if changed.mean() > .45:
            return True
        background = np.median(self.anchor[::12, ::12])
        old_ink = np.abs(self.anchor - background)
        new_ink = np.abs(value - background)
        removed = self.tiles((old_ink > 40) & (new_ink + 25 < old_ink))
        if changed.sum() >= 8:
            # Broad highlighting or dimming can preserve the original lettering.
            # Keep tiny edits on the sensitive path, including single-digit swaps.
            edges = self.contours(self.anchor, 32)
            new_edges = self.contours(value, 24)
            tolerant = new_edges.copy()
            tolerant[:, 1:] |= new_edges[:, :-1]
            tolerant[:, :-1] |= new_edges[:, 1:]
            tolerant[1:, :] |= new_edges[:-1, :]
            tolerant[:-1, :] |= new_edges[1:, :]
            lost = self.tiles(edges & ~tolerant)
            removed[(lost < 4) | (lost < .12*self.tiles(edges))] = 0
        return bool(self.changed_tiles(self.anchor, value, quiet, changed & (removed >= 6)).any())

    def feed(self, frame: np.ndarray, timestamp: float) -> None:
        value = frame.astype(np.int16)
        if self.previous is not None:
            motion = self.tiles(np.abs(self.previous - value) > 24) >= 9
            self.noise = .85 * self.noise + .15 * motion
            self.row_activity = .92*self.row_activity + .08*motion.any(axis=1)
        self.previous = value
        quiet = self.noise < .32
        # Recurring changes spanning a narrow lower band are characteristic
        # of burned-in subtitles and playback controls. Release it when idle.
        active_rows = np.convolve(self.row_activity, [1, 1, 1], mode='same')
        quiet[23:] &= (active_rows[23:] < .32)[:, None]
        # Widespread motion is a transition, not a permanent exclusion mask.
        if quiet.mean() < .65:
            quiet = np.ones_like(quiet)
        mismatch = None if self.candidate is None else self.changed_tiles(self.candidate, value, quiet)
        replace = mismatch is None or mismatch.any()
        if replace and mismatch is not None and not self.confirmed and mismatch.sum() < 24:
            # Independent activity elsewhere need not restart a stable text edit.
            # A small edit itself must remain visible throughout confirmation.
            if self.anchor is None:
                replace = False
            elif self.pending is not None and self.pending.any():
                replace = (self.pending & mismatch).sum() > .10*self.pending.sum()
        if replace:
            self.pending = self.changed_tiles(self.anchor, value, quiet) if self.anchor is not None else None
            self.candidate = value
            self.since = timestamp
            self.confirmed = False
            return
        if timestamp - self.since < 1 or self.confirmed:
            return
        self.confirmed = True
        value = self.candidate
        if self.anchor is not None:
            changed = self.changed_tiles(self.anchor, value, quiet)
            if not changed.any():
                return
            if changed.mean() > .06 and self.small_scroll(value):
                return
            if self.replaces_content(value, changed, quiet):
                self.flush(self.since)
                self.start = self.since
        self.anchor = value
        self.frame_time = self.since

    def flush(self, end: float) -> None:
        if end <= self.start:
            return
        if len(self.scenes) >= 2000:
            raise ValueError('Video exceeds 2000 presentation sections')
        self.scenes.append(dict(id=f'scene_{len(self.scenes)+1}', start=self.start,
                                end=end, frame_time=self.frame_time))
