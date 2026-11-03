import json
import os
from pathlib import Path
import subprocess
import sys
import tracemalloc
import types
from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf

from audio_intel import asr_resources as resources, db
from audio_intel.audio_store import AudioStore, BLOCK, MAX_READ, speaker_windows, window_audio
from audio_intel.config import settings
from test_worker import _wait_for_job, _stop_test_supervisor


def test_13_hour_decode_buffers_are_bounded(tmp_path, monkeypatch):
    class VirtualAudio:
        def __len__(self):
            return 779_695_104
        def __getitem__(self, item):
            assert item.stop - item.start <= BLOCK
            return np.full(min(item.stop, len(self)) - item.start, -.25, dtype=np.float32)
    with AudioStore(tmp_path / 'virtual.f32') as store:
        monkeypatch.setattr(store, '_write_block', lambda block: None)
        tracemalloc.start()
        try:
            store.append(VirtualAudio())
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert len(store) == 779_695_104 and store.peak == .25
        assert peak < 64 * 1024**2


def test_vad_keeps_60_second_cache_and_final_flush(tmp_path, monkeypatch):
    from asr.pipeline import run_vad
    calls = []
    class Model:
        def generate(self, input, fs, cache, **kwargs):
            calls.append((len(input), id(cache), kwargs))
            count = cache.get('count', 0)
            cache['count'] = count + 1
            return [{'value': [[count * 60000, count * 60000 + 100]]}]
    monkeypatch.setitem(sys.modules, 'funasr', types.SimpleNamespace(AutoModel=lambda **kwargs: Model()))
    with AudioStore(tmp_path / 'vad.f32') as store:
        store.silence(120 * 16000 + 127)
        store.file.flush()
        result = run_vad(store, 16000)
    assert [x[0] for x in calls] == [960000, 960000, 127]
    assert len({x[1] for x in calls}) == 1
    assert [x[2]['is_final'] for x in calls] == [False, False, True]
    assert all(x[2]['chunk_size'] == 60000 and not x[2]['is_streaming_input'] for x in calls)
    assert result[-1] == {'start': 120., 'end': 120.1}


def test_store_preserves_unquantized_samples_normalization_and_wav(tmp_path):
    values = (np.sin(np.arange(200_137) / 13) * 2).astype(np.float32)
    expected = values.copy()
    expected /= float(np.max(np.abs(expected)))
    target = tmp_path / 'normalized.wav'
    with AudioStore(tmp_path / 'samples.f32') as store:
        for offset in range(0, len(values), 1024):
            store.append(values[offset:offset + 1024])
        store.finish(target)
        assert np.array_equal(store[:], expected)
        with pytest.raises(TypeError):
            np.asarray(store)
        with pytest.raises(ValueError):
            store.read(0, MAX_READ + 1)
    sf.write(tmp_path / 'legacy.wav', expected, 16000, subtype='PCM_16')
    assert target.read_bytes() == (tmp_path / 'legacy.wav').read_bytes()
    target.unlink()  # Native Windows handle ownership.
    (tmp_path / 'samples.f32').unlink()


def test_silence_and_window_descriptors_match_legacy(tmp_path):
    from importlib.util import find_spec
    with AudioStore(tmp_path / 'samples.f32') as store:
        store.silence(16000)
        store.append(np.ones(91_321, dtype=np.float32))
        store.file.flush()
        regions = [{'start': 1., 'end': 1.5}, {'start': 2., 'end': 6.7}]
        descriptors = speaker_windows(store, regions)
        assert all(isinstance(x[2], tuple) for x in descriptors)
        assert np.max(np.abs(store[:16000])) == 0
        for item in descriptors:
            samples = window_audio(store, item)
            assert len(samples) == 24000
        if find_spec('funasr'):
            from funasr.models.campplus.utils import sv_chunk
            old = sv_chunk([[x['start'], x['end'], store[int(x['start'] * 16000):int(x['end'] * 16000)]] for x in regions])
            assert [(x[0], x[1]) for x in old] == [(x[0], x[1]) for x in descriptors]
            assert all(np.array_equal(x[2], window_audio(store, y)) for x, y in zip(old, descriptors))


