"""Task-local interval lookup preserving original diarization tie ordering."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import math
from typing import Any


class SpeakerIndex:
    def __init__(self, intervals: list[dict[str, Any]]):
        self.intervals = intervals
        try:
            self.valid = all(math.isfinite(x['start']) and math.isfinite(x['end'])
                             and x['start'] <= x['end'] and isinstance(x['speaker'], str)
                             for x in intervals)
        except (KeyError, TypeError, ValueError):
            self.valid = False
        self.first: dict[str, int] = {}
        for i, item in enumerate(intervals if self.valid else []):
            self.first.setdefault(item['speaker'], i)
        self.order = sorted(range(len(intervals)), key=lambda i: intervals[i]['start']) if self.valid else []
        self.starts = [intervals[i]['start'] for i in self.order]
        self.size = 1
        while self.size < len(self.order):
            self.size *= 2
        self.maximum = [float('-inf')] * (2 * self.size)
        for j, i in enumerate(self.order):
            self.maximum[self.size + j] = intervals[i]['end']
        for j in range(self.size - 1, 0, -1):
            self.maximum[j] = max(self.maximum[j * 2], self.maximum[j * 2 + 1])
        # Equal endpoints retain the first interval in the original list.
        self.left: dict[float, int] = {}
        self.right: dict[float, int] = {}
        for i, item in enumerate(intervals if self.valid else []):
            self.left.setdefault(item['end'], i)
            self.right.setdefault(item['start'], i)
        self.ends = sorted(self.left)
        self.begins = sorted(self.right)

    def speaker_at(self, start: float, end: float) -> str:
        if not self.valid or not math.isfinite(start) or not math.isfinite(end) or start > end:
            from .pipeline import speaker_at
            return speaker_at(start, end, self.intervals)
        candidates: list[int] = []
        upper = bisect_right(self.starts, end)

        def visit(node: int, first: int, last: int) -> None:
            if first >= upper or self.maximum[node] < start:
                return
            if last - first == 1:
                candidates.append(self.order[first])
                return
            middle = (first + last) // 2
            visit(node * 2, first, middle)
            visit(node * 2 + 1, middle, last)

        visit(1, 0, self.size)
        overlaps: dict[str, float] = {}
        midpoint_speakers = set()
        midpoint = (start + end) / 2
        for i in sorted(candidates):
            item = self.intervals[i]
            speaker = item['speaker']
            overlaps[speaker] = overlaps.get(speaker, 0.) + max(0., min(end, item['end']) - max(start, item['start']))
            if item['start'] <= midpoint <= item['end']:
                midpoint_speakers.add(speaker)
        if overlaps and max(overlaps.values()) > 0:
            return max(overlaps, key=lambda speaker: (overlaps[speaker], speaker in midpoint_speakers, -self.first[speaker]))
        if candidates:
            return self.intervals[min(candidates)]['speaker']
        left = bisect_right(self.ends, start) - 1
        right = bisect_left(self.begins, end)
        nearest = []
        if left >= 0:
            nearest.append((start - self.ends[left], self.left[self.ends[left]]))
        if right < len(self.begins):
            nearest.append((self.begins[right] - end, self.right[self.begins[right]]))
        return self.intervals[min(nearest)[1]]['speaker'] if nearest else 'Speaker_0'
