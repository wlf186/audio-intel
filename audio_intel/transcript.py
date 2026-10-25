"""Lossless reading paragraphs, shared by the PDF and video reading view."""
from __future__ import annotations

import unicodedata
from bisect import bisect_right
from typing import Any


def join_text(left: str, right: str) -> str:
    if not left:
        return right
    if not right:
        return left
    # Preserve existing whitespace; insert a space between Latin-script words.
    space = not left[-1].isspace() and not right[0].isspace() and (
        ord(left[-1]) < 0x3000 and ord(right[0]) < 0x3000
        and right[0] not in ',.!?:;)]}'
    )
    return left + (' ' if space else '') + right


def paragraphs(segments: list[dict[str, Any]], *, include_sources: bool = False) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for segment in segments:
        if not segment.get('text'):
            continue
        if output and output[-1]['speaker'] == segment.get('speaker'):
            current = output[-1]
            current['text'] = join_text(current['text'], segment['text'])
            current['end'] = max(current['end'], segment['end'])
            current['segment_ids'].append(segment['id'])
        else:
            output.append({key: segment.get(key) for key in ('speaker', 'speaker_label', 'start', 'end', 'text')})
            output[-1]['segment_ids'] = [segment['id']]
        if include_sources:
            output[-1].setdefault('sources', []).append({
                'segment_id': segment['id'], 'text': segment['text'],
                'start_offset': segment.get('_start_offset', 0),
                'end_offset': segment.get('_end_offset', len(segment['text'])),
                'start': segment['start'], 'end': segment['end'],
            })
    return output


def scene_transcripts(result: dict[str, Any], *, include_sources: bool = False) -> list[dict[str, Any]]:
    scenes = result.get('video', {}).get('scenes', [])
    buckets: list[list[dict[str, Any]]] = [[] for _ in scenes]
    if not scenes:
        return []

    ends = [scene['end'] for scene in scenes]

    def owner(start: float, end: float) -> int:
        first = min(bisect_right(ends, start), len(scenes) - 1)
        best, overlap = first, -1.0
        for index in range(first, len(scenes)):
            scene = scenes[index]
            if scene['start'] > end:
                break
            candidate = max(0.0, min(end, scene['end']) - max(start, scene['start']))
            if candidate > overlap:
                best, overlap = index, candidate
        return best

    def punctuation_only(value: str) -> bool:
        return all(character.isspace() or unicodedata.category(character).startswith('P') for character in value)

    for segment in result.get('segments', []):
        words = segment.get('words') or []
        text = segment['text']
        # Split only when the alignment covers the exact text. Otherwise preserve
        # the whole segment. Unaligned punctuation stays beside its original words.
        positions: list[int] = []
        cursor = 0
        for word in words:
            token = str(word.get('text') or '')
            found = text.find(token, cursor) if token else -1
            if found < 0 or not punctuation_only(text[cursor:found]):
                positions = []
                break
            positions.append(found)
            cursor = found + len(token)
        can_split = bool(positions) and punctuation_only(text[cursor:])
        if not can_split:
            buckets[owner(segment['start'], segment['end'])].append(segment)
            continue
        positions[0] = 0
        positions.append(len(text))
        for i, word in enumerate(words):
            index = owner(word['start'], word['end'])
            part = {**segment, 'start': word['start'], 'end': word['end'], 'text': text[positions[i]:positions[i + 1]], '_start_offset': positions[i], '_end_offset': positions[i + 1]}
            # Recombine aligned words without inventing inter-word whitespace.
            target = buckets[index]
            if target and target[-1]['id'] == segment['id']:
                target[-1] = {**target[-1], 'text': target[-1]['text'] + part['text'], 'end': part['end'], '_end_offset': part['_end_offset']}
            else:
                target.append(part)
    return [{**scene, 'paragraphs': paragraphs(bucket, include_sources=include_sources)} for scene, bucket in zip(scenes, buckets)]


def reading_sections(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Reading drafts retain paired paragraphs instead of lossless re-merging."""
    if not result.get('_optimized'):
        return scene_transcripts(result) if result.get('video') else [{'paragraphs': paragraphs(result.get('segments', []))}]
    scenes = [{**scene, 'paragraphs': []} for scene in result.get('video', {}).get('scenes', [])]
    if not scenes:
        return [{'paragraphs': result.get('segments', [])}]
    for paragraph in result.get('segments', []):
        owner = max(range(len(scenes)), key=lambda i: max(0, min(paragraph['end'], scenes[i]['end']) - max(paragraph['start'], scenes[i]['start'])))
        scenes[owner]['paragraphs'].append(paragraph)
    return scenes
