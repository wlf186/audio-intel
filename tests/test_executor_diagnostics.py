import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace

import pytest

from audio_intel import db, executor_diagnostics as diagnostics, worker
from audio_intel.config import settings
from test_worker import _wait_for_job, _stop_test_supervisor


@pytest.mark.parametrize('platform,code,delta,signal,possible', [
    ('posix', -9, 1, 'SIGKILL', True), ('posix', -9, 0, 'SIGKILL', False),
    ('posix', -11, 1, 'SIGSEGV', False), ('posix', 17, None, None, False),
    ('nt', -1073741819, None, None, False), ('nt', 17, None, None, False),
])
def test_exit_evidence_and_conservative_oom(tmp_path, monkeypatch, platform, code, delta, signal, possible):
    counts = iter([None, None] if delta is None else [5, 5 + delta])
    monkeypatch.setattr(diagnostics, 'oom_kills', lambda: next(counts))
    probe = diagnostics.ExecutorDiagnostics(os.getpid())
    probe.sample()
    job = {'id': 'fixture', 'kind': 'asr', 'attempts': 2, 'stage': '合并结果', 'progress': .88}
    message = probe.record(job, code, tmp_path, 'fixture-time', platform=platform)
    path = tmp_path / 'diagnostics/attempt-2-executor-exit.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    assert value['exit_code'] == code and value['signal'] == signal
    assert value['possible_oom'] == possible and value['oom_kills_delta'] == delta
    assert ('疑似' in message) == possible
    assert value['observed_peak_rss_bytes'] >= value['last_rss_bytes'] > 0
    assert len(path.read_bytes()) < 16_384
    assert '合并结果' in (tmp_path / 'error.log').read_text(encoding='utf-8')
    if platform == 'nt':
        assert value['windows_status'] == f'0x{code & 0xffffffff:08X}'


def test_unavailable_sampling_and_failed_write_do_not_break_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, 'oom_kills', lambda: None)
    def denied(*args):
        raise diagnostics.psutil.AccessDenied()
    monkeypatch.setattr(diagnostics.psutil, 'Process', denied)
    probe = diagnostics.ExecutorDiagnostics(123)
    def full(*args):
        raise OSError('disk full')
    monkeypatch.setattr(diagnostics, 'atomic_json', full)
    message = probe.record({'id': 'x', 'kind': 'tts', 'attempts': 1}, None, tmp_path, 'now')
    assert 'exit_code=None' in message
    assert probe.last_rss is None


def test_native_executor_crash_records_evidence_and_next_job_proceeds(tmp_path, monkeypatch):
    local = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp',
                    cache_dir=tmp_path/'cache', log_dir=tmp_path/'logs', run_dir=tmp_path/'run', mock_mode=True)
    monkeypatch.setattr(db, 'settings', local)
    local.ensure_directories(); db.init_db()
    first = db.create_job('asr', 'crash fixture', {'crash': True})
    second = db.create_job('asr', 'next fixture', {})
    script = tmp_path/'supervisor.py'
    script.write_text('''import os,time
from audio_intel import worker
def processor(context):
    if context.job['request'].get('crash'):
        os._exit(17)
    time.sleep(1.2)
    return {'text':'next fixture','duration':1}
worker._processor=lambda kind:processor
if __name__=='__main__':
    worker.run('asr')
''', encoding='utf-8')
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
           'AUDIO_INTEL_LOAD_ENV': '0', 'AUDIO_INTEL_MOCK_MODE': '1', 'AUDIO_INTEL_WORKER_POLL_SECONDS': '.03'}
    for key, folder in [('DATA','data'),('TEMP','tmp'),('CACHE','cache'),('LOG','logs'),('RUN','run')]:
        env['AUDIO_INTEL_'+key+'_DIR'] = str(tmp_path/folder)
    supervisor = subprocess.Popen([sys.executable, str(script)], env=env)
    try:
        failed = _wait_for_job(first['id'], lambda job: job['state']=='failed', timeout=20)
        assert failed['error_code']=='WorkerProcessExit' and 'exit_code=17' in failed['error_message']
        assert failed['attempts']==1
        value = json.loads((local.jobs_dir/first['id']/'diagnostics/attempt-1-executor-exit.json').read_text(encoding='utf-8'))
        assert value['exit_code']==17
        completed = _wait_for_job(second['id'], lambda job: job['state'] in ('succeeded','failed'), timeout=20)
        assert completed['state']=='succeeded' and completed['worker_id']==failed['worker_id']
        assert supervisor.poll() is None
        details = db.list_workers()[0]['details']
        assert not any('rss' in key for key in details)
        assert not (local.temp_dir/first['id']).exists()
    finally:
        _stop_test_supervisor(supervisor)