def policy(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    return dict(pid=os.getpid(), created=resources.psutil.Process().create_time(), baseline_rss=0,
                memory_bytes=1024**3, work_bytes=1024**3, memory_reserve=0, disk_reserve=0,
                work=str(tmp_path), output=str(tmp_path / 'output'))


def test_budget_preflights_large_allocations_and_bounds_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, 'tree_rss', lambda *args: 100)
    monkeypatch.setattr(resources, 'memory_capacity', lambda: (10 * 1024**3, 10 * 1024**3))
    monkeypatch.setattr('audio_intel.video_uploads.remaining_bytes', lambda: 0)
    budget = resources.Budget(policy(tmp_path))
    budget.check(memory=1024)
    with pytest.raises(resources.AsrResourceLimitError, match='memory growth'):
        budget.check(memory=2 * 1024**3)
    with pytest.raises(resources.AsrResourceLimitError, match='workspace'):
        budget.check(work=2 * 1024**3)
    budget.record(tmp_path / 'job', 3, 'fixture')
    path = tmp_path / 'job/diagnostics/attempt-3-resource-budget.json'
    assert path.stat().st_size < 16384
    assert json.loads(path.read_text(encoding='utf-8'))['error'] == 'fixture'


def test_small_preflights_reuse_snapshot_without_postponing_refresh(tmp_path, monkeypatch):
    clock = [10.]
    samples = []
    monkeypatch.setattr(resources.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(resources, 'tree_rss', lambda *args: samples.append(1) or 100)
    monkeypatch.setattr(resources, 'memory_capacity', lambda: (10 * 1024**3, 10 * 1024**3))
    monkeypatch.setattr('audio_intel.video_uploads.remaining_bytes', lambda: 0)
    budget = resources.Budget(policy(tmp_path))
    budget.check(memory=1024)
    clock[0] += .2
    budget.check(memory=1024)
    clock[0] += .4
    budget.check(memory=1024)
    assert len(samples) == 2


def test_huge_timestamp_gap_rejected_before_zero_allocation(tmp_path, monkeypatch):
    p = policy(tmp_path)
    p['work_bytes'] = 1024
    budget = resources.Budget(p)
    token = resources.current_budget.set(budget)
    try:
        with AudioStore(tmp_path / 'samples.f32') as store:
            monkeypatch.setattr(np, 'zeros', lambda *a, **k: pytest.fail('Must check before allocating silence'))
            with pytest.raises(resources.AsrResourceLimitError):
                store.silence(16000 * 3600 * 13)
            assert len(store) == 0
    finally:
        resources.current_budget.reset(token)


@pytest.mark.parametrize("cooperative", [True, False])
def test_native_resource_failure_exits_tree_and_next_job_proceeds(tmp_path, monkeypatch, cooperative):
    local = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp',
                    cache_dir=tmp_path/'cache', log_dir=tmp_path/'logs', run_dir=tmp_path/'run', mock_mode=True)
    monkeypatch.setattr(db, 'settings', local)
    local.ensure_directories(); db.init_db()
    first = db.create_job('asr', 'budget fixture', {'budget_failure': True, 'cooperative': cooperative})
    second = db.create_job('asr', 'next fixture', {})
    script = tmp_path/'supervisor.py'
    script.write_text('''import os,subprocess,sys,time
from audio_intel import worker
from audio_intel.asr_resources import AsrResourceLimitError
def processor(context):
    if context.job['request'].get('budget_failure'):
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
        (context.output_dir/'child.pid').write_text(str(child.pid),encoding='utf-8')
        if context.job['request']['cooperative']:
            raise AsrResourceLimitError('fixture allocation exceeds budget')
        memory = bytearray(20 * 1024**2)
        time.sleep(120)
    time.sleep(.2)
    return {'text':'next fixture','duration':1}
worker._processor=lambda kind:processor
if __name__=='__main__':worker.run('asr')
''', encoding='utf-8')
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
           'AUDIO_INTEL_LOAD_ENV':'0','AUDIO_INTEL_WORKER_POLL_SECONDS':'.03',
           'AUDIO_INTEL_ASR_MEMORY_BUDGET_BYTES':'4194304'}
    for key, folder in [('DATA','data'),('TEMP','tmp'),('CACHE','cache'),('LOG','logs'),('RUN','run')]:
        env['AUDIO_INTEL_'+key+'_DIR'] = str(tmp_path/folder)
    supervisor = subprocess.Popen([sys.executable,str(script)],env=env)
    try:
        failed=_wait_for_job(first['id'],lambda j:j['state']=='failed',timeout=20)
        assert failed['error_code']=='AsrResourceLimitError'
        child=int((local.jobs_dir/first['id']/'output/child.pid').read_text(encoding='utf-8'))
        assert not resources.psutil.pid_exists(child) or resources.psutil.Process(child).status()==resources.psutil.STATUS_ZOMBIE
        assert not (local.temp_dir/first['id']).exists()
        done=_wait_for_job(second['id'],lambda j:j['state'] in ('succeeded','failed'),timeout=20)
        assert done['state']=='succeeded' and done['worker_id']==failed['worker_id']
        assert supervisor.poll() is None
    finally:
        _stop_test_supervisor(supervisor)
