from pathlib import Path

import pytest

from audio_intel.purge import allocated_bytes


@pytest.mark.parametrize("nested", [False, True])
def test_allocation_scan_tolerates_entry_disappearing_after_discovery(tmp_path, monkeypatch, nested):
    stable = tmp_path / "retained.wav"
    stable.write_bytes(b"retained audio" * 1024)
    transient = tmp_path / "audio_intel.sqlite3-wal"
    transient.write_bytes(b"transient" * 1024)
    target = tmp_path if nested else transient
    expected = allocated_bytes(tmp_path) - allocated_bytes(transient) if nested else 0
    original = Path.lstat
    observed = []

    def disappeared(path):
        if path == transient:
            observed.append(path)
            raise FileNotFoundError(str(path))
        return original(path)

    # Discovery still succeeds; only the later metadata read loses the entry.
    monkeypatch.setattr(Path, "lstat", disappeared)
    assert allocated_bytes(target) == expected
    assert observed
    assert stable.read_bytes() == b"retained audio" * 1024


def test_allocation_scan_does_not_hide_permission_failure(tmp_path, monkeypatch):
    target = tmp_path / "protected.wav"
    target.write_bytes(b"protected")
    original = Path.lstat

    def denied(path):
        if path == target:
            raise PermissionError(str(path))
        return original(path)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(PermissionError):
        allocated_bytes(target)
