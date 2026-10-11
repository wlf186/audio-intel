"""Explicit, scoped upgrade backups and post-acceptance cleanup (standard library only)."""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parent.parent
MARKER = "upgrade-backup.json"


def create(root: Path, data: Path, scope: str) -> Path:
    root, data = root.resolve(strict=True), data.resolve(strict=True)
    backups = root / "backups"
    if scope not in {"database", "full"}:
        raise ValueError("Choose database or full explicitly")
    if backups.is_symlink() or backups.resolve().is_relative_to(data):
        raise ValueError("Upgrade backups must stay outside the data directory")
    with closing(sqlite3.connect((data / "audio_intel.sqlite3").as_uri() + "?mode=ro", uri=True)) as source:
        backups.mkdir(exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix="upgrade-" + scope + "-", dir=backups))
        marker = {"purpose": "audio-intel-upgrade", "root": str(root), "path": str(destination),
                  "data_dir": str(data), "scope": scope, "created_at": datetime.now(timezone.utc).isoformat(), "state": "creating"}
        (destination / MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
        if scope == "full":
            shutil.copytree(data, destination / "data", symlinks=True)
            saved = destination / "data" / "audio_intel.sqlite3"
            # Always create an independent snapshot, even if the source DB is a symlink.
            saved.unlink()
            for suffix in ("-wal", "-shm", "-journal"):
                saved.with_name(saved.name + suffix).unlink(missing_ok=True)
        else:
            saved = destination / "audio_intel.sqlite3"
        with closing(sqlite3.connect(saved)) as target:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or target.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Backup integrity check failed; stop the upgrade")
        marker["state"] = "verified"
        (destination / MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def cleanup(root: Path, value: str, verified: bool) -> int:
    if not verified:
        raise ValueError("Complete deployment acceptance before cleanup; pass --verified afterwards")
    if not value.strip():
        raise ValueError("Provide the exact recorded backup directory")
    root = root.resolve(strict=True)
    backups, path = root / "backups", Path(value)
    if path.is_symlink() or backups.is_symlink():
        raise ValueError("Symlink backup directories cannot be cleaned")
    path = path.resolve(strict=True)
    if path.parent != backups.resolve(strict=True) or not path.name.startswith("upgrade-"):
        raise ValueError("Cleanup accepts one recorded upgrade directory directly under project backups")
    marker_path = path / MARKER
    if marker_path.is_symlink():
        raise ValueError("Invalid backup marker")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if not isinstance(marker, dict) or marker.get("purpose") != "audio-intel-upgrade" or marker.get("root") != str(root) or marker.get("path") != str(path):
        raise ValueError("Backup identity does not match this project and directory")
    size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())
    shutil.rmtree(path)
    return size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    backup = commands.add_parser("create")
    backup.add_argument("--data-dir", required=True, type=Path, help="Actual deployment data directory; never inferred from a client shell")
    backup.add_argument("--scope", required=True, choices=("database", "full"))
    clean = commands.add_parser("cleanup")
    clean.add_argument("directory", help="Exact directory printed by create")
    clean.add_argument("--verified", action="store_true", help="Confirm complete local upgrade acceptance succeeded")
    arguments = parser.parse_args()
    try:
        if arguments.action == "create":
            directory = create(ROOT, arguments.data_dir, arguments.scope)
            print("Verified backup:", directory)
            size = sum(item.stat().st_size for item in directory.rglob("*") if item.is_file() and not item.is_symlink())
            print(f"Backup scope: {arguments.scope}; bytes: {size}")
        else:
            print("Removed upgrade backup; bytes:", cleanup(ROOT, arguments.directory, arguments.verified))
    except (OSError, sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Upgrade backup operation failed: {exc}\n")


if __name__ == "__main__":
    main()
