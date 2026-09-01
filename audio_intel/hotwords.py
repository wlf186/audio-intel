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
SYSTEM_HOTWORD_LIST_NAME = "声纹库人名（全名）"
SYSTEM_SHORT_HOTWORD_LIST_ID = "hotwords_voiceprint_people_short"
SYSTEM_SHORT_HOTWORD_LIST_NAME = "声纹库人名（去姓）"
LEGACY_SYSTEM_HOTWORD_LIST_NAME = "声纹库人名"
RESERVED_SYSTEM_HOTWORD_LIST_NAMES = frozenset({
    LEGACY_SYSTEM_HOTWORD_LIST_NAME,
    SYSTEM_HOTWORD_LIST_NAME,
    SYSTEM_SHORT_HOTWORD_LIST_NAME,
})

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_LATIN_FIRST_NAME = re.compile(r"[A-Za-z]{2,}(?:[-'][A-Za-z]+)*")
_LATIN_NAME_TOKEN = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*\.?")
_LATIN_TITLES = frozenset({
    "dame", "dr", "lady", "lord", "miss", "mr", "mrs", "ms", "prof",
    "professor", "rev", "reverend", "sir",
})

# The 100 most frequent single-character surnames published in the Ministry of
# Education's 2011 Chinese language report. Keeping the list local makes name
# derivation deterministic and preserves offline runtime behavior.
_COMMON_SINGLE_SURNAMES = frozenset(
    "李王张陈刘杨周黄吴赵孙马胡徐郭林朱金郑高何宋罗梁谢姚韩冯许邓曹丁蔡蒋于杜叶唐温沈彭袁姜余潘万苏曾董汪鲁范田陆白方贾肖谭崔雷吕石钟任韦康卢江牛魏程孟安廖夏戴邵龙钱齐秦毛汤邱洪乔俞华莫梅熊薛穆易侯尹顾段傅"
)

# Common compound surnames enumerated in the Ministry of Public Security's
# 2021 national name report.
_COMMON_COMPOUND_SURNAMES = frozenset({
    "夏侯", "司徒", "司马", "完颜", "尉迟", "慕容",
    "欧阳", "申屠", "皇甫", "诸葛", "贺兰", "长孙", "令狐", "上官",
})


def derive_voiceprint_short_name(value: str) -> str | None:
    """Return a conservative surname-free ASR hint for a stored person name."""
    name = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    if not name:
        return None

    compact = name.replace(" ", "")
    if compact and all(_is_han_character(character) for character in compact):
        if compact[:2] in _COMMON_COMPOUND_SURNAMES:
            given_name = compact[2:]
        elif compact[0] in _COMMON_SINGLE_SURNAMES:
            given_name = compact[1:]
        else:
            return None
        return given_name if len(given_name) == 2 else None

    tokens = name.split(" ")
    first = tokens[0]
    if (
        len(tokens) < 2
        or first.casefold() in _LATIN_TITLES
        or _LATIN_FIRST_NAME.fullmatch(first) is None
        or any(_LATIN_NAME_TOKEN.fullmatch(token) is None for token in tokens[1:])
    ):
        return None
    return first


def _is_han_character(value: str) -> bool:
    codepoint = ord(value)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x3134F
    )


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
