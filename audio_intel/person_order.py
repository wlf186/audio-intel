"""Deterministic, offline ordering for public voiceprint person lists."""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from typing import Any

from pypinyin import lazy_pinyin

# Surname readings take precedence only at the beginning of a Chinese name.
# Match compound surnames first; do not alter the library's general dictionary.
_SURNAMES = {
    "尉迟": "yuchi", "长孙": "zhangsun", "万俟": "moqi",
    "单": "shan", "曾": "zeng", "区": "ou", "解": "xie",
    "仇": "qiu", "查": "zha", "乐": "yue", "朴": "piao",
    "翟": "zhai", "员": "yun", "缪": "miao", "秘": "bi",
}


@lru_cache(maxsize=4096)
def romanized_name(value: str) -> str:
    name = unicodedata.normalize("NFKC", value).strip()
    prefix = ""
    for surname in sorted(_SURNAMES, key=len, reverse=True):
        if name.startswith(surname):
            prefix = _SURNAMES[surname]
            name = name[len(surname):]
            break
    result = unicodedata.normalize("NFKD", prefix + "".join(lazy_pinyin(name)))
    return "".join(c for c in result.casefold() if not unicodedata.combining(c) and not c.isspace())


def sort_initial(name: str) -> str:
    key = romanized_name(name)
    return key[0].upper() if key and "a" <= key[0] <= "z" else "#"


def person_order(person: dict[str, Any]) -> tuple[Any, ...]:
    name = str(person["name"])
    return (sort_initial(name) == "#", romanized_name(name),
            unicodedata.normalize("NFKC", name).casefold(),
            unicodedata.normalize("NFKC", person.get("note") or "").casefold(), person["id"])
