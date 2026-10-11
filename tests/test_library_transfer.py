from __future__ import annotations

import io
import json
import shutil
import threading
import time
import uuid
import wave
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import audio_intel.api as api
from audio_intel import db, library_store as store, library_transfer as transfer
from audio_intel.config import settings
from audio_intel.document_download import reader_lease


@pytest.fixture
def local(tmp_path, monkeypatch):
    config = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp',
                     api_key='', mock_mode=True, deployment_profile='cpu',
                     enabled_services=frozenset({'asr', 'tts'}), min_free_disk_bytes=0)
    monkeypatch.setattr(api, 'settings', config)
    monkeypatch.setattr(db, 'settings', config)
    config.ensure_directories()
    db.init_db()
    return config


def sample(config, person_id, name='参考录音', value=0):
    path = config.voiceprints_dir/person_id/(uuid.uuid4().hex+'.wav')
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(value.to_bytes(2, 'little')*16000)
    return db.create_voiceprint_sample(person_id, name=name, state='ready', audio_path=str(path),
                                      transcript='你好。', words=[{'text':'你好。','start':0.0,'end':1.0}], duration=1)


def exported(client):
    result = client.get('/api/v1/library/export')
    assert result.status_code == 200, result.text
    assert result.headers['content-type'] == 'application/zip'
    return result.content


def upload(client, data, key=None):
    return client.post('/api/v1/library/imports', headers={'Idempotency-Key': key or uuid.uuid4().hex},
                       files={'file': ('资料库.zip', data, 'application/zip')})


def wait(client, identifier, expected='ready'):
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        item = client.get('/api/v1/library/imports/'+identifier).json()
        if item['state'] not in {'queued','validating','applying'}:
            assert item['state'] == expected, item
            # The durable completion receipt precedes retired-file cleanup.
            if not client.app.state.library_changing:
                return item
        time.sleep(.03)
    pytest.fail('Library manager did not finish')


def preview(client, identifier, mode='merge'):
    result = client.post(f'/api/v1/library/imports/{identifier}/preview', json={'mode':mode})
    assert result.status_code == 200, result.text
    return result.json()


def apply(client, identifier, plan):
    body = {key:plan[key] for key in ('mode','preview_revision')}
    result = client.post(f'/api/v1/library/imports/{identifier}/apply', json=body)
    assert result.status_code == 202, result.text
    return wait(client, identifier, 'completed')


