#!/usr/bin/env python3
"""Create and poll a transcript reading draft (configure Ollama in System first).

Generation and bounded checks complete before publication. Resume continues the
whole candidate workflow. Original source spans are retained.
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
    parser.add_argument('--phase', choices=['workflow', 'generation', 'review'], default='workflow', help='Resume the workflow; generation/review are legacy aliases')
    parser.add_argument('--wait-review', action='store_true', help='Compatibility option; all runs now wait for checks before publication')
    args = parser.parse_args()
    if args.resume and args.rewrite_mode:
        parser.error('--resume uses the saved mode; use --regenerate to change it')
    if args.phase == 'review' and not args.resume:
        parser.error('--phase requires --resume')
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
        candidate = status['candidate']
        if not candidate or candidate.get('requires_regeneration'):
            raise SystemExit('No resumable candidate; use --regenerate for an obsolete policy')
        status = call('/resume', {'run_id': candidate['id'], 'phase': args.phase})
    else:
        status = call(body={'regenerate': args.regenerate, **({'rewrite_mode': args.rewrite_mode} if args.rewrite_mode else {})})
    target = (status['candidate'] or status['published'])['id']
    while True:
        status = call()
        if status['published'] and status['published']['id'] == target:
            print(json.dumps(call('/result?run_id='+status['published']['id']), ensure_ascii=False, indent=2))
            return
        candidate = status['candidate']
        if not candidate or candidate['state'] in ('failed', 'paused', 'deleting'):
            raise SystemExit(json.dumps(status, ensure_ascii=False))
        time.sleep(2)


if __name__ == '__main__':
    main()
