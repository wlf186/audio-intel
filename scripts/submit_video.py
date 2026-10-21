"""Resumable video submission example; Ctrl-C preserves the small resume journal."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
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
    parser.add_argument('--scene-min-change-percent', type=float, help='Minimum changed picture area, 0.5–50 percent')
    parser.add_argument('--scene-min-stable-seconds', type=float, help='Minimum stable picture/screenshot duration, 0.5–5 seconds (0.1-second input step); no qualifying screenshot uses a placeholder')
    parser.add_argument('--pdf', type=Path, help='Wait for completion and download the concise PDF')
    parser.add_argument('--voiceprint-group', action='append', help='Eligible voiceprint group ID; repeat to select several, omit for all')
    parser.add_argument('--no-redact', action='store_true', help='Export original names, notes and text')
    parser.add_argument('--replacements', type=Path, help='UTF-8 JSON array of source/target replacements for PDF export')
    args = parser.parse_args()
    try:
        submit(args)
    except (httpx.HTTPError, OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
        print(f'Submission interrupted: {exc or "Ctrl-C"}', file=sys.stderr)
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
            print(f'Retry-After: {exc.response.headers.get("Retry-After", "unspecified")} seconds', file=sys.stderr)
        if args.state.exists():
            try:
                state = json.loads(args.state.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                state = {}
            if isinstance(state, dict) and state.get('job_id'):
                print('Existing job:', state['job_id'], file=sys.stderr)
            command = [sys.executable, *sys.argv]
            print('Resume with: ' + (subprocess.list2cmdline(command) if os.name == 'nt' else shlex.join(command)), file=sys.stderr)
        raise SystemExit(130 if isinstance(exc, KeyboardInterrupt) else 1) from None


def submit(args: argparse.Namespace) -> None:
    source = args.video.resolve()
    identity = {'path': str(source), 'size': source.stat().st_size}
    if args.state.exists():
        state = json.loads(args.state.read_text(encoding='utf-8'))
        if state['file'] != identity:
            raise ValueError('Resume journal belongs to another file; use a different --state')
    else:
        state = {'file': identity, 'create_key': uuid.uuid4().hex, 'job_key': uuid.uuid4().hex,
                 'parameters': {'language': args.language, 'compute_device': args.device, **({'voiceprint_group_ids': args.voiceprint_group} if getattr(args, 'voiceprint_group', None) else {})}}
        controls = {key: getattr(args, argument) for key, argument in (
            ('min_change_percent', 'scene_min_change_percent'), ('min_stable_seconds', 'scene_min_stable_seconds'))
            if getattr(args, argument, None) is not None}
        if controls:
            state['parameters']['video_scene_detection'] = controls

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

        if state.get('job_id'):
            job = call('GET', '/api/v1/jobs/' + state['job_id'])
            upload = None
        elif state.get('upload_id'):
            upload = call('GET', '/api/v1/asr/video-uploads/' + state['upload_id'])
        else:
            upload = call('POST', '/api/v1/asr/video-uploads', headers={'Idempotency-Key': state['create_key']},
                          json={'name': source.name, 'size_bytes': identity['size']})
            state['upload_id'] = upload['id']
            save()
        if upload and not upload['job_id']:
            url = '/api/v1/asr/video-uploads/' + upload['id']
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
        elif upload:
            job = call('GET', '/api/v1/jobs/' + upload['job_id'])
        state['job_id'] = job['id']
        save()
        print('Job:', job['id'], flush=True)
        if args.pdf:
            while job['state'] not in {'succeeded', 'failed', 'cancelled'}:
                time.sleep(job.get('poll_after_seconds') or 2)
                job = call('GET', '/api/v1/jobs/' + job['id'])
            if job['state'] != 'succeeded':
                raise RuntimeError(job.get('error_message') or job['state'])
            with client.stream('POST', f"/api/v1/jobs/{job['id']}/transcript-export", json={'format': 'pdf', 'redact': not getattr(args, 'no_redact', False), 'replacements': json.loads(args.replacements.read_text(encoding='utf-8')) if getattr(args, 'replacements', None) and not getattr(args, 'no_redact', False) else []}) as response:
                response.raise_for_status()
                args.pdf.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode='wb', dir=args.pdf.parent, prefix=args.pdf.name + '.', suffix='.partial', delete=False) as output:
                        temporary = Path(output.name)
                        for block in response.iter_bytes():
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, args.pdf)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            print('PDF:', args.pdf)
        args.state.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