def unpack(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return json.loads(archive.read('manifest.json')), {name:archive.read(name) for name in archive.namelist() if name != 'manifest.json'}


def repack(manifest, assets, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        for name, data in assets.items():
            archive.writestr(name, data)
        if extra:
            archive.writestr(*extra)
    return output.getvalue()


def test_roundtrip_blank_target_preserves_audio_identity_and_snapshots(local):
    person = db.create_voiceprint_person('曾华', note='一组', include_in_hotword_library=False)
    first = sample(local, person['id'])
    second = sample(local, person['id'], name='同一音频的第二段录入')
    failed = db.create_voiceprint_sample(person['id'], name='失败录音', state='failed')
    hotwords = db.create_hotword_list('项目术语', ['声纹','本地识别'])
    job = db.create_job('asr', '历史任务', {'hotword_list_ids':[hotwords['id']]})
    db.update_job(job['id'], state='succeeded', result_json={'text':'提交时的快照'})
    historical = db.get_job(job['id'])
    with TestClient(api.create_app()) as client:
        data = exported(client)
        manifest, assets = unpack(data)
        assert len(assets) == 2  # Source duplicates remain distinct on an empty target.
        assert len(manifest['hotword_lists']) == 1  # System lists are derived, never exported.
        with db.connect() as connection:
            connection.execute('DELETE FROM voiceprint_people')
            connection.execute("DELETE FROM asr_hotword_lists WHERE kind='custom'")
        item = upload(client, data)
        assert item.status_code == 202
        identifier = item.json()['id']
        wait(client, identifier)
        plan = preview(client, identifier)
        assert plan['added'] == {'groups':0,'people':1,'samples':3,'hotword_lists':1}
        result = apply(client, identifier, plan)
        assert result['result']['id_map']['people'][person['id']] == person['id']
        restored = db.get_voiceprint_person(person['id'])
        assert restored['note'] == '一组' and not restored['include_in_hotword_library']
        for original in (first, second):
            restored_sample = db.get_voiceprint_sample(original['id'])
            assert Path(restored_sample['audio_path']).read_bytes() == Path(original['audio_path']).read_bytes()
            assert restored_sample['transcript'] == original['transcript']
            assert restored_sample['source_job_id'] is None
        assert db.get_voiceprint_sample(failed['id'])['state'] == 'failed'
        assert db.get_hotword_list('hotwords_voiceprint_people')['terms'] == []
        assert db.get_job(job['id']) == historical
        replay = client.post(f'/api/v1/library/imports/{identifier}/apply', json={key:plan[key] for key in ('mode','preview_revision')})
        assert replay.status_code == 200
        assert not (store.root(local, identifier)/'source.zip').exists()


def test_merge_existing_identity_terms_duplicate_and_name_collision(local):
    person = db.create_voiceprint_person('张三', note='同组')
    existing = sample(local, person['id'])
    words = db.create_hotword_list('术语', ['现有'])
    with TestClient(api.create_app()) as client:
        manifest, assets = unpack(exported(client))
        manifest['people'][0]['include_in_hotword_library'] = False
        original = manifest['people'][0]['samples'][0]
        different = dict(original, id='incoming_sample', name='源库的另一段录音', audio=dict(original['audio'],path='audio/incoming.wav'))
        content = bytearray(assets[original['audio']['path']]);content[-2:] = b'\x01\x00'
        import hashlib
        different['audio']['sha256'] = hashlib.sha256(content).hexdigest()
        assets['audio/incoming.wav'] = bytes(content)
        manifest['people'][0]['samples'].append(different)
        manifest['hotword_lists'][0]['terms'] = ['新增','现有']
        db.rename_voiceprint_sample(person['id'],existing['id'],'源库的另一段录音')
        identifier = upload(client, repack(manifest, assets)).json()['id']
        wait(client, identifier)
        plan = preview(client, identifier)
        assert (plan['merged_people'],plan['skipped_samples'],plan['renamed_samples']) == (1,1,1)
        result = apply(client, identifier, plan)
        assert db.get_voiceprint_person(person['id'])['include_in_hotword_library']
        assert db.get_voiceprint_sample(existing['id'])['name'] == '源库的另一段录音'
        assert db.get_voiceprint_sample('incoming_sample')['name'] == '源库的另一段录音 (2)'
        assert db.get_hotword_list(words['id'])['terms'] == ['现有','新增']
        assert result['result']['id_map']['samples'][existing['id']] == existing['id']


def test_replace_backup_historical_jobs_and_busy_preconditions(local):
    person = db.create_voiceprint_person('源人员')
    first = sample(local, person['id'])
    with TestClient(api.create_app()) as client:
        data = exported(client)
        target = db.create_voiceprint_person('目标人员')
        old = sample(local, target['id'], value=1)
        job = db.create_job('tts', '进行中的任务', {'voiceprint_person_name':'快照'})
        identifier = upload(client, data).json()['id'];wait(client, identifier)
        plan = preview(client, identifier, 'replace')
        payload = {key:plan[key] for key in ('mode','preview_revision')}
        assert client.post(f'/api/v1/library/imports/{identifier}/apply', json=payload).status_code == 409
        db.update_job(job['id'], state='succeeded', result_json={'duration':1})
        historical = db.get_job(job['id'])
        with reader_lease('voiceprint:'+old['id']):
            response = client.post(f'/api/v1/library/imports/{identifier}/apply', json=payload)
            assert response.status_code == 202
            blocked = wait(client, identifier)
            assert 'being read' in blocked['error']
        result = apply(client, identifier, plan)
        backup = client.get(result['backup_url'])
        previous, _ = unpack(backup.content)
        assert {p['id'] for p in previous['people']} == {person['id'],target['id']}
        assert not Path(old['audio_path']).exists()
        assert not Path(first['audio_path']).exists()
        assert db.get_voiceprint_person(target['id']) is None
        assert Path(db.get_voiceprint_sample(first['id'])['audio_path']).exists()
        assert db.get_job(job['id']) == historical
        assert client.delete(f'/api/v1/library/imports/{identifier}').status_code == 204
        assert client.get(result['backup_url']).status_code == 200


def test_completed_receipt_blocks_writes_until_retired_file_cleanup(local, monkeypatch):
    person = db.create_voiceprint_person('文件收尾')
    original = sample(local, person['id'])
    cleanup_entered, release_cleanup = threading.Event(), threading.Event()
    original_cleanup = transfer.cleanup

    def held_cleanup(config, identifier, completed):
        if completed:
            cleanup_entered.set()
            assert release_cleanup.wait(10), 'Cleanup test gate was not released'
        return original_cleanup(config, identifier, completed)

    with TestClient(api.create_app()) as client:
        identifier = upload(client, exported(client)).json()['id']
        wait(client, identifier)
        plan = preview(client, identifier, 'replace')
        monkeypatch.setattr(transfer, 'cleanup', held_cleanup)
        try:
            response = client.post(f'/api/v1/library/imports/{identifier}/apply',
                                   json={key:plan[key] for key in ('mode','preview_revision')})
            assert response.status_code == 202
            assert cleanup_entered.wait(5)
            receipt = client.get(f'/api/v1/library/imports/{identifier}').json()
            assert receipt['state'] == 'completed' and receipt['result']
            assert Path(original['audio_path']).exists()
            blocked = client.delete(f'/api/v1/library/imports/{identifier}')
            assert blocked.status_code == 409
            assert blocked.json()['code'] == 'library_maintenance'
        finally:
            release_cleanup.set()
        result = wait(client, identifier, 'completed')
        assert not Path(original['audio_path']).exists()
        assert Path(db.get_voiceprint_sample(original['id'])['audio_path']).exists()
        assert client.delete(f'/api/v1/library/imports/{identifier}').status_code == 204
        assert client.get(result['backup_url']).status_code == 200


@pytest.mark.parametrize('mutation', ['checksum','traversal','undeclared','version','duplicate_id','invalid_audio'])
def test_archive_validation_rejects_corruption_without_library_changes(local, mutation):
    person = db.create_voiceprint_person('校验测试');sample(local, person['id'])
    before = store.revision(transfer.current_snapshot())
    with TestClient(api.create_app()) as client:
        manifest, assets = unpack(exported(client))
        original = manifest['people'][0]['samples'][0]
        extra = None
        if mutation == 'checksum':original['audio']['sha256'] = '0'*64
        if mutation == 'traversal':extra = ('../outside.wav', b'bad')
        if mutation == 'undeclared':extra = ('other.txt', b'bad')
        if mutation == 'version':manifest['contract_version'] = 3
        if mutation == 'duplicate_id':manifest['people'].append(manifest['people'][0])
        if mutation == 'invalid_audio':
            import hashlib
            assets[original['audio']['path']] = b'not audio'
            original['audio'].update(sha256=hashlib.sha256(b'not audio').hexdigest(),size_bytes=9)
        identifier = upload(client, repack(manifest, assets, extra)).json()['id']
        result = wait(client, identifier, 'failed')
        assert result['error']
        assert store.revision(transfer.current_snapshot()) == before
        assert not (store.root(local, identifier)/'assets').exists()
        assert not (local.data_dir.parent/'outside.wav').exists()


def test_pending_missing_audio_idempotency_expiry_and_stale_preview(local):
    person = db.create_voiceprint_person('待入库')
    pending = db.create_voiceprint_sample(person['id'])
    with TestClient(api.create_app()) as client:
        assert client.get('/api/v1/library/export').status_code == 409
        db.update_voiceprint_sample(pending['id'], state='failed')
        data = exported(client)
        assert client.post('/api/v1/library/imports',files={'file':('x.zip',data)}).status_code == 400
        key = uuid.uuid4().hex
        first = upload(client,data,key);assert first.status_code == 202
        identifier = first.json()['id'];wait(client, identifier)
        repeated = upload(client,data,key)
        assert repeated.status_code == 200 and repeated.headers['Idempotency-Replayed'] == 'true'
        assert upload(client,data+b'x',key).status_code == 409
        plan = preview(client,identifier)
        db.update_voiceprint_person(person['id'], note='已修改')
        payload = {key:plan[key] for key in ('mode','preview_revision')}
        assert client.post(f'/api/v1/library/imports/{identifier}/apply',json=payload).status_code == 409
        assert client.delete(f'/api/v1/library/imports/{identifier}').status_code == 204
        assert upload(client,data,key).status_code == 409
        missing = db.create_voiceprint_sample(person['id'],state='ready',audio_path=str(local.voiceprints_dir/'missing.wav'))
        assert client.get('/api/v1/library/export').status_code == 409


def test_copy_failure_rolls_back_and_restart_cleans_uncommitted_files(local, monkeypatch):
    person = db.create_voiceprint_person('原库');sample(local,person['id'])
    with TestClient(api.create_app()) as client:
        data = exported(client)
        manifest, assets = unpack(data)
        manifest['people'][0]['id'] = 'other_person';manifest['people'][0]['name'] = '导入人员';manifest['people'][0]['aliases'] = []
        identifier = upload(client,repack(manifest, assets)).json()['id'];wait(client,identifier)
        plan = preview(client,identifier)
    before = store.revision(transfer.current_snapshot())
    store.update(identifier,state='applying')
    def failure(*args, **kwargs):raise OSError('simulated copy failure')
    with monkeypatch.context() as scoped:
        scoped.setattr(shutil,'copyfile',failure)
        transfer.apply(local,identifier)
    assert store.get(identifier)['state'] == 'ready'
    assert store.revision(transfer.current_snapshot()) == before
    orphan = local.voiceprints_dir/'other_person'/'partial.wav'
    orphan.parent.mkdir(parents=True,exist_ok=True);orphan.write_bytes(b'partial')
    transfer.atomic_json(store.root(local,identifier)/'files.json',{'new_paths':[str(orphan)],'old_paths':[]})
    store.update(identifier,state='applying')
    transfer.recover(local)
    assert not orphan.exists() and store.get(identifier)['state'] == 'ready'
    assert store.revision(transfer.current_snapshot()) == before


def test_authenticated_download_backup_range_and_same_origin(local, monkeypatch):
    protected = replace(local,api_key='library-test-key')
    monkeypatch.setattr(api,'settings',protected)
    monkeypatch.setattr(db,'settings',protected)
    person = db.create_voiceprint_person('鉴权');audio = sample(protected,person['id'])
    headers = {'Authorization':'Bearer library-test-key'}
    with TestClient(api.create_app()) as client:
        assert client.get('/api/v1/library/export').status_code == 401
        assert client.get('/api/v1/library/imports').status_code == 401
        assert client.get('/api/v1/library/export',headers=headers).status_code == 200
        response = client.post('/api/v1/auth/session',headers=headers)
        assert response.status_code == 204
        client.headers['Origin'] = 'http://testserver'
        data = exported(client)
        assert client.post('/api/v1/library/imports',headers={'Origin':'https://elsewhere.example','Idempotency-Key':uuid.uuid4().hex},files={'file':('x.zip',data)}).status_code == 403
        identifier = upload(client,data).json()['id'];wait(client,identifier)
        result = apply(client,identifier,preview(client,identifier,'replace'))
        assert client.get(result['backup_url'],headers={'Range':'bytes=0-9'}).status_code == 206
        response = client.get(f"/api/v1/voiceprints/samples/{audio['id']}/audio",headers={'Range':'bytes=0-9'})
        assert response.status_code == 206 and len(response.content) == 10
        client.delete('/api/v1/auth/session')
        assert client.get(result['backup_url']).status_code == 401


def test_api_people_sorting_full_pinyin_surnames_and_symbol_group(local):
    names = ['张三','曾华','单明','区华','Alice','bob','李四','阿明','Émile','123','🎤人','李明']
    for name in names:
        person = db.create_voiceprint_person(name)
        sample(local,person['id'])
    with TestClient(api.create_app()) as client:
        people = client.get('/api/v1/voiceprints/people').json()['items']
        assert [p['name'] for p in people] == ['Alice','阿明','bob','Émile','李明','李四','区华','单明','曾华','张三','123','🎤人']
        assert [p['sort_initial'] for p in people] == ['A','A','B','E','L','L','O','S','Z','Z','#','#']
        assert [p['name'] for p in client.get('/api/v1/tts/voices').json()['items']] == [p['name'] for p in people]


def test_v12_migration_adds_receipts_without_changing_history(local):
    job = db.create_job('asr','历史',{'text':'原快照'})
    before = db.get_job(job['id'])
    with db.connect() as connection:
        connection.execute('DROP TABLE library_imports')
        connection.execute('UPDATE schema_meta SET version=12')
    db.init_db();db.init_db()
    assert db.get_job(job['id']) == before
    with db.connect() as connection:
        assert connection.execute('SELECT version FROM schema_meta').fetchone()[0] == 14
        assert connection.execute('SELECT COUNT(*) FROM library_imports').fetchone()[0] == 0


def test_upload_limits_admission_and_import_gate(local, monkeypatch):
    with TestClient(api.create_app()) as client:
        data = exported(client)
    limited = replace(local, max_upload_bytes=100)
    monkeypatch.setattr(api,'settings',limited)
    with TestClient(api.create_app()) as client:
        assert upload(client,data).status_code == 413
        assert client.post('/api/v1/library/imports',headers={'Idempotency-Key':uuid.uuid4().hex},files={'file':('data.txt',b'wrong')}).status_code == 422
    blocked = replace(local,min_free_disk_bytes=10**30)
    monkeypatch.setattr(api,'settings',blocked)
    with TestClient(api.create_app()) as client:
        result = upload(client,data)
        assert result.status_code == 429 and int(result.headers['Retry-After']) > 0
        assert client.app.state.admission.active == 0
    monkeypatch.setattr(api,'settings',local)
    with TestClient(api.create_app()) as client:
        identifier = upload(client,data).json()['id'];wait(client,identifier)
        plan = preview(client,identifier,'replace')
        client.app.state.library_requests = 1
        response = client.post(f'/api/v1/library/imports/{identifier}/apply',json={key:plan[key] for key in ('mode','preview_revision')})
        assert response.status_code == 409
        assert not client.app.state.library_changing
        client.app.state.library_requests = 0
        client.app.state.library_changing = True
        assert client.post('/api/v1/voiceprints/people',json={'name':'被阻止'}).status_code == 409
        assert client.post(f'/api/v1/library/imports/{identifier}/preview',json={'mode':'merge'}).status_code == 409
        assert client.delete(f'/api/v1/library/imports/{identifier}').status_code == 409
        client.app.state.library_changing = False


def test_restored_sample_cannot_retry_a_historical_enrollment(local):
    person = db.create_voiceprint_person('历史入库')
    job = db.create_job('asr','失败入库',{'purpose':'voiceprint_import','voiceprint_sample_id':'historical_sample'})
    db.update_job(job['id'],state='failed')
    original = db.create_voiceprint_sample(person['id'],sample_id='historical_sample',source_job_id=job['id'],state='failed')
    with TestClient(api.create_app()) as client:
        data = exported(client)
        identifier = upload(client,data).json()['id'];wait(client,identifier)
        apply(client,identifier,preview(client,identifier,'replace'))
        assert db.get_voiceprint_sample(original['id'])['source_job_id'] is None
        assert client.post(f"/api/v1/jobs/{job['id']}/retry").status_code == 409
        assert db.get_job(job['id'])['state'] == 'failed'


def test_openapi_transfer_contract_and_sort_metadata(local):
    schema = api.create_app().openapi()
    upload_schema = schema['paths']['/api/v1/library/imports']['post']
    header = next(p for p in upload_schema['parameters'] if p['name']=='Idempotency-Key')
    assert header['required'] and header['schema']['minLength'] == 8
    assert upload_schema['requestBody']['content']['multipart/form-data']['schema']['required'] == ['file']
    assert {'202','409','413','429'} <= set(upload_schema['responses'])
    assert 'sort_initial' in schema['components']['schemas']['VoiceprintPersonResponse']['required']
    assert 'id_map' in schema['components']['schemas']['LibraryResult']['properties']
    assert all('security' in operation for path,methods in schema['paths'].items() if path.startswith('/api/v1/library/') for operation in methods.values())
