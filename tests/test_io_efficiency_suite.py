"""Exercise the benchmark's safety boundary and isolated worker output."""
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]


def test_full_suite_rejects_supplied_media_before_opening(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/benchmark_io_efficiency.py'),
                             '--suite', 'all', '--source', str(tmp_path / 'forbidden.wav'),
                             '--output', str(tmp_path / 'output.json')], capture_output=True, text=True)
    assert result.returncode == 2
    assert '--source is forbidden' in result.stderr
    assert not (tmp_path / 'output.json').exists()


def test_isolated_chunk_worker_and_legacy_cli(tmp_path):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    source = inputs / 'tone-10.wav'
    sf.write(source, np.zeros(160000, dtype='float32'), 16000, subtype='PCM_16')
    output = tmp_path / 'output.json'
    env = {**os.environ, 'AUDIO_INTEL_LOAD_ENV': '0'}
    for key in ('DATA', 'TEMP', 'RUN', 'CACHE', 'LOG'):
        env[f'AUDIO_INTEL_{key}_DIR'] = str(tmp_path / key.lower())
    subprocess.run([sys.executable, str(ROOT / 'scripts/io_efficiency_suite.py'),
                    '--source-root', str(ROOT), '--inputs', str(inputs), '--work', str(tmp_path / 'worker'),
                    '--case', 'chunks:10', '--result', str(output), '--instrument'], env=env, check=True)
    measured = json.loads(output.read_text(encoding='utf-8'))
    assert measured['counters']['logical_write_bytes'] == 0
    assert measured['peak_tree_rss'] >= measured['initial_rss'] > 0
    subprocess.run([sys.executable, str(ROOT / 'scripts/benchmark_io_efficiency.py'),
                    '--source', str(source), '--output', str(output), '--repeat', '1'], env=env,
                   stdout=subprocess.DEVNULL, check=True)
    run = json.loads(output.read_text(encoding='utf-8'))['runs'][0]
    assert run['samples_equal'] and run['wav_bytes'] > 0 and run['range_bytes'] == 0
