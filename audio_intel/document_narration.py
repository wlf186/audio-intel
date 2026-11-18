"""Versioned source annotations and deterministic document narration plans.

Every offset is a Unicode code-point offset in the retained extraction text.
No inferred annotation removes source text. Uncertain numeric content is spoken.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION = 1
KINDS = ('page_number', 'note_reference', 'note_body')


class NarrationOverride(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    action: Literal['speak', 'skip']


class NarrationOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    skip_enabled: bool = True
    skip_kinds: list[Literal['page_number', 'note_reference', 'note_body']] = Field(default_factory=lambda: list(KINDS))
    natural_pauses: bool = True
    analysis_revision: str | None = Field(None, max_length=64)
    overrides: list[NarrationOverride] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def validate_ranges(self):
        if len(set(self.skip_kinds)) != len(self.skip_kinds):
            raise ValueError('Duplicate narration categories')
        previous = -1
        for item in sorted(self.overrides, key=lambda x: x.start):
            if item.end <= item.start or item.start < previous:
                raise ValueError('Narration overrides must be nonempty and nonoverlapping')
            previous = item.end
        if self.overrides and not self.analysis_revision:
            raise ValueError('Range overrides require analysis_revision')
        self.skip_kinds.sort()
        self.overrides.sort(key=lambda x: x.start)
        return self


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def annotation(start: int, end: int, kind: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {'start': start, 'end': end, 'kind': kind, 'reason': reason, **extra}


def finish_analysis(document: dict[str, Any]) -> None:
    """Add conservative text-level evidence to format-specific extraction evidence."""
    text = document['text']
    annotations = document.setdefault('annotations', [])
    # Definitions are explicit, unlike an arbitrary standalone bracketed number.
    definitions = list(re.finditer(r'(?m)^\[\^([^\]\n]+)\]:[^\n]*(?:\n[ \t]+[^\n]+)*', text))
    labels = {m[1] for m in definitions}
    for match in definitions:
        annotations.append(annotation(match.start(), match.end(), 'note_body', 'explicit_note_definition'))
    for match in re.finditer(r'\[\^([^\]\n]+)\](?!:)', text):
        if match[1] in labels:
            annotations.append(annotation(match.start(), match.end(), 'note_reference', 'linked_note_definition'))
    # Citation-shaped suffixes need matching bibliography entries. This excludes
    # arrays, dates, arbitrary [numbers] and numbered lists without references.
    entries = list(re.finditer(r'(?m)^\[(\d{1,3})\][ \t]+\S', text))
    bibliography = {m[1] for m in entries}
    entry_starts = {m.start() for m in entries}
    for match in re.finditer(r'\[(\d{1,3})\]', text):
        prefix = text[max(0, match.start()-8):match.start()].rstrip()
        if match.start() not in entry_starts and match[1] in bibliography and prefix and prefix[-1] in '。！？.!?；;”’」』':
            annotations.append(annotation(match.start(), match.end(), 'note_reference', 'citation_with_bibliography'))
    unique = {(a['start'], a['end'], a['kind']): a for a in annotations if 0 <= a['start'] < a['end'] <= len(text)}
    document['annotations'] = sorted(unique.values(), key=lambda a: (a['start'], a['end'], a['kind']))
    if 'boundaries' not in document:
        document['boundaries'] = [{'offset': m.end(), 'kind': 'paragraph'} for m in re.finditer(r'\n[^\S\n]*\n+', text)]
    boundaries = {b['offset']: b['kind'] for b in document['boundaries']}
    for heading in document.get('headings', []):
        boundaries[heading['offset']] = 'chapter'
        end = text.find('\n', heading['offset'])
        if end >= 0:
            boundaries.setdefault(end + 1, 'paragraph')
    document['boundaries'] = [{'offset': offset, 'kind': kind} for offset, kind in sorted(boundaries.items())]
    document['narration_version'] = VERSION
    document['analysis_revision'] = digest([VERSION, document['text_hash'], document['annotations'], document['boundaries']])


def options_for(document: dict[str, Any], options: NarrationOptions | None) -> NarrationOptions | None:
    if not document.get('narration_version'):
        if options is not None:
            raise ValueError('Reanalyze this document before using narration options / 请先重新分析文档')
        return None
    value = options or NarrationOptions()
    if value.analysis_revision and value.analysis_revision != document['analysis_revision']:
        raise LookupError('Document analysis changed; preview again / 文档分析已更新，请重新预览')
    if any(item.end > len(document['text']) for item in value.overrides):
        raise ValueError('Narration override exceeds source text')
    return value


def skipped_ranges(document: dict[str, Any], options: NarrationOptions) -> list[tuple[int, int]]:
    events: dict[int, list[int]] = {}
    for a in document.get('annotations', []) if options.skip_enabled else []:
        if a['kind'] in options.skip_kinds:
            events.setdefault(a['start'], [0, 0, 0])[0] += 1
            events.setdefault(a['end'], [0, 0, 0])[0] -= 1
    for a in options.overrides:
        slot = 1 if a.action == 'speak' else 2
        events.setdefault(a.start, [0, 0, 0])[slot] += 1
        events.setdefault(a.end, [0, 0, 0])[slot] -= 1
    state = [0, 0, 0]; last = 0; ranges: list[tuple[int, int]] = []
    for at, delta in sorted(events.items()):
        if at > last and (state[2] or (state[0] and not state[1])):
            if ranges and ranges[-1][1] == last:
                ranges[-1] = (ranges[-1][0], at)
            else:
                ranges.append((last, at))
        state = [a+b for a,b in zip(state, delta)]; last = at
    return ranges


def spoken_slice(text: str, start: int, end: int, skipped: list[tuple[int, int]], *, reflow: bool) -> str:
    parts = []; cursor = start
    for low, high in skipped:
        if high <= cursor: continue
        if low >= end: break
        parts.append(text[cursor:max(cursor, low)])
        cursor = min(end, high)
    parts.append(text[cursor:end]); value = ''.join(parts)
    if reflow:
        # CJK line wrapping must not insert spaces into words. Latin wrapping
        # keeps a separator, including when page furniture occupied the gap.
        value = re.sub(r'(?<=[\u3400-\u9fff])\s*\n\s*(?=[\u3400-\u9fff])', '', value)
        value = re.sub(r'\s*\n\s*', ' ', value)
    return value.strip()


def chunks_for(document: dict[str, Any], section: dict[str, Any], options: NarrationOptions, split: Any) -> list[dict[str, Any]]:
    skipped = skipped_ranges(document, options)
    start, end = section['start'], section['end']
    boundaries = {b['offset']: b['kind'] for b in document.get('boundaries', []) if start < b['offset'] < end and not any(low <= b['offset'] < high for low,high in skipped)} if options.natural_pauses else {}
    points = [start, *sorted(boundaries), end]
    result = []
    pending_pause = 0
    for low, high in zip(points, points[1:]):
        if low != start:
            pending_pause = max(pending_pause, 700 if boundaries[low] == 'chapter' else 350)
        value = spoken_slice(document['text'], low, high, skipped, reflow=document.get('source_format') == 'pdf')
        if not value: continue
        for index, chunk in enumerate(split(value)):
            result.append({'text': chunk, 'pause_before_ms': (180 if index else pending_pause) if result else 0,
                           'source_start': low, 'source_end': high})
        pending_pause = 0
    return result


def preview(document: dict[str, Any], mode: str, target: int, max_sections: int,
            options: NarrationOptions | None = None) -> dict[str, Any]:
    from .document_text import segment
    result = segment(document, mode, target, max_sections)
    options = options_for(document, options)
    result.update(analysis_revision=document.get('analysis_revision'), narration_options=options.model_dump() if options else None,
                  analysis_required=options is None)
    if options is None:
        return result
    skipped = skipped_ranges(document, options)
    for section in result['sections']:
        value = spoken_slice(document['text'], section['start'], section['end'], skipped, reflow=False)
        section['spoken_chars'] = len(value)
        section['speakable'] = bool(value)
    result['spoken_chars'] = sum(s['spoken_chars'] for s in result['sections'])
    result['annotation_counts'] = {kind: sum(a['kind'] == kind for a in document['annotations']) for kind in KINDS}
    result['preview_revision'] = digest([result['preview_revision'], document['analysis_revision'], options.model_dump()])
    return result
