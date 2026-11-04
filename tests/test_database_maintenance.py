from dataclasses import replace
import sqlite3
import time

import pytest

from audio_intel import db
from audio_intel import database_maintenance as maintenance
from audio_intel.config import settings


@pytest.fixture
def local(tmp_path, monkeypatch):
    config = replace(settings, data_dir=tmp_path / 'data', min_free_disk_bytes=0)
    monkeypatch.setattr(db, 'settings', config)
    db.init_db()
    monkeypatch.setattr(maintenance, 'MIN_FREE', 4096)
    with db.connect() as connection:
        connection.execute('CREATE TABLE ballast(value BLOB)')
        connection.execute('INSERT INTO ballast VALUES(zeroblob(1048576))')
        connection.execute('DELETE FROM ballast')
    return config


def ready(config):
    manager = maintenance.Maintenance(config)
    manager.last_activity = time.monotonic() - 61
    return manager


def state():
    with db.connect() as connection:
        return dict(connection.execute('SELECT * FROM database_maintenance').fetchone())


def test_vacuum_is_deferred_then_durable(local):
    assert maintenance.request_compaction() == 'scheduled'
    manager = ready(local)
    with manager.business_request():
        manager.tick()
    assert state()['succeeded_at'] is None
    manager.last_activity -= 61
    manager.tick()
    saved = state()
    assert saved['succeeded_at'] and not saved['pending']
    assert not saved['checkpoint_pending']
    with db.connect() as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert maintenance.request_compaction() == 'not_needed'
    assert state()['succeeded_at'] == saved['succeeded_at']


def test_queued_work_prevents_compaction(local):
    maintenance.request_compaction()
    db.create_job('asr', 'waiting', {})
    ready(local).tick()
    assert state()['attempted_at'] is None


def test_reader_defers_only_checkpoint(local, monkeypatch):
    maintenance.request_compaction()
    reader = sqlite3.connect(local.database_path)
    reader.execute('BEGIN')
    reader.execute('SELECT * FROM jobs').fetchall()
    manager = ready(local)
    try:
        manager.tick()
        saved = state()
        assert saved['succeeded_at'] and saved['checkpoint_pending']
    finally:
        reader.close()
    monkeypatch.setattr(maintenance, 'RETRY_SECONDS', 0)
    manager.tick()
    assert state()['succeeded_at'] == saved['succeeded_at']
    assert not state()['checkpoint_pending']


def test_keeper_has_no_transaction_and_connections_remain_independent(local):
    with db.keep_database_open():
        with db.connect() as first, db.connect() as second:
            first.execute('BEGIN IMMEDIATE')
            first.execute("INSERT INTO ballast VALUES('uncommitted')")
            assert second.execute('SELECT count(*) FROM ballast').fetchone()[0] == 0
            first.execute('COMMIT')
            assert second.execute('SELECT count(*) FROM ballast').fetchone()[0] == 1
    with db.connect() as connection:
        names = {row[1] for row in connection.execute('PRAGMA index_list(workers)')}
        assert 'idx_workers_latest' in names
        assert connection.execute('SELECT version FROM schema_meta').fetchone()[0] == 23


def test_writer_interrupt_is_scoped_to_database(local):
    manager = ready(local)
    key = maintenance._key(local.database_path)
    maintenance._managers[key] = manager
    try:
        maintenance.notify_write(local.database_path, "UPDATE workers SET state='idle'")
        assert not manager.interrupt.is_set()
        maintenance.notify_write(local.database_path, "UPDATE jobs SET heartbeat_at='now' WHERE id='a'")
        assert not manager.interrupt.is_set()
        maintenance.notify_write(local.database_path, "UPDATE jobs SET cancel_requested=1 WHERE id='a'")
        assert manager.interrupt.is_set()
    finally:
        del maintenance._managers[key]


def test_interruption_survives_restart_and_enforces_retry_delay(local, monkeypatch):
    maintenance.request_compaction()
    manager = ready(local)
    real_connect = sqlite3.connect
    class InterruptingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if sql == 'VACUUM':
                manager.interrupt.set()
            return super().execute(sql, parameters)
    monkeypatch.setattr(maintenance.sqlite3, 'connect', lambda *args, **kwargs: real_connect(*args, factory=InterruptingConnection, **kwargs))
    manager.tick()
    saved = state()
    assert saved['attempted_at'] and saved['error']
    assert saved['pending'] and saved['succeeded_at'] is None
    restarted = ready(local)
    restarted.tick()
    assert state() == saved
    with db.connect() as connection:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
