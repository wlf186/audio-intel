"""Paired real-model document narration smoke benchmark (isolated service recommended).

Creates labelled jobs and imports; does not change existing library documents.
Run with the API runtime and explicit --base-url. Writes a JSON report and WAV
reference beside --output. Inspect/listen to the resulting MP3 artifacts as well.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid

import httpx


TEXT = '# 第一章\n\n真正的知识来自思考。[^1]\n\n普通数字需要保留，比如收入八十五元。\n\n[^1]: 这是一段不需要朗读的脚注。\n\n# 第二章\n\n换一个角度，继续学习。'
REFERENCE = '你好，这是一段用来验证文档合成的参考声音。希望你能听到自然清晰的表达。'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', default='qwen3-tts-0.6b')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    headers = {'Authorization':'Bearer '+os.environ['AUDIO_INTEL_API_KEY']} if os.getenv('AUDIO_INTEL_API_KEY') else {}
    report = {'model':args.model,'runs':[]}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=120) as client:
        def post(path, **kwargs):
            r=client.post(path,headers={'Idempotency-Key':uuid.uuid4().hex},**kwargs)
            r.raise_for_status();return r.json()
        def wait(path):
            deadline=time.monotonic()+1200
            while time.monotonic()<deadline:
                r=client.get(path);r.raise_for_status();item=r.json()
                if item['state'] in {'succeeded','ready'}:return item
                if item['state'] in {'failed','cancelled'}:raise RuntimeError(str(item))
                time.sleep(.5)
            raise TimeoutError(path)
        common={'model':args.model,'compute_device':'gpu','language':'Chinese','accelerate_single_task':'true'}
        reference=post('/api/v1/tts/jobs',data={**common,'voice_mode':'preset','speaker':'Vivian','text':REFERENCE,'display_name':'narration-benchmark-reference'})
        reference=wait('/api/v1/jobs/'+reference['id'])
        artifact=reference['result']['artifacts'][0]
        r=client.get(f"/api/v1/jobs/{reference['id']}/artifacts/{artifact['name']}");r.raise_for_status()
        reference_path=args.output.with_suffix('.reference.wav');reference_path.write_bytes(r.content)
        report['reference_job_id']=reference['id']
        imported=post('/api/v1/tts/document-imports',files={'file':('narration-benchmark.md',TEXT.encode(),'text/markdown')})
        imported=wait('/api/v1/tts/document-imports/'+imported['id'])
        report['import_id']=imported['id']
        for mode in ['preset','inline_clone']:
            for enabled in [False,True]:
                options={'skip_enabled':enabled,'natural_pauses':enabled}
                preview=post(f"/api/v1/tts/document-imports/{imported['id']}/preview",json={'narration_options':options})
                data={**common,'document_import_id':imported['id'],'preview_revision':preview['preview_revision'],
                      'section_ids':[s['id'] for s in preview['sections'] if s.get('speakable',True)],
                      'narration_options':json.dumps(options),'voice_mode':mode,'display_name':f'narration-benchmark-{mode}-{enabled}'}
                files=None
                if mode=='preset':data['speaker']='Vivian'
                else:
                    data['reference_text']=REFERENCE
                    files={'reference_audio':('reference.wav',reference_path.read_bytes(),'audio/wav')}
                started=time.monotonic()
                job=post('/api/v1/tts/document-jobs',data=data,files=files)
                print('submitted',mode,enabled,job['id'],flush=True)
                job=wait('/api/v1/jobs/'+job['id'])
                result=job['result']
                assert result['compute_device']=='gpu' and result['precision']=='BF16'
                report['runs'].append({'job_id':job['id'],'voice_mode':mode,'enabled':enabled,
                    'elapsed_seconds':round(time.monotonic()-started,3),'duration':result['duration'],
                    'acceleration':result['acceleration'],'generation_guard':result.get('generation_guard'),
                    'artifacts':result['artifacts']})
                args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print('completed',mode,enabled,result['duration'],flush=True)
    print(args.output)


if __name__=='__main__':main()
