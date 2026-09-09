from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import uuid
import wave

import pytest
from fastapi.testclient import TestClient

from audio_intel import api as api_module, db
from audio_intel.config import settings


@pytest.fixture
def local(tmp_path, monkeypatch):
    local = replace(settings, data_dir=tmp_path/'data', temp_dir=tmp_path/'tmp',
                    api_key='', mock_mode=True, deployment_profile='cpu',
                    enabled_services=frozenset({'asr', 'tts'}), min_free_disk_bytes=0)
    monkeypatch.setattr(db, 'settings', local)
    monkeypatch.setattr(api_module, 'settings', local)
    db.init_db()
    return local


def ready_sample(local, person_id, name='参考音频'):
    path = local.voiceprints_dir/person_id/f'{uuid.uuid4().hex}.wav'
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b'\0\0'*16000)
    return db.create_voiceprint_sample(person_id, name=name, state='ready',
                                      audio_path=str(path), transcript='你好。', duration=1)


def test_person_identity_and_shared_hotwords(local):
    first = db.create_voiceprint_person(' Ａlice  Li ', note=' Team  A ')
    second = db.create_voiceprint_person('Alice Li', note='Team B')
    blank = db.create_voiceprint_person('Alice Li')
    for note in ('team a', 'Ｔeam A', '  Team A  ', None, '', '   '):
        with pytest.raises(sqlite3.IntegrityError):
            db.create_voiceprint_person('alice li', note=note)
    with pytest.raises(sqlite3.IntegrityError):
        db.update_voiceprint_person(second['id'], note='team a')
    with pytest.raises(sqlite3.IntegrityError):
        db.update_voiceprint_person(second['id'], note=None)
    with pytest.raises(db.AmbiguousVoiceprintPersonError):
        db.find_voiceprint_person('Alice Li')
    full_id = 'hotwords_voiceprint_people'
    short_id = 'hotwords_voiceprint_people_short'
    assert db.get_hotword_list(full_id)['terms'] == ['Alice Li']
    assert db.get_hotword_list(short_id)['terms'] == ['Alice']
    before = db.get_hotword_list(full_id)
    db.update_voiceprint_person(second['id'], note='Team C')
    assert db.get_hotword_list(full_id) == before
    db.delete_voiceprint_person_record(first['id'])
    db.update_voiceprint_person(second['id'], include_in_hotword_library=False)
    assert db.get_hotword_list(full_id)['terms'] == ['Alice Li']
    db.delete_voiceprint_person_record(blank['id'])
    assert db.get_hotword_list(full_id)['terms'] == []
    assert db.get_hotword_list(short_id)['terms'] == []
    assert db.find_voiceprint_person('Alice Li')['id'] == second['id']


def test_concurrent_creation_enforces_identity_and_allocates_sample_names(local):
    def create_person(_):
        try:
            return db.create_voiceprint_person('同名', note='备注')['id']
        except sqlite3.IntegrityError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        people = [value for value in pool.map(create_person, range(6)) if value]
        samples = list(pool.map(lambda _: db.create_voiceprint_sample(people[0], name='音'*80), range(6)))
    assert len(people) == 1
    assert len({sample['name'] for sample in samples}) == 6
    assert all(len(sample['name']) <= 80 for sample in samples)
    assert all(sample['person_id'] == people[0] for sample in samples)


@pytest.mark.parametrize('state', ['pending', 'ready', 'failed'])
def test_rename_sample_contract_and_background_update(local, state):
    person = db.create_voiceprint_person('同名', note='甲')
    other = db.create_voiceprint_person('同名', note='乙')
    sample = db.create_voiceprint_sample(person['id'], name='旧名', state=state)
    db.create_voiceprint_sample(person['id'], name='已占用')
    db.create_voiceprint_sample(other['id'], name='会议录音')
    url = f"/api/v1/voiceprints/people/{person['id']}/samples/{sample['id']}"
    with TestClient(api_module.create_app()) as client:
        renamed = client.patch(url, json={'name': '  会议录音 '})
        assert renamed.status_code == 200
        assert renamed.json()['name'] == '会议录音'
        assert renamed.json()['state'] == state
        assert 'name_key' not in renamed.json()
        assert client.patch(url, json={'name': '已占用'}).status_code == 409
        for payload in ({'name': ''}, {'name': ' '}, {'name': 'a'*81}, {'name': 'a\nb'},
                        {'name': None}, {'name': '合法', 'transcript': '替换'}, {}):
            assert client.patch(url, json=payload).status_code == 422
        wrong = f"/api/v1/voiceprints/people/{other['id']}/samples/{sample['id']}"
        assert client.patch(wrong, json={'name': 'test'}).status_code == 404
        assert client.patch(url+'missing', json={'name': 'test'}).status_code == 404
        assert client.post('/api/v1/voiceprints/people', json={'name':'同名','note':'甲'}).status_code == 409
    db.update_voiceprint_sample(sample['id'], state='ready', transcript='分析结果')
    assert db.get_voiceprint_sample(sample['id'])['name'] == '会议录音'


