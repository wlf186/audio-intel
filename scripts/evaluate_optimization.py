#!/usr/bin/env python3
"""Run the fixed synthetic optimization corpus, without creating application jobs.

Outputs contain model responses and an unfilled generation-review worksheet.
Only fixture-candidate review accuracy is scored automatically. Language quality
and fact preservation require an independent reading of the recorded outputs.

Fill generation_assessment only in a separate annotated report. Each assessment
records reviewer_type (human or independent_ai), facts_preserved, readable,
coherent, meaningful_improvement (booleans; last may be null when inapplicable),
and notes. These observations are not a quality certification.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / 'tests/fixtures/optimization_quality.json'


def load_corpus(path: Path = CORPUS) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'))
    kind = value.get('kind', 'baseline')
    if value.get('version') != 1 or kind not in ('baseline', 'long_challenge'):
        raise ValueError('Unknown corpus version or kind')
    cases = value.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('Corpus requires a nonempty case list')
    if kind == 'baseline' and len(cases) < 60:
        raise ValueError('Baseline corpus requires at least 60 cases')
    if kind == 'long_challenge' and (len(cases) != 12 or value.get('expected_case_count') != 12
                                    or value.get('source_length_range') != [180, 400]
                                    or value.get('review_label_counts') != {'pass': 6, 'fact': 6}):
        raise ValueError('Long challenge requires explicit 12-case, 180–400-character and balanced-label metadata')
    identifiers = [case.get('id') for case in cases]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers) or len(set(identifiers)) != len(cases):
        raise ValueError('Case identifiers must be nonempty and unique')
    for case in cases:
        splits = ('challenge',) if kind == 'long_challenge' else ('development', 'holdout')
        expected = case.get('expected_review')
        if case.get('split') not in splits or expected not in ('pass', 'fact'):
            raise ValueError('Unknown evaluation label')
        if case.get('category') not in ('merge', 'acceptable', 'changed_fact') or (case['category'] == 'changed_fact') != (expected == 'fact'):
            raise ValueError('Category and review label disagree')
        facts, segments, candidate = case.get('required_facts'), case.get('source_segments'), case.get('review_candidate')
        if (not isinstance(facts, list) or not facts or any(not isinstance(fact, str) or not fact.strip() for fact in facts)
                or not isinstance(segments, list) or not segments or not isinstance(candidate, str) or not candidate.strip()
                or not isinstance(case.get('requires_rewrite'), bool)
                or not isinstance(case.get('hotwords'), list) or any(not isinstance(word, str) or not word.strip() for word in case['hotwords'])):
            raise ValueError('Every case needs sources, candidate and fact annotations')
        previous_end, speaker, segment_ids = None, None, set()
        for segment in segments:
            if (not isinstance(segment.get('id'), int) or isinstance(segment['id'], bool) or segment['id'] in segment_ids
                    or not isinstance(segment.get('text'), str) or not segment['text'].strip()
                    or not isinstance(segment.get('speaker'), str) or not segment['speaker']):
                raise ValueError('Sources require unique IDs, text and speaker')
            start, end = segment.get('start'), segment.get('end')
            if (any(not isinstance(number, (int, float)) or isinstance(number, bool) or not math.isfinite(number) for number in (start, end))
                    or start < 0 or end <= start or (previous_end is not None and not 0 <= start - previous_end <= 15)
                    or (speaker is not None and speaker != segment['speaker'])):
                raise ValueError('Fixture sources must form one ordered same-speaker reading group')
            previous_end, speaker = end, segment['speaker']
            segment_ids.add(segment['id'])
        if kind == 'long_challenge':
            source = ''.join(segment['text'] for segment in segments)
            if not 180 <= len(source) <= 400 or len(segments) < 3 or not case['requires_rewrite']:
                raise ValueError('Long challenge needs 180–400 characters, multiple sources and genuine editing eligibility')
            annotation = case.get('review_annotation', {})
            if annotation.get('kind') != ('equivalent' if expected == 'pass' else 'factual_change') or not annotation.get('reason'):
                raise ValueError('Long challenge requires explicit review rationale consistent with its label')
            if expected == 'fact' and (not annotation.get('source_quote') or annotation['source_quote'] not in source
                                       or not annotation.get('candidate_quote') or annotation['candidate_quote'] not in candidate):
                raise ValueError('Long challenge fact annotations must quote the source and candidate')
    if kind == 'long_challenge' and Counter(case['expected_review'] for case in cases) != {'pass': 6, 'fact': 6}:
        raise ValueError('Long challenge review labels must remain six pass and six fact')
    return value


async def generate_prepared(engine, config: dict, group: dict, hotwords: list[str]) -> dict:
    """Use the service's partition/generation/seam/assembly path without its DB."""
    chunks = []
    for part in engine.prepare_parts(config, group, hotwords):
        try:
            output = await engine.generate(config, [{**part, 'ordinal': 0}], hotwords, '', '', None)
            text, method = output[0][1], getattr(output, 'method', 'unknown')
        except (engine.InvalidOutput, engine.ReviewFailed, json.JSONDecodeError):
            text, method = engine.basic_cleanup(part['original_text']), 'basic_cleanup'
        chunks.append({'source': part, 'text': text, 'generation_method': method})
    for index in range(len(chunks) - 1):
        if chunks[index]['source']['forced_after']:
            chunks[index]['text'], chunks[index + 1]['text'] = await engine.repair_seam(config, chunks[index], chunks[index + 1], hotwords)
    methods = {chunk['generation_method'] for chunk in chunks}
    return {'text': '\n\n'.join(chunk['text'] for chunk in chunks if chunk['text']),
            'generation_method': next(iter(methods)) if len(methods) == 1 else 'mixed',
            'generation_parts': chunks, 'generation_assessment': None}


