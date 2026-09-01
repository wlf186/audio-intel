from __future__ import annotations

import pytest

from audio_intel.hotwords import derive_voiceprint_short_name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("张三丰", "三丰"),
        ("李承晚", "承晚"),
        ("欧阳娜娜", "娜娜"),
        ("张　三丰", "三丰"),
        ("Michael Jordan", "Michael"),
        ("Michael B. Jordan", "Michael"),
        ("Jean-Claude Van Damme", "Jean-Claude"),
        ("Sean O'Neal", "Sean"),
    ],
)
def test_derive_voiceprint_short_name(name: str, expected: str) -> None:
    assert derive_voiceprint_short_name(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "张伟",
        "司马光",
        "迪丽热巴",
        "阿里木·巴图尔",
        "Michael",
        "M. Jordan",
        "Dr. Michael Jordan",
        "Dr Michael Jordan",
        "Jordan, Michael",
        "张 Michael",
    ],
)
def test_derive_voiceprint_short_name_skips_ambiguous_names(name: str) -> None:
    assert derive_voiceprint_short_name(name) is None
