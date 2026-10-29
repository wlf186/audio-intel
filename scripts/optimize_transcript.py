#!/usr/bin/env python3
"""Create and poll a transcript reading draft (configure Ollama in System first).

Completed results can still be undergoing independent background review. The
default command returns once the reading draft is ready; --wait-review also waits
for review to reach a terminal or paused state. Original source spans are retained.
"""
import argparse
import json
import os
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--job', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--regenerate', action='store_true', help='Explicitly replace the candidate; retain the published draft until success')
    mode.add_argument('--resume', action='store_true', help='Resume a candidate using its saved policy and checkpoints')
    parser.add_argument('--rewrite-mode', choices=['polish', 'rewrite', 'editorial'], help='Editing style; defaults to rewrite for new runs')
    parser.add_argument('--phase', choices=['generation', 'review'], default='generation', help='Phase to resume (default generation)')
    parser.add_argument('--wait-review', action='store_true', help='Also wait for background review; paused/unavailable review does not invalidate the draft')
    args = parser.parse_args()
    if args.resume and args.rewrite_mode:
        parser.error('--resume uses the saved mode; use --regenerate to change it')
    if args.phase != 'generation' and not args.resume:
        parser.error('--phase review requires --resume')
    root = args.url.rstrip('/') + '/api/v1/jobs/' + args.job + '/transcript-optimization'
    headers = {'Content-Type': 'application/json', 'Idempotency-Key': str(uuid.uuid4())}
    if os.environ.get('AUDIO_INTEL_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['AUDIO_INTEL_API_KEY']
    def call(path='', body=None):
        request = urllib.request.Request(root + path, headers=headers, data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    if args.resume:
        status = call()
        candidate = status['published'] if args.phase == 'review' else status['candidate']
        if not candidate or candidate.get('requires_regeneration'):
            raise SystemExit('No resumable candidate; use --regenerate for an obsolete policy')
        status = call('/resume', {'run_id': candidate['id'], 'phase': args.phase})
    else:
        status = call(body={'regenerate': args.regenerate, **({'rewrite_mode': args.rewrite_mode} if args.rewrite_mode else {})})
    target = (status['published'] if args.resume and args.phase == 'review' else status['candidate'] or status['published'])['id']
    while True:
        status = call()
        if status['published'] and status['published']['id'] == target:
            if args.wait_review and status['published'].get('review_state') in ('queued', 'running'):
                time.sleep(2)
                continue
            print(json.dumps(call('/result?run_id='+status['published']['id']), ensure_ascii=False, indent=2))
            return
        candidate = status['candidate']
        if not candidate or candidate['state'] in ('failed', 'paused', 'deleting'):
            raise SystemExit(json.dumps(status, ensure_ascii=False))
        time.sleep(2)


if __name__ == '__main__':
    main()