def summarize(report: dict, *, breakdown: bool = True) -> dict:
    review_rows = [row for row in report['results'] if row['operation'] == 'review']
    positive = [row for row in review_rows if row['expected_review'] == 'pass']
    negative = [row for row in review_rows if row['expected_review'] == 'fact']
    # Uncertain/unavailable responses stay in denominators: abstaining is not a pass.
    counts = Counter(row.get('outcome', 'error') for row in review_rows)
    generation = [row for row in report['results'] if row['operation'] == 'generation']
    assessments = [(row, row.get('generation_assessment')) for row in generation]
    assessed = [(row, value) for row, value in assessments if isinstance(value, dict)
                and value.get('reviewer_type') in ('human', 'independent_ai')
                and all(isinstance(value.get(key), bool) for key in ('facts_preserved', 'readable', 'coherent'))]
    # Older reports do not carry requires_rewrite; their fixed merge category
    # has the same eligible set. Clear sources need not be changed to count well.
    eligible = [row for row in generation if row.get('requires_rewrite', row.get('category') == 'merge')]
    eligible_assessed = [(row, value) for row, value in assessed if row in eligible]
    criteria = {}
    for criterion in ('facts_preserved', 'readable', 'coherent', 'meaningful_improvement'):
        population = eligible if criterion == 'meaningful_improvement' else generation
        observations = eligible_assessed if criterion == 'meaningful_improvement' else assessed
        yes = sum(value.get(criterion) is True for _, value in observations)
        no = sum(value.get(criterion) is False for _, value in observations)
        criteria[criterion] = {'yes': yes, 'no': no, 'unassessed': len(population) - yes - no,
                               'observed_success_rate': yes / len(population) if population else None}
    eligible_characters = sum(row['source_characters'] for row in eligible)
    improved_characters = sum(row['source_characters'] for row, value in eligible_assessed if value.get('meaningful_improvement') is True)
    summary = {
        'review_cases': len(review_rows), 'review_outcomes': dict(counts),
        'false_positive_rate': sum(row.get('outcome') == 'fact' for row in positive) / len(positive) if positive else None,
        'positive_pass_rate': sum(row.get('outcome') == 'pass' for row in positive) / len(positive) if positive else None,
        'fact_recall': sum(row.get('outcome') == 'fact' for row in negative) / len(negative) if negative else None,
        'review_coverage': sum(row.get('outcome') in ('pass', 'fact') for row in review_rows) / len(review_rows) if review_rows else None,
        'generation_cases': len(generation),
        'basic_cleanup_cases': sum(row.get('generation_method') == 'basic_cleanup' for row in generation),
        'mixed_cleanup_cases': sum(row.get('generation_method') == 'mixed' for row in generation),
        'generation_part_count': sum(len(row.get('generation_parts', [])) for row in generation),
        'basic_cleanup_part_count': sum(part.get('generation_method') == 'basic_cleanup' for row in generation for part in row.get('generation_parts', [])),
        'generation_source_characters': sum(row['source_characters'] for row in generation),
        'assessed_generation_cases': len(assessed),
        'generation_assessment_coverage': len(assessed) / len(generation) if generation else None,
        'generation_assessment_reviewers': dict(Counter(value['reviewer_type'] for _, value in assessed)),
        'generation_observations': criteria,
        'eligible_for_improvement_source_characters': eligible_characters,
        'observed_improved_source_characters': improved_characters,
        'observed_improved_source_character_rate': improved_characters / eligible_characters if eligible_characters else None,
        'generation_quality_certified': False,
    }
    if breakdown:
        for field in ('repeat', 'split'):
            values = sorted({row[field] for row in report['results'] if field in row}, key=str)
            summary['by_' + field] = {str(value): summarize({'results': [row for row in report['results'] if row.get(field) == value]}, breakdown=False) for value in values}
    return summary


