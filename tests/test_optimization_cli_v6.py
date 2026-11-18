"""CLI stage selection and content-ready completion contract."""
import io
import json
import runpy
import sys
import urllib.request

import pytest


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())


@pytest.mark.parametrize('limit', [None, 150])
def test_legacy_review_resume_selects_candidate_and_waits_for_publication(monkeypatch, capsys, limit):
    old = {'id': 'old', 'review_state': 'paused'}
    statuses = iter([
        {'published': old, 'candidate': {'id': 'new', 'state': 'running'}},
        {'published': {**old, 'review_state': 'queued'}, 'candidate': {'id': 'new', 'state': 'running'}},
        {'published': {**old, 'review_state': 'completed'}, 'candidate': {'id': 'new', 'state': 'running'}},
        {'published': {'id': 'new', 'review_state': 'completed'}, 'candidate': None},
    ])
    requests = []
    def urlopen(request, **kwargs):
        requests.append(request)
        return Response({'complete': True, 'review_state': 'completed'}) if '/result?' in request.full_url else Response(next(statuses))
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr('time.sleep', lambda _: None)
    options = ['--max-llm-calls', str(limit)] if limit is not None else []
    monkeypatch.setattr(sys, 'argv', ['optimize_transcript.py', '--url', 'http://test', '--job', 'job', '--resume', '--phase', 'review', '--wait-review', *options])
    runpy.run_path('scripts/optimize_transcript.py', run_name='__main__')
    expected = {'run_id': 'new', 'phase': 'review'}
    if limit is not None:
        expected['max_llm_calls'] = limit
    assert json.loads(requests[1].data) == expected
    assert requests[-1].full_url.endswith('/result?run_id=new')
    assert json.loads(capsys.readouterr().out)['complete']


@pytest.mark.parametrize('limit', [None, 25])
def test_create_returns_only_completed_published_workflow(monkeypatch, capsys, limit):
    values = iter([{'candidate': {'id': 'draft'}, 'published': None},
                   {'candidate': None, 'published': {'id': 'draft', 'review_state': 'completed'}},
                   {'complete': True, 'review_state': 'completed'}])
    submissions = []
    def urlopen(request, **kwargs):
        if request.data is not None:
            submissions.append(json.loads(request.data))
        return Response(next(values))
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr('time.sleep', lambda _: None)
    options = ['--max-llm-calls', str(limit)] if limit is not None else []
    monkeypatch.setattr(sys, 'argv', ['optimize_transcript.py', '--url', 'http://test', '--job', 'job', *options])
    runpy.run_path('scripts/optimize_transcript.py', run_name='__main__')
    assert submissions == [{'regenerate': False, **({'max_llm_calls': limit} if limit is not None else {})}]
    assert json.loads(capsys.readouterr().out) == {'complete': True, 'review_state': 'completed'}
