"""Durable, interruptible idle-time SQLite compaction for the single API process."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import logging
from pathlib import Path
import shutil
import sqlite3
import threading
import time

log = logging.getLogger(__name__)
MIN_FREE = 64 * 1024**2
MIN_RATIO = .25
IDLE_SECONDS = 60
SUCCESS_COOLDOWN = 86400
RETRY_SECONDS = 3600
POLL_SECONDS = 60
EXECUTION_SECONDS = 30
_managers: dict[str, 'Maintenance'] = {}
_registry_lock = threading.Lock()


def _key(path) -> str:
    return str(Path(path).resolve())


def notify_write(path, sql: str) -> None:
    statement = sql.lstrip().upper()
    if not statement.startswith(('INSERT ', 'UPDATE ', 'DELETE ', 'REPLACE ', 'ALTER ', 'DROP ', 'CREATE ')):
        return
    # Liveness does not make an otherwise idle service busy. The maintenance
    # connection is separate and never installs this observer.
    if 'WORKERS' in statement or statement.startswith('UPDATE JOBS SET HEARTBEAT_AT='):
        return
    with _registry_lock:
        manager = _managers.get(_key(path))
    if manager:
        manager.activity()


def request_compaction() -> str:
    from .db import connect
    with connect() as db:
        pages = db.execute('PRAGMA page_count').fetchone()[0]
        free = db.execute('PRAGMA freelist_count').fetchone()[0]
        size = db.execute('PRAGMA page_size').fetchone()[0]
        if free * size < MIN_FREE or not pages or free / pages < MIN_RATIO:
            return 'not_needed'
        db.execute('UPDATE database_maintenance SET pending=1 WHERE singleton=1')
    return 'scheduled'


class Maintenance:
    def __init__(self, config):
        self.config = config
        self.path = config.database_path
        self.stop = threading.Event()
        self.interrupt = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.last_activity = time.monotonic()

    def activity(self):
        with self.lock:
            self.last_activity = time.monotonic()
            self.interrupt.set()

    @contextmanager
    def execution(self, db, deadline):
        finished = threading.Event()
        def interrupted():
            return self.stop.is_set() or self.interrupt.is_set() or time.monotonic() >= deadline
        def watch():
            while not finished.wait(.1):
                if interrupted():
                    db.interrupt()
        # The watchdog also covers long SQLite operations between VM callbacks.
        watcher = threading.Thread(target=watch, name='sqlite-maintenance-budget', daemon=True)
        db.set_progress_handler(lambda: int(interrupted()), 100)
        watcher.start()
        try:
            if interrupted():
                raise sqlite3.OperationalError('interrupted')
            yield
        finally:
            finished.set()
            watcher.join()
            db.set_progress_handler(None, 0)

    @contextmanager
    def business_request(self):
        with self.lock:
            self.active += 1
            self.last_activity = time.monotonic()
            self.interrupt.set()
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1
                self.last_activity = time.monotonic()

    def idle(self, db) -> bool:
        with self.lock:
            if self.active or time.monotonic() - self.last_activity < IDLE_SECONDS:
                return False
        queries = (
            "SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1",
            "SELECT 1 FROM document_imports WHERE state IN ('queued','running') LIMIT 1",
            "SELECT 1 FROM library_imports WHERE state IN ('queued','validating','applying') LIMIT 1",
            "SELECT 1 FROM transcript_optimizations WHERE state IN ('queued','running','pausing','deleting') OR summary_state IN ('queued','running') LIMIT 1",
        )
        if any(db.execute(query).fetchone() for query in queries):
            self.activity()
            return False
        return True

    def tick(self):
        db = sqlite3.connect(self.path, timeout=.1, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            state = db.execute('SELECT * FROM database_maintenance WHERE singleton=1').fetchone()
            if not state or not (state['pending'] or state['checkpoint_pending']):
                return
            now = datetime.now(timezone.utc)
            def elapsed(value):
                return (now - datetime.fromisoformat(value)).total_seconds() if value else float('inf')
            if elapsed(state['attempted_at']) < RETRY_SECONDS or not self.idle(db):
                return
            if not state['checkpoint_pending'] and elapsed(state['succeeded_at']) < SUCCESS_COOLDOWN:
                return
            with self.lock:
                if self.active or time.monotonic() - self.last_activity < IDLE_SECONDS:
                    return
                self.interrupt.clear()
            if self.stop.is_set():
                return
            pages = db.execute('PRAGMA page_count').fetchone()[0]
            size = db.execute('PRAGMA page_size').fetchone()[0]
            free = db.execute('PRAGMA freelist_count').fetchone()[0]
            if not state['checkpoint_pending']:
                if free * size < MIN_FREE or not pages or free / pages < MIN_RATIO:
                    db.execute('UPDATE database_maintenance SET pending=0,error=NULL WHERE singleton=1')
                    return
                if shutil.disk_usage(self.path.parent).free < 2 * pages * size + self.config.min_free_disk_bytes:
                    return
            db.execute('UPDATE database_maintenance SET attempted_at=?,error=NULL WHERE singleton=1', (now.isoformat(),))
            deadline = time.monotonic() + EXECUTION_SECONDS
            try:
                if not state['checkpoint_pending']:
                    with self.execution(db, deadline):
                        db.execute('VACUUM')
                    # Persist vacuum success before checkpoint. A busy reader must
                    # never cause a second full database rewrite on the next try.
                    db.execute('UPDATE database_maintenance SET pending=0,checkpoint_pending=1,succeeded_at=? WHERE singleton=1', (datetime.now(timezone.utc).isoformat(),))
                if self.stop.is_set() or self.interrupt.is_set():
                    return
                with self.execution(db, deadline):
                    busy, _, _ = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                if not busy:
                    db.execute('UPDATE database_maintenance SET checkpoint_pending=0,error=NULL WHERE singleton=1')
                    log.info('Idle database compaction/checkpoint completed')
            except sqlite3.Error as exc:
                db.execute('UPDATE database_maintenance SET error=? WHERE singleton=1', (str(exc)[:500],))
                log.info('Idle database maintenance deferred: %s', exc)
        except sqlite3.Error as exc:
            log.info('Idle database maintenance unavailable: %s', exc)
        finally:
            db.close()

    async def run(self):
        key = _key(self.path)
        with _registry_lock:
            _managers[key] = self
        try:
            while not self.stop.is_set():
                task = asyncio.create_task(asyncio.to_thread(self.tick))
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    self.stop.set()
                    self.interrupt.set()
                    await task
                    raise
                await asyncio.sleep(POLL_SECONDS)
        finally:
            self.stop.set()
            self.interrupt.set()
            with _registry_lock:
                if _managers.get(key) is self:
                    del _managers[key]
