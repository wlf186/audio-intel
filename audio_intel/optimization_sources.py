"""Immutable, scene-independent sources for continuous speech editing.

These groups describe source turns, not model request size. Request batching must
retain each span's offsets and must never turn a picture boundary into a text cut.
"""
from __future__ import annotations

from typing import Any
from bisect import bisect_right
import math

from .transcript import join_text


def scene_index(scenes: list[dict]) -> tuple[list[dict], list[float]]:
    ordered = sorted(scenes, key=lambda scene: (scene['start'], scene['end']))
    maximum, ends = float('-inf'), []
    for scene in ordered:
        maximum = max(maximum, scene['end'])
        ends.append(maximum)
    return ordered, ends


def matching_scenes(index: tuple[list[dict], list[float]], start: float, end: float):
    scenes, ends = index
    for position in range(bisect_right(ends, start), len(scenes)):
        scene = scenes[position]
        if scene['start'] >= end:
            break
        if min(end, scene['end']) > max(start, scene['start']):
            yield scene


def source_groups(result: dict[str, Any], *, pause_seconds: float = 15) -> list[dict[str, Any]]:
    """Group adjacent attributed speech without crossing another speaker's turn."""
    if pause_seconds < 0:
        raise ValueError('Pause threshold must be nonnegative')
    groups: list[dict[str, Any]] = []
    previous_speaker: str | None = None
    previous_end: float | None = None
    indexed_scenes = scene_index(result.get('video', {}).get('scenes', []))
    scene_ids: set[str] = set()
    for segment in result.get('segments', []):
        text = segment.get('text', '')
        if not text.strip():
            continue
        speaker = segment.get('speaker')
        start, end = segment['start'], segment['end']
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ValueError('Source interval ends before it starts')
        if not groups or speaker != previous_speaker or start - previous_end > pause_seconds:
            scene_ids = set()
            groups.append({'id': f'g{len(groups) + 1}', 'speaker': speaker,
                           'speaker_label': segment.get('speaker_label'),
                           'start': start, 'end': end, 'original_text': '',
                           'sources': [], 'segment_ids': [], 'scene_ids': []})
        group = groups[-1]
        group['original_text'] = join_text(group['original_text'], text)
        group['end'] = max(group['end'], end)
        group['sources'].append({'segment_id': segment['id'], 'text': text,
                                 'start_offset': 0, 'end_offset': len(text),
                                 'start': start, 'end': end})
        group['segment_ids'].append(segment['id'])
        for scene in matching_scenes(indexed_scenes, start, end):
            if scene['id'] not in scene_ids:
                scene_ids.add(scene['id'])
                group['scene_ids'].append(scene['id'])
        previous_speaker, previous_end = speaker, group['end']
    for group in groups:
        group['merged_source_count'] = len(group['segment_ids'])
    return groups
