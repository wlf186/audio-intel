#!/usr/bin/env python3
"""Compare legacy chunk writes with exact PCM16 range reads (no inference).

Use the ASR runtime and a saved 16 kHz mono PCM16 WAV. Outputs only checksums,
logical write bytes and timings; temporary chunks are removed after each run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import soundfile as sf
from asr.chunk_input import chunk_reader


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--suite', choices=['chunks', 'all'], default='chunks')
    parser.add_argument('--baseline', default='1c4a537')
    parser.add_argument('--candidate', default='HEAD')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    if args.suite == 'all':
        if args.source is not None or args.repeat < 3:
            parser.error('Full suite generates all inputs; --source is forbidden and --repeat must be >= 3')
        from io_efficiency_suite import run_suite
        run_suite(args)
        return
    if args.source is None:
        parser.error('--source is required for the legacy chunk comparison')
    info = sf.info(args.source)
    if info.samplerate != 16000 or info.channels != 1 or info.subtype != 'PCM_16' or args.repeat < 1:
        parser.error('Require mono 16 kHz PCM16 and positive repeat')
    chunks = [{'start_sample': i, 'end_sample': min(info.frames, i + 45 * 16000)}
              for i in range(0, info.frames, 45 * 16000)]
    report = {'source_sha256': hashlib.file_digest(args.source.open('rb'), 'sha256').hexdigest(),
              'samples': info.frames, 'runs': [], 'note': 'Logical file writes, not SSD NAND writes; no inference.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(args.repeat):
        with tempfile.TemporaryDirectory(dir=args.output.parent) as directory:
            started = time.perf_counter()
            paths = []
            with sf.SoundFile(args.source) as source:
                for i, chunk in enumerate(chunks):
                    source.seek(chunk['start_sample'])
                    values = source.read(chunk['end_sample'] - chunk['start_sample'], dtype='float32')
                    path = Path(directory) / f'{i}.wav'
                    sf.write(path, values, 16000, subtype='PCM_16')
                    paths.append(path)
            expected = hashlib.sha256()
            for path in paths:
                values, _ = sf.read(path, dtype='float32')
                expected.update(values.tobytes())
            elapsed = time.perf_counter() - started
            started = time.perf_counter()
            actual = hashlib.sha256()
            with chunk_reader({'audio_source': {'path': str(args.source), 'sample_rate': 16000}}) as read:
                for chunk in chunks:
                    actual.update(read([chunk])[0][0].tobytes())
            assert actual.digest() == expected.digest()
            report['runs'].append({'wav_seconds': elapsed, 'range_seconds': time.perf_counter() - started,
                                   'wav_bytes': sum(p.stat().st_size for p in paths), 'range_bytes': 0,
                                   'samples_equal': True})
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
