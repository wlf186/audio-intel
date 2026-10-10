from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from scripts import submit_video


@pytest.fixture
def args(tmp_path):
    source = tmp_path / '中文 讲座.mp4'
    source.write_bytes(b'lecture')
    return argparse.Namespace(video=source, state=tmp_path / '续传.json', device='cpu', language='Auto', pdf=tmp_path / '讲座.pdf')


def install_client(monkeypatch, handler):
    factory = httpx.Client
    monkeypatch.setattr(submit_video.httpx, 'Client', lambda **kwargs: factory(**{**kwargs, 'base_url': 'http://test', 'transport': httpx.MockTransport(handler)}))
    monkeypatch.setattr(submit_video.time, 'sleep', lambda _: None)


@pytest.mark.parametrize('failure', ['lost_accept', 'poll', 'busy', 'stream', 'replace', 'failed', 'cancelled'])
def test_resume_never_resubmits_accepted_job(args, monkeypatch, failure):
    calls = []; accepted = False; broken = True
    original_replace = submit_video.os.replace
    args.pdf.write_bytes(b'old complete PDF')

    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'%PDF-partial'
            raise httpx.ReadError('disconnected')

    def handler(request):
        nonlocal accepted
        calls.append((request.method, request.url.path))
        manifest = {'id': 'upload', 'offset': 7 if accepted else 0, 'chunk_bytes': 7, 'chunks': [], 'job_id': 'existing-job' if accepted else None}
        if request.url.path == '/api/v1/asr/video-uploads':
            return httpx.Response(201, json=manifest)
        if request.method == 'PATCH':
            assert request.content == b'lecture'
            assert request.headers['Upload-Checksum'] == hashlib.sha256(b'lecture').hexdigest()
            return httpx.Response(200, json={**manifest, 'offset': 7})
        if '/video-uploads/' in request.url.path:
            return httpx.Response(200, json=manifest)
        if request.url.path == '/api/v1/asr/video-jobs':
            accepted = True
            if broken and failure == 'lost_accept':
                raise httpx.ReadError('accept response lost')
            return httpx.Response(202, json={'id': 'existing-job', 'state': 'queued'})
        if request.url.path.endswith('/transcript.pdf'):
            if broken and failure == 'busy':
                return httpx.Response(429, headers={'Retry-After': '2'})
            if broken and failure == 'stream':
                return httpx.Response(200, stream=BrokenStream())
            return httpx.Response(200, content=b'%PDF-complete')
        if broken and failure == 'poll':
            raise KeyboardInterrupt
        return httpx.Response(200, json={'id': 'existing-job', 'state': failure if broken and failure in {'failed', 'cancelled'} else 'succeeded'})

    def replace(source, target):
        # The download handle must already be closed, including on native Windows.
        if Path(target) == args.pdf and broken and failure == 'replace':
            raise PermissionError('destination in use')
        original_replace(source, target)

    install_client(monkeypatch, handler)
    monkeypatch.setattr(submit_video.os, 'replace', replace)
    with pytest.raises((httpx.HTTPError, KeyboardInterrupt, OSError, RuntimeError)):
        submit_video.submit(args)
    assert args.state.exists()
    assert args.pdf.read_bytes() == b'old complete PDF'
    assert not list(args.pdf.parent.glob('*.partial'))
    broken = False
    submit_video.submit(args)
    assert not args.state.exists()
    assert args.pdf.read_bytes() == b'%PDF-complete'
    assert calls.count(('POST', '/api/v1/asr/video-jobs')) == 1
    assert calls.count(('POST', '/api/v1/asr/video-uploads')) == 1
    assert calls.count(('PATCH', '/api/v1/asr/video-uploads/upload')) == 1


def test_legacy_upload_journal_and_submit_only_completion(args, monkeypatch):
    args.state.write_text(json.dumps({'file': {'path': str(args.video.resolve()), 'size': 7}, 'upload_id': 'legacy', 'create_key': 'old-create-key', 'job_key': 'old-job-key', 'parameters': {'language': 'Auto', 'compute_device': 'cpu'}}), encoding='utf-8')
    args.pdf = None
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={'id': 'legacy', 'job_id': 'old-job'} if 'video-uploads' in request.url.path else {'id': 'old-job', 'state': 'queued'})
    install_client(monkeypatch, handler)
    submit_video.submit(args)
    assert calls == ['GET', 'GET']
    assert not args.state.exists()


def test_cli_failure_reports_job_and_retry_command(args, monkeypatch, capsys):
    args.state.write_text(json.dumps({'file': {'path': str(args.video.resolve()), 'size': 7}, 'job_id': 'same-job'}), encoding='utf-8')
    def handler(request):
        return httpx.Response(429, headers={'Retry-After': '9'}) if request.url.path.endswith('.pdf') else httpx.Response(200, json={'id': 'same-job', 'state': 'succeeded'})
    install_client(monkeypatch, handler)
    monkeypatch.setattr('sys.argv', ['scripts/submit_video.py', str(args.video), '--state', str(args.state), '--pdf', str(args.pdf)])
    with pytest.raises(SystemExit) as exc:
        submit_video.main()
    assert exc.value.code == 1
    output = capsys.readouterr().err
    assert 'same-job' in output and 'Retry-After: 9' in output and '--state' in output
    assert args.state.exists()
