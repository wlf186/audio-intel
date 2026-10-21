"""Preview local picture detection for an existing job; --apply explicitly saves it."""
from __future__ import annotations

import argparse
import os
import time
import uuid

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    parser.add_argument('--min-change-percent', type=float)
    parser.add_argument('--min-stable-seconds', type=float, help='Stable screenshot duration, 0.5–5 seconds; e.g. 0.6')
    parser.add_argument('--apply', action='store_true', help='Replace current section/image edits after preview succeeds')
    args = parser.parse_args()
    if not args.job_id.isalnum():
        parser.error('job_id must be an alphanumeric task ID')
    settings = {name: getattr(args, name) for name in ('min_change_percent', 'min_stable_seconds') if getattr(args, name) is not None}
    key = os.getenv('AUDIO_INTEL_API_KEY', '')
    with httpx.Client(base_url=os.getenv('AUDIO_INTEL_BASE_URL', 'http://127.0.0.1:20810'),
                      headers={'Authorization': f'Bearer {key}'} if key else {}, timeout=60) as client:
        def call(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json() if response.status_code != 204 else None
        root = '/api/v1/jobs/' + args.job_id
        revision = call('GET', root)['result']['video']['revision']
        item = call('POST', root + '/video-scene-previews', json={'revision': revision, 'settings': settings},
                    headers={'Idempotency-Key': uuid.uuid4().hex})
        url = root + '/video-scene-previews/' + item['id']
        print('Preview:', item['id'], flush=True)
        try:
            while item['state'] == 'running':
                time.sleep(.5)
                item = call('GET', url)
            if item['state'] != 'ready':
                raise RuntimeError(item.get('error') or item['state'])
            print('Sections:', len(item['scenes']))
            print('Without stable pictures:', sum(scene['image'] is None for scene in item['scenes']))
            if args.apply:
                saved = call('PATCH', root + '/video-scenes',
                             json={'revision': revision, 'action': 'apply_detection', 'preview_id': item['id']})
                print('Applied revision:', saved['video']['revision'])
        finally:
            # The CLI prints only metadata and does not retain private images.
            client.delete(url)


if __name__ == '__main__':
    main()
