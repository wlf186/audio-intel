from __future__ import annotations

import argparse
import json
import sqlite3
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


VALID_PROFILES = {"full", "cpu"}


def installed_torch_profile() -> str:
    try:
        torch_version = version("torch").lower()
    except PackageNotFoundError:
        return "missing"
    if "+cpu" in torch_version:
        return "cpu"
    if "+cu" in torch_version:
        return "full"
    return "unknown"


def active_processes(run_dir: Path) -> list[int]:
    try:
        import psutil
    except ImportError:
        return []
    candidates: set[int] = set()
    for path in run_dir.glob("*.pid"):
        try:
            candidates.add(int(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            continue
    for path in run_dir.glob("*-executor.json"):
        try:
            candidates.add(int(json.loads(path.read_text(encoding="utf-8"))["pid"]))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    return sorted(pid for pid in candidates if psutil.pid_exists(pid))


def nonterminal_jobs(database: Path) -> int:
    if not database.is_file():
        return 0
    try:
        with sqlite3.connect(database) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            if table is None:
                return 0
            row = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','cancelling')"
            ).fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        raise SystemExit(f"Unable to verify queued jobs before profile switch: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and guard inference runtime profiles")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("detect")
    validate = subparsers.add_parser("validate")
    validate.add_argument("expected", choices=sorted(VALID_PROFILES))
    guard = subparsers.add_parser("guard-switch")
    guard.add_argument("--run-dir", type=Path, required=True)
    guard.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args()

    if arguments.action == "detect":
        print(installed_torch_profile())
        return
    if arguments.action == "validate":
        actual = installed_torch_profile()
        if actual != arguments.expected:
            raise SystemExit(
                f"Inference runtime profile mismatch: expected {arguments.expected}, detected {actual}"
            )
        return

    active = active_processes(arguments.run_dir)
    if active:
        raise SystemExit(
            "Stop all services before changing deployment profile; active process IDs: "
            + ", ".join(str(pid) for pid in active)
        )
    pending = nonterminal_jobs(arguments.database)
    if pending:
        raise SystemExit(
            f"Drain or cancel all queued/running tasks before changing deployment profile ({pending} remain)"
        )


if __name__ == "__main__":
    main()
