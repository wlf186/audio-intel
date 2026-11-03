from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
import soundfile as sf

from audio_intel.audio_store import AudioStore, BLOCK
from audio_intel import asr_resources as resources
from test_asr_resources import policy


@pytest.mark.parametrize('pages', [0, 1, 8])
@pytest.mark.parametrize('amplitude', [.4, 1., 2.3])
def test_cache_modes_preserve_samples_and_write_once(tmp_path, pages, amplitude):
    values = (np.sin(np.arange(BLOCK * 3 + 137) / 17) * amplitude).astype(np.float32)
    expected = values.copy()
    peak = float(np.max(np.abs(values)))
    if peak > 1:
        expected /= peak
    path = tmp_path / 'cache.f32'
    with AudioStore(path, memory_limit_bytes=pages * BLOCK * 4) as store:
        for start in range(0, len(values), 991):
            store.append(values[start:start + 991])
        assert store.memory_bytes <= pages * BLOCK * 4
        store.finish(tmp_path / 'actual.wav')
        before = store.stats['spilled_bytes']
        assert before <= values.nbytes
        if pages == 8:
            assert before == 0 and not path.exists()
        elif pages == 0:
            assert before == values.nbytes
        for _ in range(3):
            assert np.array_equal(store[:], expected)
        result = store[:100]
        result[:] = 0
        assert np.array_equal(store[:100], expected[:100])
        assert store.stats['spilled_bytes'] == before
        assert store.stats['normalization_rewrite_bytes'] == 0
    sf.write(tmp_path / 'expected.wav', expected, 16000, subtype='PCM_16')
    assert (tmp_path / 'actual.wav').read_bytes() == (tmp_path / 'expected.wav').read_bytes()
    assert not path.exists()
    store.close()


def test_silence_is_logical_and_reads_cross_all_backends(tmp_path):
    with AudioStore(tmp_path/'cache.f32', memory_limit_bytes=BLOCK*4) as store:
        store.append(np.ones(BLOCK, dtype=np.float32))
        store.silence(BLOCK*20)
        store.append(np.full(BLOCK, -.25, dtype=np.float32))
        assert len(store.extents) == 3
        assert store.stats['spilled_bytes'] == BLOCK*4
        assert np.array_equal(store[BLOCK-2:BLOCK+2], [1, 1, 0, 0])
        assert np.array_equal(store[BLOCK*21-2:BLOCK*21+2], [0, 0, -.25, -.25])


def test_readers_and_reclamation_keep_independent_samples(tmp_path):
    values = np.linspace(-.9, .9, BLOCK*8, dtype=np.float32)
    with AudioStore(tmp_path/'cache.f32', memory_limit_bytes=values.nbytes) as store:
        store.append(values)
        def read():
            for _ in range(20):
                assert np.array_equal(store[:], values)
        with ThreadPoolExecutor(max_workers=3) as pool:
            readers = [pool.submit(read) for _ in range(2)]
            store.reclaim(values.nbytes)
            for future in readers:
                future.result()
        assert store.memory_bytes == 0
        assert store.stats['spilled_bytes'] == values.nbytes


def test_pressure_reclaims_before_memory_failure(tmp_path, monkeypatch):
    p = policy(tmp_path)
    p['memory_bytes'] = 16*1024**2
    budget = resources.Budget(p)
    monkeypatch.setattr(resources, 'tree_rss', lambda *a: sum(x.memory_bytes for x in budget.audio_stores))
    monkeypatch.setattr(resources, 'memory_capacity', lambda: (100*1024**3, 100*1024**3))
    monkeypatch.setattr('audio_intel.video_uploads.remaining_bytes', lambda: 0)
    token = resources.current_budget.set(budget)
    try:
        with AudioStore(tmp_path/'cache.f32', memory_limit_bytes=4*1024**2) as store:
            store.append(np.ones(1024**2, dtype=np.float32))
            budget.check(memory=13*1024**2, force=True)
            assert store.memory_bytes < 3*1024**2
            assert store.stats['pressure_spills'] > 0
            assert np.all(store[:BLOCK] == 1)
    finally:
        resources.current_budget.reset(token)


