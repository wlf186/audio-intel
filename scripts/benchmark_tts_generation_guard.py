"""Isolated, fixed-model A/B validation of the TTS generation guard.

Run with the project TTS Python. Case JSON contains ordinary request fields and
`texts`, never model repositories/configs. All output goes outside production.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases-json', type=Path, required=True)
    parser.add_argument('--device', choices=['cpu', 'gpu'], default='gpu')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output or Path(tempfile.mkdtemp(prefix='tts-generation-guard-'))
    output.mkdir(parents=True, exist_ok=True)
    print(f"OUTPUT {output.resolve()}", flush=True)
    for key, name in [('DATA','data'),('TEMP','tmp'),('RUN','run'),('LOG','logs'),('CACHE','cache')]:
        directory = output / name
        directory.mkdir(exist_ok=True)
        os.environ[f'AUDIO_INTEL_{key}_DIR'] = str(directory)
    # Only the GPU lease uses the real run directory; this script never starts
    # workers or writes executor metadata. No privileged Windows symlink needed.
    if args.device == 'gpu':
        os.environ['AUDIO_INTEL_RUN_DIR'] = str(root / 'run')
    else:
        os.environ['CUDA_VISIBLE_DEVICES'] = ''
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', AUDIO_INTEL_MOCK_MODE='0')
    sys.path.insert(0, str(root))
    import numpy as np
    import soundfile as sf
    import torch
    from contextlib import contextmanager, nullcontext
    from audio_intel.gpu import gpu_lease
    from audio_intel.model_registry import resolve_tts_model
    from tts import pipeline
    from tts.generation_guard import generation_budget, job_guard, token_counts

    @contextmanager
    def observe_generation(model, records):
        """Read returned sequences without altering sampling or stopping.

        Use the baseline rows for budget headroom. Guarded batches may contain
        synthetic EOS padding; their acceptance comes from the guard journal.
        """
        talker = model.model.talker
        original = talker.generate
        def generate(*args, **kwargs):
            result = original(*args, **kwargs)
            eos = int(model.model.config.talker_config.codec_eos_token_id)
            rows = []
            for sequence in result.sequences.detach().cpu():
                positions = (sequence == eos).nonzero().flatten().tolist()
                rows.append({'steps': positions[0] + 1 if positions else len(sequence),
                             'eos_present': bool(positions)})
            records.append(rows)
            return result
        talker.generate = generate
        try:
            yield
        finally:
            talker.generate = original

    production = root / 'data/audio_intel.sqlite3'
    db = sqlite3.connect(production.as_uri()+'?mode=ro', uri=True) if production.exists() else None
    last_check = 0.0
    def idle(_step: int = 0, *, force: bool = False) -> None:
        nonlocal last_check
        if db is not None and (force or time.monotonic()-last_check > 1):
            last_check = time.monotonic()
            if db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]:
                raise RuntimeError('Production work is active; stop this isolated benchmark')
    cases = json.loads(args.cases_json.read_text(encoding="utf-8"))
    if not isinstance(cases,list) or not cases:
        parser.error('--cases-json must contain a nonempty case list')
    records = []
    model = None
    loaded = None
    torch.set_num_threads(4)
    idle(force=True)
    lease = gpu_lease(lambda: idle(force=True)) if args.device == 'gpu' else nullcontext()
    with lease:
        try:
            for index, source in enumerate(cases):
                idle(force=True)
                request = {key:value for key,value in source.items() if key not in {'texts','name'}}
                request['compute_device'] = args.device
                # Do not write reference alignment caches into the library.
                request.pop('voiceprint_sample_id', None)
                key = (request['model'],request['voice_mode'])
                work = output / f'case-{index}'
                work.mkdir(exist_ok=True)
                context = SimpleNamespace(work_dir=work,output_dir=work/'output',
                    job={'attempts':1,'request':request},progress=lambda *a,**kw:idle())
                if request['voice_mode'] not in {'preset','voice_design'}:
                    pipeline._prepare_clone_reference(context,request,args.device)
                if key != loaded:
                    if model is not None:
                        del model
                        model = None
                    pipeline._release_cpu_models()
                    gc.collect()
                    if args.device == 'gpu':
                        torch.cuda.empty_cache()
                    model = pipeline.load_model(resolve_tts_model(request['model']),request['voice_mode'],args.device)
                    loaded = key
                    warm = True
                else:
                    warm = False
                prompt = None
                if request['voice_mode'] not in {'preset','voice_design'}:
                    prompt = model.create_voice_clone_prompt(ref_audio=request['reference_audio_path'],ref_text=request['reference_text'],x_vector_only_mode=False)
                if warm:
                    pipeline._generate_tts_batch(model,request,[source['texts'][0]],prompt,progress_callback=idle)
                pair = {}
                counts, basis = token_counts(model, source['texts'])
                budgets = [generation_budget(count) for count in counts]
                seed = 42000 + index
                for mode in (['baseline','guard'] if index % 2 == 0 else ['guard','baseline']):
                    idle(force=True)
                    torch.manual_seed(seed)
                    if args.device == 'gpu':
                        torch.cuda.synchronize()
                        torch.cuda.reset_peak_memory_stats()
                    start = time.perf_counter()
                    summary = None
                    generations = []
                    with observe_generation(model, generations):
                        if mode == 'guard':
                            with job_guard(context,model) as controller:
                                audio,rate = pipeline._generate_tts_batch(model,request,source['texts'],prompt,progress_callback=idle)
                                summary = controller.summary()
                        else:
                            audio,rate = pipeline._generate_tts_batch(model,request,source['texts'],prompt,progress_callback=idle)
                    if args.device == 'gpu':
                        torch.cuda.synchronize()
                    elapsed = time.perf_counter()-start
                    peak = torch.cuda.max_memory_allocated()/1048576 if args.device == 'gpu' else None
                    hashes = []
                    for row,waveform in enumerate(audio):
                        hashes.append(hashlib.sha256(np.asarray(waveform).tobytes()).hexdigest())
                        sf.write(work/f'{mode}-{row}.wav',waveform,rate,subtype='PCM_16')
                    pair[mode] = {'seconds':elapsed,'peak_mib':peak,'audio_hashes':hashes,
                                  'audio_seconds':[len(a)/rate for a in audio],'generation_guard':summary,
                                  'generations':generations}
                result = {'name':source.get('name',str(index)),'model':key,'seed':seed,'pair':pair,
                          'text_counts':counts,'count_basis':basis,'budgets':budgets,
                          'identical':pair['baseline']['audio_hashes']==pair['guard']['audio_hashes'],
                          'change_percent':(pair['guard']['seconds']/pair['baseline']['seconds']-1)*100}
                records.append(result)
                (output/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(result,ensure_ascii=False),flush=True)
        finally:
            if model is not None:
                del model
            pipeline._release_cpu_models()
            gc.collect()
            if args.device == 'gpu':
                torch.cuda.empty_cache()
            if db is not None:
                db.close()
    changes = sorted(row['change_percent'] for row in records)
    memory_changes = [(row['pair']['guard']['peak_mib']/row['pair']['baseline']['peak_mib']-1)*100 for row in records if row['pair']['baseline']['peak_mib']]
    summary = {'max_peak_memory_change_percent':max(memory_changes,default=0.0),'pairs':len(records),'identical_pairs':sum(r['identical'] for r in records),
               'median_change_percent':statistics.median(changes),
               'p95_change_percent':changes[max(0,__import__('math').ceil(len(changes)*.95)-1)],
               'retries':sum(r['pair']['guard']['generation_guard']['retry_attempts'] for r in records)}
    (output/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('SUMMARY',json.dumps(summary),flush=True)
    if summary['identical_pairs'] != len(records) or summary['retries']:
        raise SystemExit('Normal-output equivalence gate failed')
    if summary['median_change_percent'] > 5 or summary['p95_change_percent'] > 10 or summary['max_peak_memory_change_percent'] > 5:
        raise SystemExit('Normal-generation performance gate failed')


if __name__ == '__main__':
    main()
