#!/usr/bin/env python3
"""Measure ASR decode storage without modifying or submitting historical jobs.

Run with the ASR interpreter and AUDIO_INTEL_LOAD_ENV=0. Each output directory
must be new. Input files are opened read-only; reports contain hashes, not text.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil

from audio_intel.asr_resources import Budget, current_budget, tree_rss
from audio_intel.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--cache-max-bytes', type=int, default=4 * 1024**3)
    parser.add_argument('--video', action='store_true')
    args = parser.parse_args()
    if args.cache_max_bytes < 0 or not args.source.is_file():
        parser.error('Require an existing source and nonnegative cache cap')
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    work, output = root/'work', root/'output'
    work.mkdir(); output.mkdir()
    config = replace(settings, temp_dir=work, data_dir=root/'data', cache_dir=root/'cache',
                     log_dir=root/'logs', run_dir=root/'run', asr_audio_cache_max_bytes=args.cache_max_bytes)
    from audio_intel import db
    db.settings = config
    config.ensure_directories()
    db.init_db()
    budget = Budget.start(config, psutil.Process().pid, work, output)
    token = current_budget.set(budget)
    stopped = threading.Event()
    peak = [budget.policy['baseline_rss']]
    def sample() -> None:
        while not stopped.wait(.05):
            peak[0] = max(peak[0], tree_rss(budget.policy['pid'], budget.policy['created']))
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    audio = None
    started = time.monotonic()
    try:
        if args.video:
            from audio_intel.video_frames import decode
            audio, rate, _ = decode(args.source, work/'normalized.wav', work/'frames', None,
                                    SimpleNamespace(progress=lambda *a, **kw: None))
        else:
            from asr.pipeline import decode_audio
            audio, rate = decode_audio(args.source, work/'normalized.wav')
        digest = hashlib.sha256()
        for block in audio.iter_blocks():
            digest.update(block.tobytes())
        report = dict(samples=len(audio), rate=rate, float32_sha256=digest.hexdigest(),
                      duration_seconds=len(audio)/rate, elapsed_seconds=time.monotonic()-started,
                      cache=dict(audio.stats), policy=budget.policy)
        audio.close()
        stopped.set(); sampler.join()
        report['peak_tree_rss_bytes'] = peak[0]
        source_root = Path(__file__).resolve().parents[1]
        report['source_hashes'] = {
            name: hashlib.sha256((source_root/name).read_bytes()).hexdigest()
            for name in ('audio_intel/audio_store.py', 'audio_intel/asr_resources.py', 'asr/pipeline.py')
        }
        try:
            report['source_sha'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source_root, text=True).strip()
            report['source_dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=source_root, text=True).strip())
        except (OSError, subprocess.CalledProcessError):
            report['source_sha'] = None
        report['note'] = 'Cache counters measure logical payload writes, not SSD NAND writes. Model inference is measured separately.'
        (root/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        budget.record(output, 1)
        print(json.dumps({k: report[k] for k in ('samples', 'elapsed_seconds', 'peak_tree_rss_bytes', 'cache')}))
    finally:
        if audio is not None:
            audio.close()
        stopped.set(); sampler.join()
        current_budget.reset(token)


if __name__ == '__main__':
    main()