def test_partial_write_failure_keeps_resident_samples_until_cleanup(tmp_path, monkeypatch):
    import errno
    path = tmp_path/'cache.f32'
    store = AudioStore(path, memory_limit_bytes=BLOCK*4)
    values = np.ones(BLOCK, dtype=np.float32)
    store.append(values)
    original = store._write
    def fail(block):
        original(block[:7])
        raise OSError(errno.ENOSPC, 'fixture disk full')
    monkeypatch.setattr(store, '_write', fail)
    with pytest.raises(OSError):
        store.reclaim(BLOCK*4)
    assert store.memory_bytes == BLOCK*4
    assert np.array_equal(store[:], values)
    assert store.stats['spilled_bytes'] == 28
    store.close()
    assert not path.exists()


def test_short_writes_are_completed_without_duplicate_payload(tmp_path):
    path = tmp_path/'cache.f32'
    with AudioStore(path) as store:
        actual = path.open('w+b', buffering=0)
        class ShortWriter:
            def __getattr__(self, name):
                return getattr(actual, name)
            def write(self, data):
                return actual.write(data[:17])
        store.file = ShortWriter()
        values = np.linspace(-.5, .5, 123, dtype=np.float32)
        store.append(values)
        assert store.stats['spilled_bytes'] == values.nbytes
        assert np.array_equal(store[:], values)


def test_io_exception_traceback_does_not_pin_memory_mapping(tmp_path):
    import errno
    path = tmp_path/'cache.f32'
    store = AudioStore(path, memory_limit_bytes=BLOCK*4)
    store.append(np.ones(BLOCK, dtype=np.float32))
    actual = path.open('w+b', buffering=0)
    class BrokenWriter:
        def __getattr__(self, name):
            return getattr(actual, name)
        def write(self, data):
            raise OSError(errno.ENOSPC, 'fixture disk full')
    store.file = BrokenWriter()
    with pytest.raises(OSError) as failure:
        store.reclaim(BLOCK*4)
    # Keep the complete traceback alive during cleanup, as worker error handling does.
    assert failure.value.__traceback__ is not None
    store.close()
    assert store.memory_bytes == 0 and not path.exists()


def test_memory_cache_does_not_reserve_an_unwritten_float_file(tmp_path, monkeypatch):
    p = policy(tmp_path)
    p.update(work_bytes=BLOCK*3*2+8192, audio_cache_bytes=BLOCK*4*4)
    budget = resources.Budget(p)
    monkeypatch.setattr(resources, 'tree_rss', lambda *a: 100 + sum(x.memory_bytes for x in budget.audio_stores))
    monkeypatch.setattr(resources, 'memory_capacity', lambda: (100*1024**3, 100*1024**3))
    monkeypatch.setattr('audio_intel.video_uploads.remaining_bytes', lambda: 0)
    token = resources.current_budget.set(budget)
    try:
        with AudioStore(tmp_path/'cache.f32') as store:
            store.append(np.ones(BLOCK*3, dtype=np.float32))
            store.finish(tmp_path/'normalized.wav')
            assert store.stats['spilled_bytes'] == 0
            assert not store.path.exists()
            assert np.all(store[:BLOCK] == 1)
    finally:
        resources.current_budget.reset(token)


def test_cache_policy_respects_admin_cap_and_quarter_budget(tmp_path, monkeypatch):
    from dataclasses import replace
    from audio_intel.config import settings
    monkeypatch.setattr(resources, 'memory_capacity', lambda: (100*1024**3, 100*1024**3))
    monkeypatch.setattr('audio_intel.video_uploads.remaining_bytes', lambda: 0)
    for cap, expected in [(4*1024**3, 10*1024**2//4), (BLOCK*4+17, BLOCK*4), (0, 0)]:
        config = replace(settings, temp_dir=tmp_path, asr_memory_budget_bytes=10*1024**2,
                         asr_work_budget_bytes=1024**3, asr_audio_cache_max_bytes=cap)
        budget = resources.Budget.start(config, resources.psutil.Process().pid, tmp_path, tmp_path)
        assert budget.policy['audio_cache_bytes'] == expected
