"""Measure one complete expression-optimization workflow from an immutable checkout.

Run this script by absolute path with cwd set to the baseline/candidate checkout.
--case is UTF-8 JSON containing request and result from a completed ASR task.
--integrated selects the packet profile (JSON schema, presence penalty zero).
The normal deployment is read only; credentials stay in memory. Each trial owns
an isolated mock-ASR API/database but sends optimization calls to the configured
real model. Linux /proc/self/io includes API shutdown/WAL checkpoint writes;
fixture setup and report writing are excluded. Quality requires separate review.
--resume continues the same paused run and preserves each original receipt;
the aggregate sums phase writes/time and uses the cumulative call ledger.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

root = Path.cwd()
sys.path.insert(0, str(root))
parser = argparse.ArgumentParser()
parser.add_argument('--case', required=True, type=Path, help='UTF-8 JSON with request/result; private data stays in the trial directory')
parser.add_argument('--mode', default='rewrite', choices=('polish', 'rewrite', 'editorial'))
parser.add_argument('--trial', required=True)
parser.add_argument('--integrated', action='store_true', help='Use the packet candidate profile; omit for the preserved baseline')
parser.add_argument('--resume', action='store_true', help='Continue the same paused trial; retain immutable phase receipts and cumulative usage')
parser.add_argument('--configuration-stdin', action='store_true')
parser.add_argument('--configuration-db', type=Path)
parser.add_argument('--output-root', required=True, type=Path)
args = parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', args.trial):
    parser.error('--trial must be a simple ASCII label, at most 128 characters')
if not sys.platform.startswith('linux'):
    parser.error('Physical-write measurement requires Linux /proc/self/io')
if not args.configuration_stdin and not args.configuration_db:
    parser.error('--configuration-db or --configuration-stdin is required')
args.output_root = args.output_root.resolve()
case = json.loads(args.case.read_text(encoding='utf-8'))
if args.configuration_stdin:
    config = json.load(sys.stdin)
else:
    path = args.configuration_db.resolve()
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    config = json.loads(connection.execute('SELECT value_json FROM optimization_settings').fetchone()[0])
    key = connection.execute('SELECT api_key FROM optimization_credentials WHERE id=?', (config.get('credential_ref'),)).fetchone()
    config.pop('credential_ref', None)
    config['_api_key'] = key[0] if key else ''
    connection.close()
config.update(enabled=True, max_llm_calls=10000, max_output_tokens=4096)
if args.integrated:
    config.update(packet_presence_penalty=0, structured_output_mode='json_schema')
else:
    config.pop('packet_presence_penalty', None)
benchmark_key = config.pop('_api_key', '')
benchmark_url = config['base_url']
folder = args.output_root / 'trials' / args.trial
if not folder.resolve().is_relative_to((args.output_root / 'trials').resolve()):
    parser.error('Trial directory must remain inside the output root')
if any(not (folder / name).resolve().is_relative_to(folder.resolve())
       for name in ('data', 'tmp', 'run', 'logs')):
    parser.error('Trial runtime paths must remain inside the trial directory')
prior = None
phase = 0
if args.resume:
    previous = args.output_root / 'measurements' / (args.trial + '.aggregate.json')
    if not previous.exists():
        previous = previous.with_name(args.trial + '.json')
    prior = json.loads(previous.read_text(encoding='utf-8'))
    assert prior['status'] in ('paused', 'measurement_error') and prior['mode'] == args.mode
    if not prior.get('usage', {}).get('run_id') or 'io_delta' not in prior:
        raise RuntimeError('The previous receipt has no measured persisted run to resume')
    phase = len(list(previous.parent.glob(args.trial + '.resume-*.json'))) + 1
    assert (folder / 'data' / 'audio_intel.sqlite3').exists()
elif (folder / 'data' / 'audio_intel.sqlite3').exists():
    raise RuntimeError('Refusing to overwrite a measured trial; resume must retain its original receipt')
os.environ.update(AUDIO_INTEL_LOAD_ENV='0', AUDIO_INTEL_MOCK_MODE='1', AUDIO_INTEL_API_KEY='',
                  AUDIO_INTEL_DATA_DIR=str(folder / 'data'), AUDIO_INTEL_TEMP_DIR=str(folder / 'tmp'),
                  AUDIO_INTEL_RUN_DIR=str(folder / 'run'), AUDIO_INTEL_LOG_DIR=str(folder / 'logs'),
                  AUDIO_INTEL_MIN_FREE_DISK_BYTES='0')

def git(*arguments):
    return subprocess.check_output(['git', *arguments], cwd=root, text=True).strip()

def source_hashes():
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in git('ls-files', 'audio_intel/*.py').splitlines()}

software_sha, source_before = git('rev-parse', 'HEAD'), source_hashes()
benchmark_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
if git('status', '--porcelain', '--untracked-files=no'):
    raise RuntimeError('Benchmark source must be committed and immutable')
if args.resume and prior['software_sha'] != software_sha:
    raise RuntimeError('Resume requires the same immutable source checkout')
from fastapi.testclient import TestClient
from audio_intel import api, db, optimization_store as store, optimization_budget as budget
if args.integrated:
    from audio_intel.optimization_packet_policy import ELIGIBLE_MODELS
    if config['model'] not in ELIGIBLE_MODELS:
        parser.error('This model has not passed the packet-policy gates')

def memory_credential(profile):
    if profile['base_url'] != benchmark_url:
        raise ValueError('Benchmark credential endpoint mismatch')
    return benchmark_key
store.credential = memory_credential

def io_counters():
    return {line.split(':')[0]: int(line.split(':')[1]) for line in Path('/proc/self/io').read_text().splitlines()}

result, run_id, before, started = {}, None, None, None
try:
    if args.resume:
        # A recovery is part of the measured task, including startup/WAL work.
        started, before = time.monotonic(), io_counters()
    with TestClient(api.create_app()) as client:
        if args.resume:
            run_id = prior['usage']['run_id']
            run = store.get(run_id)
            for key in ('base_url', 'model', 'use_proxy', 'context_length', 'max_output_tokens',
                        'extra_body', 'structured_output_mode', 'packet_presence_penalty'):
                assert run['config'].get(key) == config.get(key), key
            assert run['config']['rewrite_mode'] == args.mode
            job = db.get_job(run['job_id'])
            assert job['request'] == case['request'] and job['result'] == case['result'], 'Fixture changed'
            endpoint = f"/api/v1/jobs/{run['job_id']}/transcript-optimization"
            resumed = client.post(endpoint + '/resume', json={'run_id': run_id, 'max_llm_calls': 10000})
            resumed.raise_for_status()
        else:
            store.save_settings(config)
            job = db.create_job('asr', 'isolated-optimization-research', case['request'])
            db.update_job(job['id'], state='succeeded', result_json=case['result'])
            endpoint = f"/api/v1/jobs/{job['id']}/transcript-optimization"
            started, before = time.monotonic(), io_counters()
            created = client.post(endpoint, json={'max_llm_calls': 10000, 'rewrite_mode': args.mode},
                                  headers={'Idempotency-Key': 'research-' + args.trial})
            created.raise_for_status()
            run_id = created.json()['candidate']['id']
        previous = -1
        while True:
            response = client.get(endpoint)
            response.raise_for_status()
            status = response.json()
            current = status['candidate'] or status['published']
            if current['calls_used'] // 20 != previous:
                previous = current['calls_used'] // 20
                print(json.dumps({'trial': args.trial, 'calls': current['calls_used'],
                                  'units': current['completed_units'], 'state': current['state']}), flush=True)
            if status['published']:
                draft = client.get(endpoint + '/result', params={'run_id': run_id})
                draft.raise_for_status()
                items = client.get(endpoint + '/summaries', params={'run_id': run_id})
                items.raise_for_status()
                result.update(status='succeeded', result=draft.json(), insights=items.json())
                break
            if current['state'] in ('failed', 'paused'):
                result.update(status=current['state'], error=current.get('error'))
                break
            if time.monotonic() - started > 10800:
                raise TimeoutError('complete-workflow measurement deadline exceeded')
            time.sleep(.15)
        result['usage'] = budget.usage(run_id)
except Exception as exc:
    result.update(status='measurement_error', error=type(exc).__name__ + ': ' + str(exc)[:500])
    if run_id:
        result['usage'] = budget.usage(run_id)
# Include API background work and closing/checkpointing the WAL keeper.
after = io_counters()
result.update(trial=args.trial, mode=args.mode, fixture_digest=case.get('source_digest') or hashlib.sha256(json.dumps(case, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
              benchmark_sha256=benchmark_sha256,
              software_sha=software_sha, source_hashes=source_before, model=config['model'], quality_assessment='unassessed',
              context_length=config.get('context_length'), max_output_tokens=config['max_output_tokens'],
              structured_output_mode=config.get('structured_output_mode'),
              packet_presence_penalty=config.get('packet_presence_penalty'),
              extra_body=config.get('extra_body', {}), seconds=time.monotonic()-started if started else None)
if before:
    result['io_delta'] = {key: after[key] - before[key] for key in after}
    result['storage_write_bytes'] = result['io_delta']['write_bytes'] - result['io_delta']['cancelled_write_bytes']
result['phase'] = phase
previous_calls = prior['usage']['calls_used'] if prior else 0
result['calls_in_phase'] = result.get('usage', {}).get('calls_used', previous_calls) - previous_calls
if source_hashes() != source_before or git('rev-parse', 'HEAD') != software_sha:
    result['status'] = 'invalid_source_mutation'
output = args.output_root / 'measurements' / (args.trial + ('.resume-' + str(phase) if args.resume else '') + '.json')
output.parent.mkdir(parents=True, exist_ok=True)
assert not output.exists()
output.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
if args.resume and 'io_delta' in result and 'usage' in result:
    aggregate = {**result, 'seconds': prior['seconds'] + result['seconds'],
                 'storage_write_bytes': prior['storage_write_bytes'] + result['storage_write_bytes'],
                 'io_delta': {key: prior['io_delta'].get(key, 0) + value for key, value in result['io_delta'].items()},
                 'phases': prior.get('phases', [args.trial + '.json']) + [output.name],
                 'initial_interruption': prior.get('initial_interruption', prior.get('error')),
                 'measurement_note': 'Cumulative ledger calls; summed active phase times and all physical writes; engineering idle between phases excluded.'}
    aggregate['last_phase_calls'] = aggregate.pop('calls_in_phase')
    assert aggregate['usage']['calls_used'] >= prior['usage']['calls_used']
    output.with_name(args.trial + '.aggregate.json').write_text(json.dumps(aggregate, ensure_ascii=False), encoding='utf-8')
print(json.dumps({'trial': args.trial, 'status': result['status'], 'seconds': result.get('seconds'),
                  'calls': result.get('usage', {}).get('calls_used'), 'write_bytes': result.get('storage_write_bytes')}), flush=True)
sys.exit(0 if result['status'] == 'succeeded' else 1)
