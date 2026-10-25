#!/usr/bin/env python3
"""Create and poll a transcript reading draft (configure Ollama in System first)."""
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
    args = parser.parse_args()
    root = args.url.rstrip('/') + '/api/v1/jobs/' + args.job + '/transcript-optimization'
    headers = {'Content-Type': 'application/json', 'Idempotency-Key': str(uuid.uuid4())}
    if os.environ.get('AUDIO_INTEL_API_KEY'):
        headers['Authorization'] = 'Bearer ' + os.environ['AUDIO_INTEL_API_KEY']
    def call(path='', body=None):
        request = urllib.request.Request(root + path, headers=headers, data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    call(body={'regenerate': False})
    while True:
        status = call()
        if status['published']:
            print(json.dumps(call('/result?run_id='+status['published']['id']), ensure_ascii=False, indent=2))
            return
        candidate = status['candidate']
        if not candidate or candidate['state'] in ('failed', 'paused', 'deleting'):
            raise SystemExit(json.dumps(status, ensure_ascii=False))
        time.sleep(2)


if __name__ == '__main__':
    main()
