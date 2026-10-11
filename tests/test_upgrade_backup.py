import json
from pathlib import Path
import sqlite3

import pytest

from scripts.upgrade_backup import create, cleanup, MARKER


def test_database_backup_includes_wal_without_copying_media_and_exact_cleanup(tmp_path):
    data = tmp_path / "数据目录"
    data.mkdir()
    media = data / "large-video.mp4"
    media.write_bytes(b"original media must remain")
    with sqlite3.connect(data / "audio_intel.sqlite3") as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE history(text TEXT)")
        connection.execute("INSERT INTO history VALUES('历史任务')")
        connection.commit()
        saved = create(tmp_path, data, "database")
        with sqlite3.connect(saved / "audio_intel.sqlite3") as copy:
            assert copy.execute("SELECT text FROM history").fetchone()[0] == "历史任务"
        assert not list(saved.rglob("*.mp4"))
        with pytest.raises(ValueError):
            cleanup(tmp_path, str(saved), False)
        assert saved.exists()
        unrelated = tmp_path / "backups" / "daily"
        unrelated.mkdir()
        assert cleanup(tmp_path, str(saved), True) > 0
        assert not saved.exists() and unrelated.exists()
        assert media.read_bytes() == b"original media must remain"
        assert connection.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1


def test_full_scope_and_cleanup_reject_root_outside_and_forged_identity(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "audio_intel.sqlite3") as connection:
        connection.execute("CREATE TABLE jobs(id TEXT)")
    (data / "video.mp4").write_bytes(b"full scope media")
    saved = create(tmp_path, data, "full")
    assert (saved / "data/video.mp4").read_bytes() == b"full scope media"
    for path in ("", str(tmp_path / "backups"), str(data), str(tmp_path)):
        with pytest.raises(ValueError):
            cleanup(tmp_path, path, True)
    marker = json.loads((saved / MARKER).read_text(encoding="utf-8"))
    marker["path"] = str(data)
    (saved / MARKER).write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError):
        cleanup(tmp_path, str(saved), True)
    assert saved.exists() and data.exists()


def test_full_backup_never_writes_through_database_symlink(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    original = tmp_path / "outside.sqlite3"
    with sqlite3.connect(original) as connection:
        connection.execute("CREATE TABLE history(text TEXT)")
        connection.execute("INSERT INTO history VALUES('retain')")
    try:
        (data / "audio_intel.sqlite3").symlink_to(original)
    except OSError:
        pytest.skip("Creating symlinks requires native Windows privileges")
    before = original.read_bytes()
    saved = create(tmp_path, data, "full")
    snapshot = saved / "data/audio_intel.sqlite3"
    assert not snapshot.is_symlink()
    with sqlite3.connect(snapshot) as connection:
        assert connection.execute("SELECT text FROM history").fetchone()[0] == "retain"
        connection.execute("DELETE FROM history")
    assert original.read_bytes() == before
    cleanup(tmp_path, str(saved), True)
    assert original.read_bytes() == before
