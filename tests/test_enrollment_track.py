import io
import json
import numpy as np
import soundfile as sf
import pytest
from audio_intel import db
from test_video_transcription import local, client

@pytest.mark.parametrize('video', [False, True])
def test_enrollment_uses_selected_track(client, local, video):
    job = db.create_job('asr', 'sample.wav', {})
    folder = local.jobs_dir / job['id']
    (folder/'input').mkdir(parents=True)
    (folder/'output').mkdir()
    original = folder / ('output/playback.wav' if video else 'input/source.wav')
    sf.write(original, np.full(16000, .1), 16000, subtype='PCM_16')
    enhanced = folder/'output/enhanced.wav'
    sf.write(enhanced, np.full(16000, .4), 16000, subtype='PCM_16')
    result = {'language':'Chinese','segments':[{'id':i,'start':.1,'end':.6,'speaker':'s0','text':'测试'} for i in range(4)],
              'audio_enhancement':{'playback_url':f"/api/v1/jobs/{job['id']}/artifacts/enhanced.wav"},
              'artifacts':[{'name':'enhanced.wav','path':str(enhanced)}]}
    if video: result['video'] = {'revision':1}
    db.update_job(job['id'], state='succeeded', request_json={'input_path':str(original)}, result_json=result)
    with db.connect() as connection:
        connection.execute('UPDATE jobs SET request_json=? WHERE id=?',(json.dumps({'input_path':str(original)}),job['id']))
    person = client.post('/api/v1/voiceprints/people',json={'name':'Track tester'}).json()
    url = f"/api/v1/voiceprints/people/{person['id']}/samples/from-asr"
    for i, track, expected in [(0,None,.1),(1,'enhanced',.4)]:
        body = {'job_id':job['id'],'segment_ids':[i]}
        if track: body['audio_track'] = track
        response = client.post(url,json=body)
        assert response.status_code == 201, response.text
        sample = response.json()['items'][0]
        audio, rate = sf.read(io.BytesIO(client.get(sample['audio_url']).content))
        assert rate == 16000 and len(audio) == 8000
        assert np.mean(audio) == pytest.approx(expected,abs=.001)
    assert client.post(url,json={'job_id':job['id'],'segment_ids':[0],'audio_track':'enhanced'}).status_code == 409
    schema = client.get('/openapi.json').json()['components']['schemas']['AddAsrSamplesRequest']['properties']['audio_track']
    assert schema['default'] == 'original' and schema['enum'] == ['original','enhanced']
    enhanced.unlink()
    body = {'job_id':job['id'],'segment_ids':[2],'audio_track':'enhanced'}
    assert client.post(url,json=body).status_code == 404
    body['audio_track'] = 'bad'
    assert client.post(url,json=body).status_code == 422
    assert len(db.list_voiceprint_samples(person['id'])) == 2
