"""The measurement harness must isolate deployment data and credentials."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import shutil
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/benchmark_optimization_packets.py'


def test_benchmark_help_is_portable_and_does_not_access_configuration():
    result = subprocess.run([sys.executable, str(SCRIPT), '--help'], cwd=ROOT,
                            text=True, encoding='utf-8', capture_output=True, timeout=10)
    assert result.returncode == 0
    assert '--configuration-db' in result.stdout
    assert '--integrated' in result.stdout
    assert '--resume' in result.stdout


@pytest.mark.skipif(not sys.platform.startswith('linux'), reason='Linux physical-write counters')
@pytest.mark.parametrize('recover', [False, True])
def test_complete_benchmark_has_real_call_count_and_no_persisted_credential(tmp_path, recover):
    stage = tmp_path / 'immutable source'
    stage.mkdir()
    shutil.copytree(ROOT / 'audio_intel', stage / 'audio_intel',
                    ignore=shutil.ignore_patterns('__pycache__'))
    subprocess.run(['git', 'init', '-q'], cwd=stage, check=True)
    subprocess.run(['git', 'add', 'audio_intel'], cwd=stage, check=True)
    subprocess.run(['git', '-c', 'user.name=Benchmark Fixture',
                    '-c', 'user.email=fixture@example.test', 'commit', '-qm',
                    'test: stage immutable benchmark source'], cwd=stage, check=True)
    key = 'synthetic-memory-only-benchmark-secret'
    requests = []
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.headers['Authorization'] == 'Bearer ' + key
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            data = json.loads(body['messages'][-1]['content'])
            requests.append(data)
            if recover and len(requests) <= 3:
                self.send_response(503)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":"Temporary outage"}')
                return
            if 'response_format' not in body:
                content = data['target']['text'].replace('嗯，那个', '')
            elif isinstance(data, list):
                values = {item['id']: item['source'].replace('嗯，那个', '') for item in data}
                content = json.dumps(values, ensure_ascii=False)
            else:
                values = {
                    'reviews': {item['id']: {'verdict': 'pass', 'source_quote': '',
                        'candidate_quote': '', 'reason': ''} for item in data['units']},
                    'groups': {item['id']: item['source'] for item in data['groups']},
                    'scenes': {}}
                content = json.dumps(values, ensure_ascii=False)
            payload = json.dumps({'choices': [{'message': {'content': content},
                                                'finish_reason': 'stop'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = '嗯，那个预算四千元，但还没批准。你明天问一下，好吗？'
    case = tmp_path / 'case.json'
    case.write_text(json.dumps({'request': {}, 'result': {'language': 'Chinese',
        'duration': 4, 'speakers': [], 'segments': [{'id': 0, 'speaker': 's1',
        'start': 0, 'end': 4, 'text': source}]}}, ensure_ascii=False), encoding='utf-8')
    config = {'protocol': 'openai_chat_completions', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
              'model': 'qwen3.5:9b', 'use_proxy': False, 'context_length': 32768,
              'prompt_version': 7, 'extra_body': {}, '_api_key': key}
    try:
        command = [sys.executable, str(SCRIPT), '--case', str(case),
            '--trial', 'isolated-smoke', '--integrated', '--configuration-stdin',
            '--output-root', str(tmp_path / 'outputs')]
        result = subprocess.run(command, cwd=stage,
            input=json.dumps(config), text=True, encoding='utf-8', capture_output=True, timeout=30)
        initial = tmp_path / 'outputs/measurements/isolated-smoke.json'
        original = initial.read_bytes()
        if recover:
            assert result.returncode == 1, result.stdout + result.stderr
            interrupted = json.loads(original)
            assert interrupted['status'] == 'paused' and interrupted['usage']['calls_used'] == 3
            result = subprocess.run([*command, '--resume'], cwd=stage,
                input=json.dumps(config), text=True, encoding='utf-8', capture_output=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            assert initial.read_bytes() == original
            phase = json.loads(initial.with_name('isolated-smoke.resume-1.json').read_text(encoding='utf-8'))
            aggregate = json.loads(initial.with_name('isolated-smoke.aggregate.json').read_text(encoding='utf-8'))
            assert phase['calls_in_phase'] == 2 and phase['usage']['calls_used'] == 5
            assert aggregate['storage_write_bytes'] == interrupted['storage_write_bytes'] + phase['storage_write_bytes']
            assert aggregate['seconds'] == interrupted['seconds'] + phase['seconds']
            assert aggregate['phases'] == [initial.name, 'isolated-smoke.resume-1.json']
        else:
            assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
    report = aggregate if recover else json.loads(original)
    assert report['status'] == 'succeeded'
    assert report['usage']['calls_used'] == len(requests) == (5 if recover else 2)
    assert report['storage_write_bytes'] > 0
    assert report['quality_assessment'] == 'unassessed'
    assert len(report['source_hashes']) > 10 and len(report['benchmark_sha256']) == 64
    for file in (tmp_path / 'outputs').rglob('*'):
        if file.is_file():
            assert key.encode() not in file.read_bytes(), file
