"""Brand assets shared by the workbench and self-contained exports."""

from functools import lru_cache
from pathlib import Path
import re


@lru_cache(maxsize=1)
def stylesheet() -> str:
    return (Path(__file__).parent / "assets" / "theme.css").read_text(encoding="utf-8")


def print_color(role: str) -> str:
    match = re.search(rf"--reader-{re.escape(role)}:\s*#([\da-fA-F]{{6}});", stylesheet())
    if not match:
        raise ValueError(f"Missing print color for {role}")
    return match.group(1).upper()
