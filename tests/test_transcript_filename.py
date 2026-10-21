from copy import deepcopy
from email.message import Message

from fastapi.testclient import TestClient
import pytest

from audio_intel import api, db
from audio_intel.transcript_filename import transcript_filename
from test_voiceprint_groups_exports import local, transcript


def filename(response):
    message = Message()
    message['Content-Disposition'] = response.headers['content-disposition']
    return message.get_filename()


@pytest.mark.parametrize('name,expected', [
    ('Recording_10.m4a', 'Recording_10'),
    ('WorkBuddy与AI-Coding融合方案-用户旅程版.mp4', 'WorkBuddy与AI-Coding融合方案-用户旅程版'),
    ('会议.v2.final.MP4', '会议.v2.final'),
    ('无后缀', '无后缀'),
    ('会议.v2', '会议.v2'),
    ('../目录/会议 录音?.wav', '会议_录音'),
    ('C:\\目录\\会议.M4A', '会议'),
    ('\r\n...', 'transcript-fixture'),
    ('', 'transcript-fixture'),
    ('.wav', 'wav'),
    ('字' * 200 + '.mp3', '字' * 180),
])
def test_cross_platform_download_name(name, expected):
    assert transcript_filename(name, 'fixture', 'docx') == expected + '.docx'


@pytest.mark.parametrize('source', ['Recording_10.m4a', 'WorkBuddy与AI-Coding融合方案-用户旅程版.mp4'])
@pytest.mark.parametrize('redact', [True, False])
def test_every_export_format_uses_task_name_without_rewriting_history(local, source, redact):
    result = transcript()
    job = db.create_job('asr', source, {'export_formats': ['json', 'txt', 'srt', 'vtt']})
    db.update_job(job['id'], state='succeeded', result_json=result)
    before = deepcopy(db.get_job(job['id']))
    with TestClient(api.create_app()) as client:
        root = f"/api/v1/jobs/{job['id']}"
        for format in ('pdf', 'docx', 'json', 'txt', 'srt', 'vtt'):
            response = client.post(root + '/transcript-export', json={'format': format, 'redact': redact})
            assert response.status_code == 200, response.text[:200]
            assert filename(response) == source.rsplit('.', 1)[0] + '.' + format
            assert len(response.content) == int(response.headers['content-length'])
        get = client.get(root + '/transcript.pdf')
        head = client.head(root + '/transcript.pdf')
        assert get.status_code == head.status_code == 200
        assert filename(get) == filename(head) == source.rsplit('.', 1)[0] + '.pdf'
        assert get.headers['content-length'] == head.headers['content-length']
        assert not head.content
    assert db.get_job(job['id']) == before


@pytest.mark.parametrize('kind', ['asr', 'tts'])
def test_artifact_download_names_keep_paths_bytes_and_other_artifacts(local, kind):
    job = db.create_job(kind, '会议.v2.wav', {})
    output = local.jobs_dir / job['id'] / 'output'
    output.mkdir(parents=True)
    names = ['transcript.' + suffix for suffix in ('json', 'txt', 'srt', 'vtt')] + ['audio.wav', 'extra.json']
    artifacts = []
    for name in names:
        path = output / name
        path.write_bytes(('saved ' + name).encode('utf-8'))
        artifacts.append({'name': name, 'path': str(path), 'mime_type': 'application/octet-stream'})
    db.update_job(job['id'], state='succeeded', result_json={'artifacts': artifacts})
    before = deepcopy(db.get_job(job['id']))
    with TestClient(api.create_app()) as client:
        for name in names:
            response = client.get(f"/api/v1/jobs/{job['id']}/artifacts/{name}")
            assert response.status_code == 200
            expected = '会议.v2.' + name.rsplit('.', 1)[1] if kind == 'asr' and name.startswith('transcript.') else name
            assert filename(response) == expected
            assert response.content == (output / name).read_bytes() == ('saved ' + name).encode('utf-8')
    assert db.get_job(job['id']) == before
    assert sorted(p.name for p in output.iterdir()) == sorted(names)


def test_export_names_are_documented_in_openapi(local):
    schema = api.create_app().openapi()
    for path, method in [('/api/v1/jobs/{job_id}/transcript-export', 'post'), ('/api/v1/jobs/{job_id}/transcript.pdf', 'get'), ('/api/v1/jobs/{job_id}/transcript.pdf', 'head'), ('/api/v1/jobs/{job_id}/artifacts/{name}', 'get')]:
        operation = schema['paths'][path][method]
        assert 'Content-Disposition' in operation['responses']['200']['headers']
        assert 'task basename' in operation['description']
