from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from audio_intel import RELEASE_VERSION, __version__
from audio_intel.version import version_from_describe


ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    ("describe", "expected"),
    (
        ("v0.1.6-0-g3a478297", "0.1.6"),
        ("v0.1.6-0-g3a478297-dirty", "0.1.6+0.g3a478297.dirty"),
        ("v0.1.6-3-g8adfe461", "0.1.6+3.g8adfe461"),
        ("v0.1.6-3-g8adfe461-dirty", "0.1.6+3.g8adfe461.dirty"),
        ("v0.1.5-1-g8adfe461", "0.1.6+g8adfe461"),
        ("8adfe461", "0.1.6+g8adfe461"),
        ("8adfe461-dirty", "0.1.6+g8adfe461.dirty"),
        (None, "0.1.6"),
        ("not-a-version", "0.1.6"),
    ),
)
def test_version_from_git_describe(describe: str | None, expected: str) -> None:
    assert version_from_describe(describe) == expected


def test_release_tag_matches_fallback_in_tag_ci() -> None:
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        return
    tag = os.environ.get("GITHUB_REF_NAME", "")
    assert tag == f"v{RELEASE_VERSION}"
    assert __version__ == RELEASE_VERSION


def test_frontend_package_uses_release_fallback() -> None:
    package = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert package["version"] == RELEASE_VERSION
