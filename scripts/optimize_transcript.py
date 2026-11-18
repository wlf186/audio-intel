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
    mode.add_argument('--usage', action='store_true', help='Read usage for the current candidate or published draft')
    parser.add_argument('--rewrite-mode', choices=['polish', 'rewrite', 'editorial'], help='Editing style; defaults to rewrite for new runs')
    parser.add_argument('--phase', choices=['workflow', 'generation', 'review'], default='workflow', help='Resume the workflow; generation/review are legacy aliases')
    parser.add_argument('--analyze-topics', action='store_true', help='Discover recording topics before publishing the draft')
    parser.add_argument('--max-llm-calls', type=int, help='Total model attempts for this run. New drafts default to their estimate. On resume you can raise the saved limit.')
    parser.add_argument('--wait-review', action='store_true', help='Compatibility option; all runs now wait for checks before publication')
    args = parser.parse_args()
    if args.max_llm_calls is not None and (args.usage or not 1 <= args.max_llm_calls <= 2147483647):
        parser.error('--max-llm-calls requires 1–2147483647 and applies only to creation or resume')
    if args.resume and (args.rewrite_mode or args.analyze_topics):
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
    if args.usage:
        status = call()
        run = status['candidate'] or status['published']
        if not run:
            raise SystemExit('No optimization run for this transcription')
        print(json.dumps(call('/usage?run_id='+run['id']), ensure_ascii=False, indent=2))
        return
    limit = {'max_llm_calls': args.max_llm_calls} if args.max_llm_calls is not None else {}
    if args.resume:
        status = call()
        candidate = status['candidate']
        if not candidate or candidate.get('requires_regeneration'):
            raise SystemExit('No resumable candidate; use --regenerate for an obsolete policy')
        status = call('/resume', {'run_id': candidate['id'], 'phase': args.phase, **limit})
    else:
        status = call(body={'regenerate': args.regenerate, **limit, **({'analyze_topics': True} if args.analyze_topics else {}), **({'rewrite_mode': args.rewrite_mode} if args.rewrite_mode else {})})
    target = (status['candidate'] or status['published'])['id']
    while True:
        status = call()
        if status['published'] and status['published']['id'] == target:
            result = call('/result?run_id='+status['published']['id'])
            if status['published'].get('topic_state') == 'completed':
                result['topics'] = call('/topics?run_id='+target)['result']
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        candidate = status['candidate']
        if not candidate or candidate['state'] in ('failed', 'paused', 'deleting'):
            raise SystemExit(json.dumps(status, ensure_ascii=False))
        time.sleep(2)


if __name__ == '__main__':
    main()
