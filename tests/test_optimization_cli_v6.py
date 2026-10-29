"""CLI stage selection and content-ready completion contract."""
import io
import json
import runpy
import sys
import urllib.request


class Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())


def test_legacy_review_resume_selects_candidate_and_waits_for_publication(monkeypatch, capsys):
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
    monkeypatch.setattr(sys, 'argv', ['optimize_transcript.py', '--url', 'http://test', '--job', 'job', '--resume', '--phase', 'review', '--wait-review'])
    runpy.run_path('scripts/optimize_transcript.py', run_name='__main__')
    assert json.loads(requests[1].data) == {'run_id': 'new', 'phase': 'review'}
    assert requests[-1].full_url.endswith('/result?run_id=new')
    assert json.loads(capsys.readouterr().out)['complete']


def test_default_returns_only_completed_published_workflow(monkeypatch, capsys):
    values = iter([{'candidate': {'id': 'draft'}, 'published': None},
                   {'candidate': None, 'published': {'id': 'draft', 'review_state': 'completed'}},
                   {'complete': True, 'review_state': 'completed'}])
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *args, **kwargs: Response(next(values)))
    monkeypatch.setattr(sys, 'argv', ['optimize_transcript.py', '--url', 'http://test', '--job', 'job'])
    runpy.run_path('scripts/optimize_transcript.py', run_name='__main__')
    assert json.loads(capsys.readouterr().out) == {'complete': True, 'review_state': 'completed'}
