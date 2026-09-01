from __future__ import annotations

import sys
from pathlib import Path

import pytest

import scripts.lock_dependencies as lock_dependencies


def configure_single_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    source = tmp_path / "requirements-api.txt"
    source.write_text("example==1.0\n", encoding="utf-8")
    destination = tmp_path / "requirements-lock" / "linux" / "api.txt"
    destination.parent.mkdir(parents=True)
    monkeypatch.setattr(lock_dependencies, "ROOT", tmp_path)
    monkeypatch.setattr(lock_dependencies, "UV", uv)
    monkeypatch.setattr(lock_dependencies, "ENVIRONMENTS", {"api": source.name})
    monkeypatch.setattr(lock_dependencies, "PLATFORMS", {"linux": "linux-target"})
    return destination


def test_check_uses_existing_lock_as_resolution_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = configure_single_lock(tmp_path, monkeypatch)
    destination.write_text("example==1.0\n", encoding="utf-8")

    def fake_compile(
        name: str, source: str, platform: str, output: Path, *, torch_backend: str | None = None, upgrade: bool = False,
    ) -> None:
        assert (name, source, platform, torch_backend, upgrade) == (
            "api", "requirements-api.txt", "linux-target", None, False,
        )
        assert output.read_text(encoding="utf-8") == "example==1.0\n"

    monkeypatch.setattr(lock_dependencies, "compile_lock", fake_compile)
    monkeypatch.setattr(sys, "argv", ["lock_dependencies.py", "--check"])

    lock_dependencies.main()


@pytest.mark.parametrize("existing", [True, False])
def test_check_reports_changed_or_missing_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    destination = configure_single_lock(tmp_path, monkeypatch)
    if existing:
        destination.write_text("example==1.0\n", encoding="utf-8")

    def fake_compile(
        name: str, source: str, platform: str, output: Path, *, torch_backend: str | None = None, upgrade: bool = False,
    ) -> None:
        output.write_text("example==2.0\n", encoding="utf-8")

    monkeypatch.setattr(lock_dependencies, "compile_lock", fake_compile)
    monkeypatch.setattr(sys, "argv", ["lock_dependencies.py", "--check"])

    with pytest.raises(SystemExit, match=r"requirements-lock[\\/]linux[\\/]api\.txt"):
        lock_dependencies.main()


def test_compile_lock_only_upgrades_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(lock_dependencies, "ROOT", tmp_path)
    monkeypatch.setattr(lock_dependencies, "UV", uv)
    monkeypatch.setattr(
        lock_dependencies.subprocess, "run",
        lambda command, **kwargs: calls.append(command),
    )

    lock_dependencies.compile_lock(
        "asr", "requirements-asr.txt", "linux-target", tmp_path / "asr.txt",
    )
    lock_dependencies.compile_lock(
        "asr", "requirements-asr.txt", "linux-target", tmp_path / "asr-upgrade.txt",
        upgrade=True,
    )

    assert "--upgrade" not in calls[0]
    assert "--upgrade" in calls[1]
    assert calls[0][3:5] == ["--torch-backend", "cu130"]


def test_check_and_upgrade_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys, "argv", ["lock_dependencies.py", "--check", "--upgrade"],
    )

    with pytest.raises(SystemExit) as error:
        lock_dependencies.main()

    assert error.value.code == 2