def test_upload_source_names_and_replay_after_rename(local):
    person = db.create_voiceprint_person('上传测试')
    url = f"/api/v1/voiceprints/people/{person['id']}/samples/upload"
    key = {'Idempotency-Key': str(uuid.uuid4())}
    with TestClient(api_module.create_app()) as client:
        first = client.post(url, headers=key, files={'file':('会议 录音.wav',b'RIFF', 'audio/wav')})
        assert first.status_code == 202
        sample = first.json()['sample']
        assert sample['name'] == '会议 录音'
        db.rename_voiceprint_sample(person['id'], sample['id'], '重命名')
        replay = client.post(url, headers=key, files={'file':('会议 录音.wav',b'RIFF','audio/wav')})
        assert replay.status_code == 200
        assert replay.json()['sample']['name'] == '重命名'
        assert replay.json()['sample']['id'] == sample['id']
        for expected in ('录音 2026-09-09 12:00 UTC', '录音 2026-09-09 12:00 UTC (2)'):
            response = client.post(url, headers={'Idempotency-Key':str(uuid.uuid4())},
                                   files={'file':('recording.webm',b'RIFF','audio/webm')},
                                   data={'name':'录音 2026-09-09 12:00 UTC'})
            assert response.status_code == 202
            assert response.json()['sample']['name'] == expected
        invalid = client.post(url, headers={'Idempotency-Key':str(uuid.uuid4())},
                              files={'file':('audio.wav',b'RIFF','audio/wav')}, data={'name':' '})
        assert invalid.status_code == 422


