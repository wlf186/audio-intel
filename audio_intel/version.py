from __future__ import annotations

import re
import subprocess
from pathlib import Path


RELEASE_VERSION = "0.1.7"

_DESCRIBE_PATTERN = re.compile(
    r"^v(?P<version>\d+\.\d+\.\d+)-(?P<distance>\d+)-g(?P<commit>[0-9a-f]+)(?P<dirty>-dirty)?$",
)
_COMMIT_PATTERN = re.compile(r"^(?P<commit>[0-9a-f]+)(?P<dirty>-dirty)?$")


def _version_tuple(value: str) -> tuple[int, int, int]:
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def version_from_describe(describe: str | None, fallback: str = RELEASE_VERSION) -> str:
    """Convert ``git describe`` output into a SemVer-compatible public version."""
    if not describe:
        return fallback

    match = _DESCRIBE_PATTERN.fullmatch(describe.strip())
    if match:
        tagged_version = match.group("version")
        distance = int(match.group("distance"))
        commit = match.group("commit")
        dirty = bool(match.group("dirty"))
        if _version_tuple(tagged_version) < _version_tuple(fallback):
            suffix = f"g{commit}"
            return f"{fallback}+{suffix}{'.dirty' if dirty else ''}"
        if distance == 0 and not dirty:
            return tagged_version
        suffix = f"{distance}.g{commit}"
        return f"{tagged_version}+{suffix}{'.dirty' if dirty else ''}"

    match = _COMMIT_PATTERN.fullmatch(describe.strip())
    if match:
        commit = match.group("commit")
        dirty = bool(match.group("dirty"))
        return f"{fallback}+g{commit}{'.dirty' if dirty else ''}"
    return fallback


def _describe_worktree() -> str | None:
    repository = Path(__file__).resolve().parents[1]
    if not (repository / ".git").exists():
        return None
    try:
        result = subprocess.run(
            [
                "git", "describe", "--tags", "--long", "--dirty", "--always",
                "--match", "v[0-9]*.[0-9]*.[0-9]*",
            ],
            cwd=repository,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def resolve_version() -> str:
    return version_from_describe(_describe_worktree())
