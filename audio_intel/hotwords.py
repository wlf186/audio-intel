from __future__ import annotations

import re
import unicodedata
from typing import Any


MAX_HOTWORD_LISTS = 100
MAX_TERMS_PER_LIST = 200
MAX_SELECTED_LISTS = 8
MAX_SELECTED_TERMS = 500
MAX_HOTWORD_PROMPT_CHARS = 8_000
MAX_HOTWORD_NAME_CHARS = 80
MAX_HOTWORD_TERM_CHARS = 64
SYSTEM_HOTWORD_LIST_ID = "hotwords_voiceprint_people"
SYSTEM_HOTWORD_LIST_NAME = "声纹库人名"

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


def normalize_hotword_name(value: str) -> str:
    name = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not name:
        raise ValueError("Hotword list name is required")
    if len(name) > MAX_HOTWORD_NAME_CHARS:
        raise ValueError(f"Hotword list name must not exceed {MAX_HOTWORD_NAME_CHARS} characters")
    return name


def hotword_name_key(value: str) -> str:
    return normalize_hotword_name(value).casefold()


def normalize_hotword_terms(values: list[Any]) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("terms must be an array")
    terms: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("Every hotword term must be a string")
        term = " ".join(unicodedata.normalize("NFKC", raw).strip().split())
        if not term:
            continue
        if _CONTROL_CHARACTERS.search(term):
            raise ValueError("Hotword terms must not contain control characters")
        if len(term) > MAX_HOTWORD_TERM_CHARS:
            raise ValueError(f"Hotword terms must not exceed {MAX_HOTWORD_TERM_CHARS} characters")
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    if not terms:
        raise ValueError("A hotword list must contain at least one term")
    if len(terms) > MAX_TERMS_PER_LIST:
        raise ValueError(f"A hotword list must not contain more than {MAX_TERMS_PER_LIST} terms")
    return terms


def parse_hotword_list_ids(value: str | None) -> list[str]:
    ids = list(dict.fromkeys(item.strip() for item in str(value or "").split(",") if item.strip()))
    if len(ids) > MAX_SELECTED_LISTS:
        raise ValueError(f"No more than {MAX_SELECTED_LISTS} hotword lists may be selected")
    return ids


def compile_hotword_context(
    raw_context: str,
    hotword_lists: list[dict[str, Any]],
) -> tuple[str, int]:
    terms: list[str] = []
    seen: set[str] = set()
    for item in sorted(hotword_lists, key=lambda entry: (str(entry["name_key"]), str(entry["id"]))):
        for term in item["terms"]:
            key = str(term).casefold()
            if key not in seen:
                seen.add(key)
                terms.append(str(term))
    if len(terms) > MAX_SELECTED_TERMS:
        raise ValueError(f"Selected hotword lists contain more than {MAX_SELECTED_TERMS} unique terms")
    vocabulary = f"Vocabulary: {', '.join(terms)}." if terms else ""
    if len(vocabulary) > MAX_HOTWORD_PROMPT_CHARS:
        raise ValueError(f"Generated hotword prompt exceeds {MAX_HOTWORD_PROMPT_CHARS} characters")
    context = raw_context.strip()
    if vocabulary:
        context = f"{context}\n\n{vocabulary}" if context else vocabulary
    return context, len(terms)