@pytest.mark.parametrize('sequence', [False, True])
@pytest.mark.parametrize('legacy', [False, True])
def test_tts_replay_keeps_reference_snapshots_after_library_edits(local, sequence, legacy):
    person = db.create_voiceprint_person('张三', note='甲')
    sample = ready_sample(local, person['id'])
    key = {'Idempotency-Key':str(uuid.uuid4())}
    payload = ({'voice_mode':'voiceprint','compute_device':'cpu','items':[{'id':'one','text':'你好','voiceprint_sample_id':sample['id']}]}
               if sequence else {'voice_mode':'voiceprint','compute_device':'cpu','text':'你好','voiceprint_sample_id':sample['id']})
    endpoint = '/api/v1/tts/sequence-jobs' if sequence else '/api/v1/tts/jobs'
    def submit(client, value):
        return client.post(endpoint, headers=key, **({'json':value} if sequence else {'data':value}))
    with TestClient(api_module.create_app()) as client:
        response = submit(client, payload)
        assert response.status_code == 202, response.text
        job = response.json()
        snapshot = job['request']['voiceprint_references'][sample['id']] if sequence else job['request']
        assert snapshot['voiceprint_person_note'] == '甲'
        assert snapshot['voiceprint_sample_name'] == sample['name']
        audio = Path(snapshot['reference_audio_path']).read_bytes()
        if legacy:
            snapshot.pop('voiceprint_person_note')
            snapshot.pop('voiceprint_sample_name')
            with db.connect() as connection:
                connection.execute('UPDATE jobs SET request_json=? WHERE id=?',(json.dumps(job['request'],ensure_ascii=False),job['id']))
                connection.execute("UPDATE job_idempotency SET request_hash='pre-v10-fingerprint' WHERE job_id=?", (job['id'],))
        db.update_voiceprint_person(person['id'], name='张三丰', note='乙')
        db.rename_voiceprint_sample(person['id'], sample['id'], '新样本名')
        replay = submit(client, payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()['id'] == job['id']
        assert replay.json()['request'] == job['request']
        assert Path(snapshot['reference_audio_path']).read_bytes() == audio
        changed = json.loads(json.dumps(payload))
        if sequence:
            changed['items'][0]['text'] = '改变文本'
        else:
            changed['text'] = '改变文本'
        assert submit(client, changed).status_code == 409
        db.update_job(job['id'], state='failed')
        retry = client.post(f"/api/v1/jobs/{job['id']}/retry")
        assert retry.status_code == 200
        assert retry.json()['request'] == job['request']
        if legacy:
            with db.connect() as connection:
                assert connection.execute('SELECT request_hash FROM job_idempotency WHERE job_id=?',(job['id'],)).fetchone()[0] == 'pre-v10-fingerprint'


def test_legacy_voice_creation_rejects_ambiguous_people_without_leaking_audio(local):
    db.create_voiceprint_person('同名', note='甲')
    db.create_voiceprint_person('同名', note='乙')
    before = list(local.voices_dir.rglob('*'))
    with TestClient(api_module.create_app()) as client:
        response = client.post('/api/v1/tts/voices', data={'name':'同名','ref_text':'你好'},
                               files={'ref_audio':('voice.wav', b'RIFF', 'audio/wav')})
    assert response.status_code == 409
    assert response.json()['code'] == 'ambiguous_voiceprint_person'
    assert list(local.voices_dir.rglob('*')) == before


def downgrade_names_to_v9(local):
    with sqlite3.connect(local.database_path, isolation_level=None) as connection:
        connection.executescript('''
            PRAGMA foreign_keys=OFF;
            CREATE TABLE people_v9(id TEXT PRIMARY KEY,name TEXT NOT NULL,name_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL,note TEXT,include_in_hotword_library INTEGER NOT NULL DEFAULT 1);
            INSERT INTO people_v9 SELECT id,name,name_key,created_at,updated_at,note,include_in_hotword_library FROM voiceprint_people;
            DROP TABLE voiceprint_people;
            ALTER TABLE people_v9 RENAME TO voiceprint_people;
            DROP INDEX idx_voiceprint_sample_name;
            ALTER TABLE voiceprint_samples DROP COLUMN name;
            ALTER TABLE voiceprint_samples DROP COLUMN name_key;
            UPDATE schema_meta SET version=9;
        ''')


def test_v9_migration_preserves_library_numbering_and_history(local):
    person = db.create_voiceprint_person('原人员', note='原备注')
    job = db.create_job('asr', '历史', {'context':'快照'})
    db.update_job(job['id'], result_json={'text':'历史正文'}, state='succeeded')
    for index in range(3):
        db.create_voiceprint_sample(person['id'], sample_id=f'sample-{index}', source_job_id=job['id'])
    order = [sample['id'] for sample in db.list_voiceprint_samples(person['id'])]
    original_job = db.get_job(job['id'])
    downgrade_names_to_v9(local)
    db.init_db()
    db.init_db()
    samples = db.list_voiceprint_samples(person['id'])
    assert [(s['id'],s['name']) for s in samples] == [(sample_id,f'样本 {3-index}') for index,sample_id in enumerate(order)]
    assert db.get_job(job['id']) == original_job
    assert db.get_voiceprint_person(person['id'])['note'] == '原备注'
    db.rename_voiceprint_sample(person['id'], samples[0]['id'], '自定义名')
    db.delete_voiceprint_sample_record(samples[1]['id'])
    db.init_db()
    assert db.get_voiceprint_sample(samples[0]['id'])['name'] == '自定义名'
    assert db.get_voiceprint_sample(samples[2]['id'])['name'] == '样本 1'
    with db.connect() as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('SELECT version FROM schema_meta').fetchone()[0] == 10
        assert connection.execute('SELECT person_id FROM voiceprint_aliases WHERE alias_id=?',(person['id'],)).fetchone()[0] == person['id']


def test_migration_failure_rolls_back_and_restores_foreign_keys(local):
    person = db.create_voiceprint_person('迁移测试')
    db.create_voiceprint_sample(person['id'])
    downgrade_names_to_v9(local)
    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith('CREATE UNIQUE INDEX'):
                raise sqlite3.OperationalError('simulated migration interruption')
            return super().execute(sql, *args)
    with sqlite3.connect(local.database_path, isolation_level=None, factory=FailingConnection) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        with pytest.raises(sqlite3.OperationalError, match='interruption'):
            db._migrate_voiceprint_names(connection)
        assert connection.execute('SELECT version FROM schema_meta').fetchone()[0] == 9
        assert 'name' not in {row['name'] for row in connection.execute('PRAGMA table_info(voiceprint_samples)')}
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM voiceprint_samples').fetchone()[0] == 1
    db.init_db()


def test_sample_rename_is_authenticated_and_schema_documents_contract(local, monkeypatch):
    person = db.create_voiceprint_person('受保护人员')
    sample = db.create_voiceprint_sample(person['id'])
    monkeypatch.setattr(api_module, 'settings', replace(local, api_key='test-only-secret'))
    url = f"/api/v1/voiceprints/people/{person['id']}/samples/{sample['id']}"
    with TestClient(api_module.create_app()) as client:
        assert client.patch(url, json={'name':'新名'}).status_code == 401
        assert client.patch(url, json={'name':'新名'}, headers={'Authorization':'Bearer test-only-secret'}).status_code == 200
        schema = client.get('/openapi.json',headers={'Authorization':'Bearer test-only-secret'}).json()
        operation = schema['paths']['/api/v1/voiceprints/people/{person_id}/samples/{sample_id}']['patch']
        assert {'200','401','404','409','422'} <= set(operation['responses'])
        assert operation['requestBody']['content']['application/json']['examples']['rename_sample']['value'] == {'name':'会议室录音'}
        rename = schema['components']['schemas']['VoiceprintSampleRenameRequest']
        assert rename['required'] == ['name']
        assert rename['additionalProperties'] is False
        assert 'name' in schema['components']['schemas']['VoiceprintSampleResponse']['required']
        assert 'normalized name and note' in schema['components']['schemas']['VoiceprintPersonCreateRequest']['properties']['name']['description']
