from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EMPTY_ARGUMENT_FILTER = "$ExtraArgs = @($ExtraArgs | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })"


def test_windows_setup_filters_empty_forwarded_arguments() -> None:
    for relative_path in ("service.ps1", "scripts/bootstrap.ps1"):
        source = (ROOT / relative_path).read_text(encoding="utf-8-sig")
        assert EMPTY_ARGUMENT_FILTER in source
