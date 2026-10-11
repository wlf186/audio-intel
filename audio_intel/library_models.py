"""Portable library v2 contract with backward-compatible v1 imports."""
from __future__ import annotations

import base64
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import normalize_person_name, normalize_person_note, normalize_sample_name
from .hotwords import normalize_hotword_name, normalize_hotword_terms, RESERVED_SYSTEM_HOTWORD_LIST_NAMES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ArchiveWord(StrictModel):
    text: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    speaker: str | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end < self.start:
            raise ValueError("Word end precedes start")
        return self


class ArchiveAudio(StrictModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)


class ArchiveEmbedding(StrictModel):
    model: Literal["CAM++/campplus_cn_common"]
    revision: str
    data: str = Field(max_length=65536)

    @field_validator("data")
    @classmethod
    def vector(cls, value: str) -> str:
        import numpy as np
        raw = base64.b64decode(value, validate=True)
        if not raw or len(raw) % 4:
            raise ValueError("Invalid float32 embedding")
        vector = np.frombuffer(raw, dtype="<f4")
        if not np.isfinite(vector).all() or not 0 < float(np.linalg.norm(vector)) < float("inf"):
            raise ValueError("Invalid embedding values")
        return value


class ArchiveSample(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str
    state: Literal["ready", "failed"]
    language: str
    transcript: str | None = None
    words: list[ArchiveWord] = Field(default_factory=list)
    duration: float | None = Field(None, gt=0)
    error_message: str | None = None
    created_at: str
    updated_at: str
    audio: ArchiveAudio | None = None
    embedding: ArchiveEmbedding | None = None

    _name = field_validator("name")(normalize_sample_name)

    @model_validator(mode="after")
    def usable(self):
        if self.state == "ready" and self.audio is None:
            raise ValueError("A ready sample requires audio")
        if len({(word.start, word.end, word.text) for word in self.words}) != len(self.words):
            raise ValueError("Duplicate words")
        if self.duration is not None and any(word.end > self.duration + .1 for word in self.words):
            raise ValueError("Word exceeds sample duration")
        if any(a.start > b.start or a.end > b.end for a, b in zip(self.words, self.words[1:])):
            raise ValueError("Word timestamps must be ordered")
        return self


class ArchivePerson(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str
    group_id: str | None = None
    note: str | None = None
    include_in_hotword_library: bool
    aliases: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    samples: list[ArchiveSample] = Field(default_factory=list)

    _name = field_validator("name")(normalize_person_name)
    _note = field_validator("note")(normalize_person_note)


class ArchiveHotwords(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str
    terms: list[str]
    created_at: str
    updated_at: str

    _terms = field_validator("terms")(normalize_hotword_terms)

    @field_validator("name")
    @classmethod
    def name_valid(cls, value: str):
        name = normalize_hotword_name(value)
        if name.casefold() in {item.casefold() for item in RESERVED_SYSTEM_HOTWORD_LIST_NAMES}:
            raise ValueError("Reserved system hotword name")
        return name


class ArchiveGroup(StrictModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str
    created_at: str
    updated_at: str

    @field_validator("name")
    @classmethod
    def normalize(cls, value: str) -> str:
        from .voiceprint_groups import normalize_group_name
        return normalize_group_name(value)


class LibraryArchive(StrictModel):
    format: Literal["audio-intel-library"] = "audio-intel-library"
    contract_version: Literal[1, 2] = 1
    exported_at: str
    source_version: str
    people: list[ArchivePerson]
    hotword_lists: list[ArchiveHotwords]
    groups: list[ArchiveGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def membership(self):
        from .db import person_name_key
        from .voiceprint_groups import UNGROUPED_ID, UNGROUPED_NAME
        if self.contract_version == 1:
            if self.groups or any(person.group_id is not None for person in self.people):
                raise ValueError("Groups require library contract v2")
            return self
        ids = {group.id for group in self.groups}
        if len(ids) != len(self.groups) or len({person_name_key(group.name) for group in self.groups}) != len(self.groups):
            raise ValueError("Duplicate group ID or name")
        if not any(group.id == UNGROUPED_ID and group.name == UNGROUPED_NAME for group in self.groups):
            raise ValueError("Library v2 requires the built-in ungrouped group")
        if any(person.group_id not in ids for person in self.people):
            raise ValueError("Every v2 person requires a declared group")
        return self


class LibraryCounts(StrictModel):
    groups: int = 0
    people: int = 0
    samples: int = 0
    hotword_lists: int = 0


class LibraryModeRequest(StrictModel):
    mode: Literal["merge", "replace"] = "merge"


class LibraryApplyRequest(LibraryModeRequest):
    preview_revision: str


class LibraryPreview(StrictModel):
    mode: Literal["merge", "replace"]
    preview_revision: str
    source: LibraryCounts
    target: LibraryCounts
    added: LibraryCounts
    removed: LibraryCounts
    merged_people: int = 0
    merged_hotword_lists: int = 0
    skipped_samples: int = 0
    renamed_samples: int = 0
    warnings: list[str] = Field(default_factory=list)


class LibraryIdMap(StrictModel):
    groups: dict[str, str] = Field(default_factory=dict)
    people: dict[str, str]
    samples: dict[str, str]
    hotword_lists: dict[str, str]


class LibraryResult(LibraryPreview):
    id_map: LibraryIdMap


class LibraryImportResponse(StrictModel):
    id: str
    name: str
    state: Literal["queued", "validating", "ready", "applying", "completed", "failed", "expired"]
    size_bytes: int
    created_at: str
    updated_at: str
    mode: Literal["merge", "replace"] | None = None
    error: str | None = None
    preview: LibraryPreview | None = None
    result: LibraryResult | None = None
    backup_url: str | None = None
