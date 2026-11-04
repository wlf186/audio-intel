"""Synthetic-only paired I/O benchmark support; all generated artifacts stay local.

The driver exports immutable Git sources. Workers import only their selected source
archive, never a deployment database. Counters run separately from timing passes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from unittest.mock import patch


def fixtures(folder: Path) -> None:
    import av
    import numpy as np
    import soundfile as sf
    folder.mkdir(parents=True)
    for seconds in (10, 300, 3600):
        with sf.SoundFile(folder / f'tone-{seconds}.wav', 'w', samplerate=16000,
                          channels=1, subtype='PCM_16') as output:
            for start in range(0, seconds * 16000, 65536):
                indices = np.arange(start, min(seconds * 16000, start + 65536))
                output.write((.35 * np.sin(indices * 2 * np.pi * 440 / 16000)).astype('float32'))
    with av.open(str(folder / 'geometry.mp4'), 'w') as output:
        stream = output.add_stream('libx264', rate=10)
        stream.width, stream.height, stream.pix_fmt = 320, 180, 'yuv420p'
        for i in range(60):
            pixels = np.full((180, 320, 3), 240, dtype='uint8')
            pixels[20:80, 20 + (i // 10) * 20:100 + (i // 10) * 20] = (20, 60, 170)
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels, format='rgb24')):
                output.mux(packet)
        for packet in stream.encode():
            output.mux(packet)
    for count in (100, 10000):
        tree = folder / f'tree-{count}'
        tree.mkdir()
        for i in range(count):
            (tree / f'{i:05d}').write_bytes(b'x' * 16)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def prepare_case(case: str, inputs: Path, work: Path, counters: dict, instrument: bool, stack: ExitStack):
    """Prepare imports/cache state outside measured operation, returning a callable."""
    import av
    import numpy as np
    import soundfile as sf
    from audio_intel import asr_resources, video_detection, video_frames, waveforms, waveform_ranges
    from asr import pipeline, stage

    if instrument:
        for obj, name, label in ((av, 'open', 'container_opens'), (sf.SoundFile, 'read', 'block_reads'),
                                 (sf.SoundFile, '__init__', 'pcm_opens'),
                                 (json, 'loads', 'json_parses'), (shutil, 'disk_usage', 'disk_free_calls')):
            original = getattr(obj, name)
            def counted(*args, _original=original, _label=label, **kwargs):
                counters[_label] += 1
                return _original(*args, **kwargs)
            stack.enter_context(patch.object(obj, name, counted))

    kind, variant = case.split(':')
    if kind == 'chunks':
        source = inputs / f'tone-{variant}.wav'
        frames = int(variant) * 16000
        intervals = [{'start': i / 16000, 'end': min(frames, i + 720000) / 16000}
                     for i in range(0, frames, 720000)]
        ranges = [{'start_sample': int(c['start'] * 16000), 'end_sample': int(c['end'] * 16000)} for c in intervals]
        modern = (Path(pipeline.__file__).parent / 'chunk_input.py').exists()
        def run():
            checksum = hashlib.sha256()
            if modern:
                from asr.chunk_input import chunk_reader
                with chunk_reader({'audio_source': {'path': str(source), 'sample_rate': 16000}}) as read:
                    for item in ranges:
                        checksum.update(read([item])[0][0].tobytes())
            else:
                chunks = pipeline.write_chunks(None, 16000, intervals, work / 'chunks', source=source)
                for item in chunks:
                    values, _ = sf.read(item['path'], dtype='float32')
                    checksum.update(values.tobytes())
            counters['logical_write_bytes'] = sum(p.stat().st_size for p in work.glob('chunks/*.wav'))
            return checksum.hexdigest()
        return run
    if kind == 'resources':
        clock = [10.]
        # Deterministic policy inputs isolate filesystem costs and compare decisions.
        stack.enter_context(patch.object(asr_resources.time, 'monotonic', lambda: clock[0]))
        stack.enter_context(patch.object(asr_resources, 'tree_rss', lambda *args: 100))
        stack.enter_context(patch.object(asr_resources, 'memory_capacity', lambda: (16 * 1024**3, 16 * 1024**3)))
        stack.enter_context(patch('audio_intel.video_uploads.remaining_bytes', return_value=0))
        policy = dict(pid=os.getpid(), created=0, baseline_rss=0, memory_bytes=1024**3,
                      work_bytes=1024**3, memory_reserve=0, disk_reserve=0,
                      work=str(inputs / f'tree-{variant}'), output=str(work / 'empty'))
        (work / 'empty').mkdir()
        def run():
            budget = asr_resources.Budget(policy)
            outcomes = []
            for offset, request in [(0, {'memory': 1024}), (.1, {'memory': 1024}),
                                    (.2, {'memory': 128 * 1024**2}), (.3, {'memory': 128 * 1024**2}),
                                    (.6, {'memory': 1024}), (.7, {'force': True}),
                                    (.8, {'memory': 2 * 1024**3}), (.9, {'work': 2 * 1024**3})]:
                clock[0] = 10 + offset
                try:
                    budget.check(**request)
                    outcomes.append('accepted')
                except asr_resources.AsrResourceLimitError:
                    outcomes.append('rejected')
            return outcomes
        return run
    if kind == 'frames':
        times = [.1, 1.2, 1.2, None, 3.7] if variant == 'sparse' else [i / 10 for i in range(50)] + [1.2, None]
        source = inputs / 'geometry.mp4'
        existing = [{'frame_time': .1, 'image': video_frames.save_picture(video_frames.picture_at(source, .1, None), work / 'existing')}]
        def run():
            scenes = [{'frame_time': value} for value in times]
            video_detection.materialize(source, scenes, work / 'frames', None, 0, lambda *args: None,
                                        existing, work / 'existing')
            return [None if row['image'] is None else hashlib.sha256((work / 'frames' / row['image']).read_bytes()).hexdigest()
                    for row in scenes]
        return run
    if kind.startswith('wave'):
        source = inputs / f'tone-{variant}.wav'
        cache = work / 'waveforms'
        def calculate():
            if kind.startswith('wave_window'):
                return waveform_ranges.retrieve(source, 'original', .123, min(float(variant), 1.1856875), 1000)
            return waveforms.prepare(source, cache_directory=cache)
        if kind.endswith('_hit'):
            calculate()
        return calculate
    if kind == 'progress':
        if variant == 'asr':
            base = work / 'snapshots' / 'progress.json'
            base.parent.mkdir()
            def run():
                payload = {'progress_path': str(base)}
                for i in range(200):
                    for _ in range(3):
                        stage._progress(payload, 'transcribe', i, 199)
                files = list(base.parent.glob('*.json'))
                counters['snapshot_files'] = len(files)
                counters['logical_write_bytes'] = sum(p.stat().st_size for p in files)
                seen = []
                pipeline._drain_stage_progress(base, seen.append, 0)
                return seen[-1]
            return run
        from audio_intel.video_previews import PreviewManager
        (work / 'request.json').write_text('{}', encoding='utf-8')
        clock = [10.]
        stack.enter_context(patch.object(video_detection.time, 'monotonic', lambda: clock[0]))
        stack.enter_context(patch.object(sys, 'argv', ['preview', str(work)]))
        def preview(request, folder, progress):
            for phase in ('scanning', 'detecting', 'selecting', 'images'):
                for i in range(20):
                    clock[0] += .001 if variant == 'boundaries' else .3
                    progress(phase, i, 19)
                    if i < 19:
                        progress(phase, i, 19)
            return {'scenes': []}
        stack.enter_context(patch.object(video_detection, 'preview', preview))
        def run():
            video_detection.main()
            files = list(work.rglob('progress-*.json'))
            counters['snapshot_files'] = len(files)
            counters['logical_write_bytes'] = sum(p.stat().st_size for p in files)
            manager = object.__new__(PreviewManager)
            manager.lock = threading.RLock()
            item = {'folder': work}
            manager.drain(item)
            return item['progress']
        return run
    raise ValueError('Unknown synthetic case')


def worker(args) -> None:
    sys.path.insert(0, str(args.source_root))
    import psutil
    work = args.work
    work.mkdir(parents=True)
    counters = Counter()
    with ExitStack() as stack:
        run = prepare_case(args.case, args.inputs, work, counters, args.instrument, stack)
        counters.clear()  # Imports/cache priming excluded.
        def profile(frame, event, arg):
            if event == 'c_call':
                name = getattr(arg, '__name__', '')
                if name in ('stat', 'lstat'):
                    counters['metadata_calls'] += 1
                elif name == 'scandir':
                    counters['directory_scans'] += 1
        process = psutil.Process()
        def rss():
            total = 0
            for child in [process, *process.children(recursive=True)]:
                try:
                    total += child.memory_info().rss
                except psutil.NoSuchProcess:
                    pass
            return total
        initial = rss()
        peak = [initial]
        done = threading.Event()
        def sample():
            while not done.wait(.01):
                peak[0] = max(peak[0], rss())
        monitor = threading.Thread(target=sample)
        monitor.start()
        if args.instrument:
            sys.setprofile(profile)
        started = time.perf_counter()
        try:
            result = run()
            sys.setprofile(None)
            elapsed = time.perf_counter() - started
            peak[0] = max(peak[0], rss())
        finally:
            done.set()
            monitor.join()
            sys.setprofile(None)
        report = dict(seconds=elapsed, initial_rss=initial, peak_tree_rss=peak[0],
                      correctness=digest(result), counters=dict(counters) if args.instrument else None)
    args.result.write_text(json.dumps(report), encoding='utf-8')


def archive(root: Path, ref: str, destination: Path) -> str:
    sha = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--verify', ref + '^{commit}'], text=True).strip()
    data = subprocess.check_output(['git', '-C', str(root), 'archive', sha])
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(data)) as source:
        source.extractall(destination, filter='data')
    return sha


def check_counters(case: str, entry: dict) -> None:
    before, after = [entry[label]['instrumented']['counters'] for label in ('before', 'after')]
    if case.startswith('chunks:'):
        assert before['logical_write_bytes'] > 0 and after['logical_write_bytes'] == 0
    elif case.startswith('resources:'):
        assert after['directory_scans'] < before['directory_scans']
        assert after['metadata_calls'] < before['metadata_calls']
    elif case == 'progress:asr':
        assert after['snapshot_files'] == 200 and before['snapshot_files'] == 600
        assert after['json_parses'] == 1 and before['json_parses'] == 600
        assert after['logical_write_bytes'] < before['logical_write_bytes']
    elif case == 'progress:boundaries':
        assert after['snapshot_files'] == 8  # Four starts plus four real completions.
    elif case.startswith('frames:'):
        assert after['container_opens'] == 1 and before['container_opens'] > 1
    elif case.startswith('wave_full:'):
        assert before['container_opens'] == 2 and after.get('container_opens', 0) == 0
        assert after['pcm_opens'] == 1
    elif case.startswith('wave_window:'):
        assert before['block_reads'] == 1000 and after['block_reads'] == 3
    elif '_hit:' in case:
        assert before.get('block_reads', 0) == after.get('block_reads', 0) == 0
        assert before.get('container_opens', 0) == after.get('container_opens', 0) == 0


def run_suite(args) -> None:
    root = Path(__file__).resolve().parents[1]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='synthetic-io-', dir=args.output.parent) as temporary:
        folder = Path(temporary)
        sources = {label: folder / label for label in ('before', 'after')}
        shas = {label: archive(root, ref, sources[label])
                for label, ref in [('before', args.baseline), ('after', args.candidate)]}
        inputs = folder / 'inputs'
        fixtures(inputs)
        cases = ([f'chunks:{n}' for n in (10, 300, 3600)] + ['resources:100', 'resources:10000']
                 + ['progress:asr', 'progress:ordinary', 'progress:boundaries', 'frames:sparse', 'frames:dense']
                 + [f'{kind}:{n}' for kind in ('wave_full', 'wave_full_hit', 'wave_window', 'wave_window_hit')
                    for n in (10, 300, 3600)])
        report = dict(sources=shas, python=platform.python_version(), platform=platform.system(),
                      machine=platform.machine(), cpu_count=os.cpu_count(), rss_interval_seconds=.01,
                      note='Synthetic inputs only. Logical I/O, not NAND writes. OS cache uncontrolled. No inference.', cases={})
        counter = 0
        def execute(case, label, instrument=False):
            nonlocal counter
            counter += 1
            work = folder / f'run-{counter}'
            env = {**os.environ, 'AUDIO_INTEL_LOAD_ENV': '0', 'AUDIO_INTEL_MOCK_MODE': '0',
                   'PYTHONPATH': str(sources[label])}
            for key in ('DATA', 'TEMP', 'RUN', 'CACHE', 'LOG'):
                env[f'AUDIO_INTEL_{key}_DIR'] = str(work / key.lower())
            result = folder / 'result.json'
            command = [sys.executable, str(Path(__file__).resolve()), '--source-root', str(sources[label]),
                       '--inputs', str(inputs), '--work', str(work), '--case', case, '--result', str(result)]
            if instrument:
                command.append('--instrument')
            subprocess.run(command, cwd=sources[label], env=env, check=True, stdout=subprocess.DEVNULL)
            value = json.loads(result.read_text(encoding='utf-8'))
            shutil.rmtree(work)
            return value
        for case in cases:
            entry = {label: {'initial': execute(case, label), 'warmup': execute(case, label), 'runs': []}
                     for label in sources}
            for round_number in range(args.repeat):
                for label in (('before', 'after') if round_number % 2 == 0 else ('after', 'before')):
                    entry[label]['runs'].append(execute(case, label))
            for label in sources:
                entry[label]['instrumented'] = execute(case, label, True)
            expected = entry['before']['initial']['correctness']
            assert all(value['correctness'] == expected for e in entry.values()
                       for value in [e['initial'], e['warmup'], e['instrumented'], *e['runs']]), case
            def regression():
                for metric, threshold in [('seconds', .005), ('peak_tree_rss', 16 * 1024**2)]:
                    before, after = [statistics.median(r[metric] for r in entry[label]['runs']) for label in sources]
                    if after > before * 1.1 and after - before > threshold:
                        return True
                return False
            entry['repeated_for_regression'] = regression()
            if entry['repeated_for_regression']:
                for round_number in range(3):
                    for label in (('after', 'before') if round_number % 2 == 0 else ('before', 'after')):
                        entry[label]['runs'].append(execute(case, label))
            entry['needs_review'] = regression()
            check_counters(case, entry)
            entry['equal'] = True
            report['cases'][case] = entry
            args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(case, 'equal', 'review' if entry['needs_review'] else 'passed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root', 'inputs', 'work', 'result'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--case', required=True)
    parser.add_argument('--instrument', action='store_true')
    worker(parser.parse_args())
