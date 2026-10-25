"""Pure, offline transcript exports; never rewrite persistent task artifacts."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import html
import json
import math
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .asr_speakers import speaker_identity, speaker_label
from .utils import timecode

MIME_TYPES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf", "json": "application/json", "txt": "text/plain", "srt": "application/x-subrip", "vtt": "text/vtt"}


class Replacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=8000, description="区分大小写的字面原词 / Case-sensitive literal source")
    target: str = Field(max_length=8000, description="替换词，可为空 / Replacement, may be empty")

    @field_validator("source")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Replacement source cannot be blank")
        return value


class TranscriptExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["pdf", "docx", "json", "txt", "srt", "vtt"] = "pdf"
    text_version: Literal["original", "optimized", "comparison"] = "original"
    optimization_id: str | None = Field(None, pattern=r"^[a-f0-9]{32}$")
    redact: bool = Field(True, description="缩写姓名、遮盖备注并替换正文 / Abbreviate names, mask notes and replace transcript keywords")
    replacements: list[Replacement] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique(self):
        if self.text_version != 'original':
            if not self.optimization_id:
                raise ValueError('optimization_id is required for optimized exports')
            allowed = ('pdf', 'docx') if self.text_version == 'comparison' else ('pdf', 'docx', 'txt')
            if self.format not in allowed:
                raise ValueError('This format requires the original transcript')
        elif self.optimization_id:
            raise ValueError('optimization_id requires an optimized text_version')
        if len({rule.source for rule in self.replacements}) != len(self.replacements):
            raise ValueError("Duplicate replacement source")
        return self


def initials(name: str, fallback: str) -> str:
    from pypinyin import Style, lazy_pinyin
    from .person_order import _SURNAMES
    name = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", name))
    name = "".join(c for c in name if not unicodedata.combining(c))
    if re.fullmatch(r"Speaker[ _]\d+", name, re.IGNORECASE):
        return name
    parts = re.findall(r"[\u3400-\u9fff]|[^\W\d_]+", name, re.UNICODE)
    output = []
    compound = {"尉迟": "YC", "长孙": "ZS", "万俟": "MQ"}
    for surname, letters in compound.items():
        if name.startswith(surname):
            output.extend(letters)
            parts = parts[len(surname):]
            break
    for index, part in enumerate(parts):
        if len(part) == 1 and "\u3400" <= part <= "\u9fff":
            letter = _SURNAMES[part][0] if not output and index == 0 and part in _SURNAMES else lazy_pinyin(part, style=Style.FIRST_LETTER)[0]
        else:
            letter = part[0]
        if re.fullmatch(r"[A-Za-z]", letter):
            output.append(letter.upper())
    return "".join(output) or fallback


def mask_note(note: str | None) -> str | None:
    if not note:
        return None
    hidden = math.ceil(len(note) / 2)
    prefix = (len(note) - hidden) // 2
    return note[:prefix] + "*" * hidden + note[prefix + hidden:]


class LiteralReplacer:
    def __init__(self, rules: list[Replacement]):
        self.targets = {rule.source: rule.target for rule in rules}
        self.pattern = re.compile("|".join(re.escape(source) for source in sorted(self.targets, key=len, reverse=True))) if rules else None

    def text(self, text: str) -> str:
        return self.pattern.sub(lambda match: self.targets[match.group()], text) if self.pattern else text

    def words(self, words: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
        clean = [{key: word[key] for key in ("text", "start", "end", "speaker") if key in word} for word in words]
        if not clean or not self.pattern:
            return clean
        positions = []
        cursor = 0
        for word in clean:
            value = str(word.get("text", ""))
            start = text.find(value, cursor) if value else cursor
            if start < 0:
                # Historical aligners may omit spaces from their token text.
                text = "".join(str(item.get("text", "")) for item in clean)
                positions = []
                cursor = 0
                for item in clean:
                    end = cursor + len(str(item.get("text", "")))
                    positions.append((cursor, end))
                    cursor = end
                break
            end = start + len(value)
            positions.append((start, end))
            cursor = end
        spans = []
        for match in self.pattern.finditer(text):
            touched = [i for i, (start, end) in enumerate(positions) if end > match.start() and start < match.end()]
            if touched:
                first, last = touched[0], touched[-1]
                if spans and first <= spans[-1][1]:
                    spans[-1] = (spans[-1][0], max(last, spans[-1][1]))
                else:
                    spans.append((first, last))
        output, cursor = [], 0
        for first, last in spans:
            output.extend(clean[cursor:first])
            item = dict(clean[first])
            item["text"] = self.text(text[positions[first][0]:positions[last][1]])
            item["end"] = clean[last].get("end", item.get("end"))
            output.append(item)
            cursor = last + 1
        output.extend(clean[cursor:])
        return output


def export_result(result: dict[str, Any], policy: TranscriptExportRequest) -> dict[str, Any]:
    if not policy.redact:
        return deepcopy(result)
    replacer = LiteralReplacer(policy.replacements)
    # Unknown historical metadata can contain source text, identities or file paths.
    safe_fields = ("_optimized", "text_version", "language", "duration", "timestamp_precision", "diarization_mode", "model", "model_name", "model_revision", "compute_device", "compute_device_name", "precision", "quantized")
    exported = {key: deepcopy(result[key]) for key in safe_fields if key in result}
    identities = list(result.get("speakers", []))
    known = {speaker["id"] for speaker in identities}
    for segment in result.get("segments", []):
        identifier = segment.get("speaker", "Speaker_0")
        if identifier not in known:
            identities.append({"id": identifier, "name": segment.get("speaker_label") or identifier})
            known.add(identifier)
    names = [initials(speaker_identity(speaker)[0], f"S-{i + 1}") for i, speaker in enumerate(identities)]
    counts, numbers, labels, speakers = Counter(names), Counter(), {}, []
    for speaker, short in zip(identities, names):
        if counts[short] > 1:
            numbers[short] += 1
            name = f"{short}-{numbers[short]}"
        else:
            name = short
        note = mask_note(speaker_identity(speaker)[1])
        label = speaker_label(name, note)
        labels[speaker["id"]] = label
        speakers.append({"id": speaker["id"], "name": name, "note": note, "label": label})
    segments = []
    for segment in result.get("segments", []):
        item = {key: deepcopy(segment[key]) for key in ("id", "start", "end", "speaker", "timestamp_precision") if key in segment}
        original = str(segment.get("text", ""))
        item.update(text=replacer.text(original), speaker_label=labels.get(item.get("speaker"), "Speaker"))
        if "original_text" in segment:
            item["original_text"] = replacer.text(segment["original_text"])
        if "words" in segment:
            item["words"] = replacer.words(segment["words"], original)
        segments.append(item)
    exported.update(text=replacer.text(str(result.get("text", ""))), speakers=speakers, segments=segments)
    if result.get("video"):
        video = result["video"]
        exported["video"] = {key: deepcopy(video[key]) for key in ("revision", "duration", "time_origin", "roi", "export_hidden_scene_ids") if key in video}
        for key in ("scenes", "original_scenes"):
            if key in video:
                exported["video"][key] = [{field: deepcopy(scene[field]) for field in ("id", "start", "end", "frame_time", "image") if field in scene} for scene in video[key]]
    return exported


def format_transcript(result: dict[str, Any], format: str) -> str:
    segments = result.get("segments", [])
    if format == "json":
        return json.dumps({key: value for key, value in result.items() if key != "artifacts"}, ensure_ascii=False, indent=2)
    if format == "txt":
        return "\n".join(f"[{timecode(x['start'])} - {timecode(x['end'])}] {x['speaker_label']}: {x['text']}" for x in segments)
    if format == "srt":
        return "\n\n".join(f"{i}\n{timecode(x['start'], ',')} --> {timecode(x['end'], ',')}\n{x['speaker_label']}: {x['text']}" for i, x in enumerate(segments, 1))
    if format == "vtt":
        return "WEBVTT\n\n" + "\n\n".join(f"{timecode(x['start'])} --> {timecode(x['end'])}\n<v {html.escape(x['speaker_label'], quote=False)}>{html.escape(x['text'], quote=False)}" for x in segments)
    raise ValueError("Unsupported transcript format")


def optimized_export_result(job: dict, policy: TranscriptExportRequest) -> dict:
    from . import optimization_engine as engine, optimization_store as store
    from fastapi import HTTPException
    run = store.get(policy.optimization_id or '')
    if run['job_id'] != job['id']:
        raise HTTPException(404, 'Optimization not found')
    if not run['published'] or run['state'] != 'succeeded':
        raise HTTPException(409, '请等待优化稿完成 / A completed draft is required')
    try:
        draft = engine.reading_result(job['result'], run['id'], complete=True)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    result = deepcopy(job['result'])
    result.update(_optimized=True, text_version=policy.text_version)
    result['segments'] = []
    for paragraph in draft['paragraphs']:
        item = {key: paragraph[key] for key in ('speaker', 'speaker_label', 'start', 'end', 'text')}
        item['id'] = paragraph['ordinal']
        if policy.text_version == 'comparison':
            item['original_text'] = paragraph['original_text']
        result['segments'].append(item)
    result['text'] = '\n'.join(p['text'] for p in result['segments'])
    result.pop('artifacts', None)
    return export_result(result, policy)
