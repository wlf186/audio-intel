"""Resumable video submission example; Ctrl-C preserves the small resume journal."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('video', type=Path)
    parser.add_argument('--state', type=Path, default=Path('tmp/video-upload-client.json'))
    parser.add_argument('--device', choices=('cpu', 'gpu'))
    parser.add_argument('--language', default='Auto')
    parser.add_argument('--pdf', type=Path, help='Wait for completion and download the concise PDF')
    args = parser.parse_args()
    source = args.video.resolve()
    identity = {'path': str(source), 'size': source.stat().st_size}
    if args.state.exists():
        state = json.loads(args.state.read_text(encoding='utf-8'))
        if state['file'] != identity:
            raise ValueError('Resume journal belongs to another file; use a different --state')
    else:
        state = {'file': identity, 'create_key': uuid.uuid4().hex, 'job_key': uuid.uuid4().hex,
                 'parameters': {'language': args.language, 'compute_device': args.device}}

    def save() -> None:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.state.with_suffix(args.state.suffix + '.partial')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, args.state)

    save()
    base_url = os.getenv('AUDIO_INTEL_BASE_URL', 'http://127.0.0.1:20810')
    api_key = os.getenv('AUDIO_INTEL_API_KEY', '')
    with httpx.Client(base_url=base_url, headers={'Authorization': f'Bearer {api_key}'} if api_key else {}, timeout=300) as client:
        def call(method: str, url: str, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        if state.get('upload_id'):
            upload = call('GET', '/api/v1/asr/video-uploads/' + state['upload_id'])
        else:
            upload = call('POST', '/api/v1/asr/video-uploads', headers={'Idempotency-Key': state['create_key']},
                          json={'name': source.name, 'size_bytes': identity['size']})
            state['upload_id'] = upload['id']
            save()
        url = '/api/v1/asr/video-uploads/' + upload['id']
        if not upload['job_id']:
            with source.open('rb') as stream:
                for chunk in upload['chunks']:
                    stream.seek(chunk['offset'])
                    if hashlib.sha256(stream.read(chunk['size_bytes'])).hexdigest() != chunk['sha256']:
                        raise ValueError('Local file differs from confirmed server bytes')
                stream.seek(upload['offset'])
                while upload['offset'] < identity['size']:
                    block = stream.read(upload['chunk_bytes'])
                    upload = call('PATCH', url, content=block, headers={
                        'Content-Type': 'application/octet-stream', 'Upload-Offset': str(upload['offset']),
                        'Upload-Checksum': hashlib.sha256(block).hexdigest()})
                    print(f"Uploaded {upload['offset']} / {identity['size']} bytes", flush=True)
            job = call('POST', '/api/v1/asr/video-jobs', headers={'Idempotency-Key': state['job_key']},
                       json={**state['parameters'], 'upload_id': upload['id']})
        else:
            job = call('GET', '/api/v1/jobs/' + upload['job_id'])
        print('Job:', job['id'], flush=True)
        args.state.unlink(missing_ok=True)
        if args.pdf:
            while job['state'] not in {'succeeded', 'failed', 'cancelled'}:
                time.sleep(job.get('poll_after_seconds') or 2)
                job = call('GET', '/api/v1/jobs/' + job['id'])
            if job['state'] != 'succeeded':
                raise RuntimeError(job.get('error_message') or job['state'])
            with client.stream('GET', f"/api/v1/jobs/{job['id']}/transcript.pdf") as response:
                response.raise_for_status()
                args.pdf.parent.mkdir(parents=True, exist_ok=True)
                with args.pdf.open('wb') as output:
                    for block in response.iter_bytes():
                        output.write(block)
            print('PDF:', args.pdf)


if __name__ == '__main__':
    main()
