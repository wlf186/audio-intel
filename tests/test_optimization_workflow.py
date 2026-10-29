import asyncio
import httpx
from fastapi.testclient import TestClient
from audio_intel import api, db, optimization_engine as engine, optimization_store as store
from test_optimization import configured, job, source, start, url, wait
from test_voiceprint_groups_exports import local


def test_review_is_candidate_and_pause_resume_finishes_same_work(configured, monkeypatch):
    async def slow(*args):
        await asyncio.sleep(60)
    monkeypatch.setattr(engine, 'review', slow)
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        assert start(client, identifier).status_code == 202
        state = wait(client, identifier, lambda s: s['candidate'] and s['candidate']['phase'] == 'review')
        run = state['candidate']['id']
        assert state['published'] is None
        assert client.get(url(identifier) + '/result', params={'run_id': run}).status_code == 409
        assert client.post(url(identifier) + '/pause', json={'run_id': run}).status_code == 200
        assert store.get(run)['state'] == 'paused'
        async def passes(*args):
            return {'outcome': 'pass', 'details': {'stage': 'review', 'issues': []}}
        monkeypatch.setattr(engine, 'review', passes)
        assert client.post(url(identifier) + '/resume', json={'run_id': run}).status_code == 200
        done = wait(client, identifier, lambda s: bool(s['published']))['published']
        assert done['id'] == run and done['phase'] == 'complete'
        assert done['reviewed_parts'] == done['total_parts'] == 2
        assert client.get(url(identifier) + '/result', params={'run_id': run}).status_code == 200


def test_review_outage_finishes_with_notice_and_bounded_calls(configured, monkeypatch):
    calls = []
    async def offline(*args):
        calls.append(1)
        raise httpx.ConnectError('offline')
    monkeypatch.setattr(engine, 'review', offline)
    identifier = job(source(5))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        done = wait(client, identifier, lambda s: bool(s['published']))['published']
        assert len(calls) == 3
        assert done['unavailable_units'] == 5
        assert done['review_state'] == 'completed'
        assert client.post(f'/api/v1/jobs/{identifier}/transcript-export', json={'format':'txt','text_version':'optimized','optimization_id':done['id']}).status_code == 200


def test_cleanup_migration_is_once_and_preserves_parent(configured):
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        wait(client, identifier, lambda s: bool(s['published']))
    original = db.get_job(identifier)['result']
    with db.connect() as c:
        c.execute('UPDATE schema_meta SET version=18')
    db.init_db()
    assert store.status(identifier) == {'published': None, 'candidate': None}
    assert db.get_job(identifier)['result'] == original
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        done = wait(client, identifier, lambda s: bool(s['published']))['published']['id']
    db.init_db()
    assert store.status(identifier)['published']['id'] == done


def test_scene_invalidation_rolls_back_with_result_transaction(configured):
    from copy import deepcopy
    identifier = job(source(2))
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        published = wait(client, identifier, lambda s: bool(s['published']))['published']['id']
    before = db.get_job(identifier)['result']
    after = deepcopy(before)
    after['video'] = {'scenes': [{'id':'first','start':0,'end':6}]}
    try:
        with db.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            assert store.invalidate_scene_change(c, identifier, before, after)
            raise RuntimeError('scene commit failed')
    except RuntimeError:
        pass
    assert store.status(identifier)['published']['id'] == published
    with db.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        assert not store.invalidate_scene_change(c, identifier, before, before)
        assert store.invalidate_scene_change(c, identifier, before, after)
        c.commit()
    assert store.status(identifier)['published'] is None


def test_scene_edit_cancels_check_and_conflict_resumes_same_candidate(configured, monkeypatch):
    from copy import deepcopy
    import threading
    cancelled = threading.Event()
    async def slow(*args):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()
    monkeypatch.setattr(engine, 'review', slow)
    data = source(2)
    scenes = [{'id':'a','start':0,'end':3,'frame_time':1,'image':'a.jpg'},
              {'id':'b','start':3,'end':6,'frame_time':4,'image':'b.jpg'}]
    data['video'] = {'contract_version':1,'time_origin':0,'detector':'presentation-v1','revision':1,'duration':6,'scenes':scenes,'original_scenes':deepcopy(scenes)}
    identifier = job(data)
    root = f'/api/v1/jobs/{identifier}/video-scenes'
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: s['candidate'] and s['candidate']['phase']=='review')['candidate']['id']
        # Stale scene revisions must preserve and resume the same unfinished work.
        response = client.patch(root,json={'revision':2,'action':'merge_next','scene_id':'a'})
        assert response.status_code == 409
        assert cancelled.wait(1)
        resumed = wait(client, identifier, lambda s: s['candidate'] and s['candidate']['state']=='running')
        assert resumed['candidate']['id'] == run
        assert db.get_job(identifier)['result'] == data
        response = client.patch(root,json={'revision':1,'action':'merge_next','scene_id':'a'})
        assert response.status_code == 200, response.text
        assert len(response.json()['video']['scenes']) == 1
        assert store.status(identifier) == {'published':None,'candidate':None}
        assert not store.checkpoint_review(run,0,0,{'outcome':'pass','details':{'stage':'review','issues':[]}})
        assert client.get(url(identifier)+'/result',params={'run_id':run}).status_code == 404


def test_export_visibility_keeps_published_optimization(configured):
    from copy import deepcopy
    data = source(2)
    scenes = [{'id':'a','start':0,'end':3,'frame_time':1,'image':'a.jpg'},
              {'id':'b','start':3,'end':6,'frame_time':4,'image':'b.jpg'}]
    data['video'] = {'contract_version':1,'time_origin':0,'detector':'presentation-v1','revision':1,'duration':6,'scenes':scenes,'original_scenes':deepcopy(scenes)}
    identifier = job(data)
    with TestClient(api.create_app()) as client:
        start(client, identifier)
        run = wait(client, identifier, lambda s: bool(s['published']))['published']['id']
        response = client.patch(f'/api/v1/jobs/{identifier}/video-scenes',json={'revision':1,'action':'set_export_visibility','export_hidden_scene_ids':['a']})
        assert response.status_code == 200, response.text
        assert store.status(identifier)['published']['id'] == run
        assert client.get(url(identifier)+'/result',params={'run_id':run}).status_code == 200