def save_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    report['summary'] = summarize(report)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def evaluate(arguments) -> None:
    # Never load the deployment's .env or initialize its SQLite database.
    os.environ['AUDIO_INTEL_LOAD_ENV'] = '0'
    sys.path.insert(0, str(ROOT))
    from audio_intel import optimization_engine as engine
    from audio_intel.optimization_sources import source_groups
    import psutil

    corpus = load_corpus(arguments.corpus)
    cases = [case for case in corpus['cases'] if arguments.split == 'all' or case['split'] == arguments.split]
    if not cases:
        raise ValueError('The selected split has no cases')
    config = {'base_url': engine.normalize_url(arguments.base_url), 'model': arguments.model,
              'use_proxy': arguments.use_proxy, 'context_length': arguments.context,
              'rewrite_mode': arguments.rewrite_mode, 'prompt_version': engine.PROMPT_VERSION}
    identity = {'corpus_sha256': hashlib.sha256(arguments.corpus.read_bytes()).hexdigest(),
                'model': arguments.model, 'rewrite_mode': arguments.rewrite_mode,
                'context_length': arguments.context, 'split': arguments.split,
                'operation': arguments.operation, 'repeats': arguments.repeats,
                'corpus_kind': corpus.get('kind', 'baseline'),
                'generation_pipeline': 'prepared_parts_forced_seams_assembly_v1',
                'review_pipeline': 'whole_fixed_source_and_candidate_v1',
                'policy_sha256': hashlib.sha256((ROOT/'audio_intel/optimization_policy.py').read_bytes() + (ROOT/'audio_intel/optimization_engine.py').read_bytes()).hexdigest()}
    remote = await engine.discover(config)
    model = next((item for item in remote['models'] if item['name'] == arguments.model), None)
    if not model:
        raise ValueError('Requested model is not installed on the configured service')
    identity['model_digest'] = model['digest']
    if arguments.output.exists():
        if not arguments.resume:
            raise ValueError('Output exists; choose a new path or explicitly --resume')
        report = json.loads(arguments.output.read_text(encoding='utf-8'))
        if report['identity'] != identity:
            raise ValueError('Resume identity differs; use a new output path')
    else:
        report = {'identity': identity, 'service_version': remote.get('version'),
                  'created_at': datetime.now(timezone.utc).isoformat(),
                  'client_platform': platform.platform(), 'results': []}
    operations = ('generation', 'review') if arguments.operation == 'both' else (arguments.operation,)
    completed = {(row['case_id'], row['repeat'], row['operation']) for row in report['results']}
    for repeat in range(1, arguments.repeats + 1):
        for case in cases:
            for operation in operations:
                if (case['id'], repeat, operation) in completed:
                    continue
                if psutil.virtual_memory().available < 4 * 1024**3:
                    save_report(arguments.output, report)
                    raise RuntimeError('Evaluation stopped below 4 GiB available memory; resume after recovery')
                groups = source_groups({'segments': case['source_segments']})
                if len(groups) != 1:
                    raise ValueError('Fixture must remain one complete source group')
                group = groups[0]
                source = group['original_text']
                row = {'case_id': case['id'], 'category': case['category'], 'split': case['split'],
                       'repeat': repeat, 'operation': operation, 'source': source,
                       'source_characters': len(source), 'required_facts': case['required_facts'],
                       'requires_rewrite': case['requires_rewrite'],
                       'expected_review': case['expected_review']}
                started = time.monotonic()
                try:
                    if operation == 'generation':
                        row.update(await asyncio.wait_for(generate_prepared(engine, config, group, case['hotwords']), arguments.timeout))
                    else:
                        output = await asyncio.wait_for(engine.review(config, source, case['review_candidate'], case['hotwords']), arguments.timeout)
                        row.update(candidate=case['review_candidate'], review_scope='whole_fixture_source_and_candidate', **output)
                except Exception as exc:
                    row.update(outcome='error', error_type=type(exc).__name__, error=str(exc)[:500])
                row['seconds'] = round(time.monotonic() - started, 3)
                report['results'].append(row)
                save_report(arguments.output, report)
                print(json.dumps({key: row[key] for key in ('case_id', 'repeat', 'operation', 'seconds')}, ensure_ascii=False), flush=True)
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=CORPUS)
    parser.add_argument('--validate-corpus', action='store_true')
    parser.add_argument('--summarize', type=Path)
    parser.add_argument('--base-url')
    parser.add_argument('--model', choices=('qwen3.5:4b', 'qwen3.5:9b'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--context', type=int, default=32768)
    parser.add_argument('--rewrite-mode', choices=('polish', 'rewrite', 'editorial'), default='editorial')
    parser.add_argument('--split', choices=('development', 'holdout', 'challenge', 'all'), default='all')
    parser.add_argument('--operation', choices=('generation', 'review', 'both'), default='both')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('--use-proxy', action='store_true')
    parser.add_argument('--resume', action='store_true')
    arguments = parser.parse_args()
    if arguments.validate_corpus:
        corpus = load_corpus(arguments.corpus)
        print(json.dumps({'cases': len(corpus['cases']), 'splits': dict(Counter(case['split'] for case in corpus['cases']))}))
        return
    if arguments.summarize:
        print(json.dumps(summarize(json.loads(arguments.summarize.read_text(encoding='utf-8'))), ensure_ascii=False, indent=2))
        return
    if not arguments.base_url or not arguments.model or not arguments.output:
        parser.error('--base-url, --model and --output are required for live evaluation')
    if arguments.repeats < 1 or arguments.timeout <= 0 or arguments.context < 16384:
        parser.error('Use positive repeats/timeout and at least 16384 context')
    asyncio.run(evaluate(arguments))


if __name__ == '__main__':
    main()
