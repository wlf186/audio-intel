from dataclasses import replace
import asyncio
import hashlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from audio_intel.audio_upload import AudioParser, BLOCK, clean_staging, staging_directory
from audio_intel.config import settings


def parser_for(config, parts, *, closed=True):
    body = b''
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += b'--test\r\nContent-Disposition: ' + disposition.encode() + b'\r\n\r\n' + content + b'\r\n'
    if closed:
        body += b'--test--\r\n'
    chunks = iter([body[i:i+731] for i in range(0, len(body), 731)])
    async def receive():
        chunk = next(chunks, b'')
        return {'type': 'http.request', 'body': chunk, 'more_body': bool(chunk)}
    request = Request({'type': 'http', 'headers': [(b'content-type', b'multipart/form-data; boundary=test')]}, receive)
    return AudioParser(request, config)


def test_upload_writes_once_and_preserves_form_order(tmp_path, monkeypatch):
    config = replace(settings, data_dir=tmp_path, max_upload_bytes=4*1024**2)
    content = b'audio' * 300000
    parser = parser_for(config, [('language', None, b'zh'), ('file', 'audio.wav', content), ('language', None, b'en')])
    written = []
    from audio_intel.audio_upload import StagedAudio
    original = StagedAudio.write
    async def write(self, data):
        written.append(len(data))
        await original(self, data)
    monkeypatch.setattr(StagedAudio, 'write', write)
    async def check():
        form = await parser.parse()
        assert form.getlist('language') == ['zh', 'en']
        upload = form['file']
        assert upload.size == len(content)
        assert upload.digest.hexdigest() == hashlib.sha256(content).hexdigest()
        assert upload.path.read_bytes() == content
        assert sum(written) == len(content)
        assert max(written) <= BLOCK
    try:
        asyncio.run(check())
    finally:
        parser.cleanup()
    assert list(staging_directory(config).iterdir()) == []


@pytest.mark.parametrize('parts,closed,code', [
    ([('file', 'a.wav', b'123456')], True, 413),
    ([('file', 'a.wav', b'1'), ('file', 'b.wav', b'2')], True, 422),
    ([('other', 'a.wav', b'1')], True, 422),
    ([('file', 'a.wav', b'1')], False, 422),
])
def test_bad_audio_is_cleaned(tmp_path, parts, closed, code):
    config = replace(settings, data_dir=tmp_path, max_upload_bytes=5)
    parser = parser_for(config, parts, closed=closed)
    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(parser.parse())
        assert error.value.status_code == code
        if code == 413:
            assert error.value.code == 'upload_too_large'
    finally:
        parser.cleanup()
    assert not staging_directory(config).exists() or not list(staging_directory(config).iterdir())


def test_staging_cleanup_only_owned_names(tmp_path):
    config = replace(settings, data_dir=tmp_path)
    folder = staging_directory(config)
    folder.mkdir(parents=True)
    owned = folder / ('a' * 32 + '.partial')
    owned.write_bytes(b'old upload')
    unrelated = folder / 'user.wav'
    unrelated.write_bytes(b'keep')
    clean_staging(config)
    assert not owned.exists()
    assert unrelated.read_bytes() == b'keep'


def test_route_adopts_without_reading_and_cleans_replay(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from audio_intel import api, db
    from audio_intel.audio_upload import StagedAudio
    config = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp',
                     mock_mode=True, api_key='', min_free_disk_bytes=0,
                     max_upload_bytes=4*1024**2, deployment_profile='cpu')
    monkeypatch.setattr(api, 'settings', config)
    monkeypatch.setattr(db, 'settings', config)
    async def no_read(*args, **kwargs):
        raise AssertionError('Adoption must not reread the uploaded payload')
    monkeypatch.setattr(StagedAudio, 'read', no_read)
    payload = b'RIFF' + b'payload' * 200000
    with TestClient(api.create_app()) as client:
        headers = {'Idempotency-Key': 'batch1-upload-once'}
        response = client.post('/api/v1/asr/jobs', files={'file': ('source.wav', payload)}, headers=headers)
        assert response.status_code == 202, response.text
        job = db.get_job(response.json()['id'])
        from pathlib import Path
        assert Path(job['request']['input_path']).read_bytes() == payload
        replay = client.post('/api/v1/asr/jobs', files={'file': ('source.wav', payload)}, headers=headers)
        assert replay.status_code == 200
        assert replay.json()['id'] == job['id']
        assert not list(staging_directory(config).iterdir())
        assert [p.name for p in config.jobs_dir.iterdir() if p.name != '.audio-upload-staging'] == [job['id']]
