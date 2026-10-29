"""The evaluation must not pass by omitting unavailable or unknown reviews."""
import importlib.util
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


path = Path(__file__).resolve().parents[1] / 'scripts/evaluate_optimization.py'
spec = importlib.util.spec_from_file_location('evaluate_optimization', path)
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_fixed_quality_corpus_has_independent_holdout_and_fact_annotations():
    corpus = evaluation.load_corpus()
    cases = corpus['cases']
    assert len(cases) == 60
    assert sum(case['split'] == 'holdout' for case in cases) == 24
    assert {case['category'] for case in cases} == {'merge', 'acceptable', 'changed_fact'}
    assert all(case['required_facts'] for case in cases)
    assert next(case for case in cases if case['id'] == 'merge_mike_atlas')['hotwords'] == ['Atlas']


def test_unavailable_and_uncertain_do_not_inflate_review_accuracy():
    report = {'results': [
        dict(operation='review', expected_review='pass', outcome='fact'),
        dict(operation='review', expected_review='pass', outcome='uncertain'),
        dict(operation='review', expected_review='fact', outcome='fact'),
        dict(operation='review', expected_review='fact', outcome='unavailable'),
    ]}
    summary = evaluation.summarize(report)
    assert summary['false_positive_rate'] == .5
    assert summary['positive_pass_rate'] == 0
    assert summary['fact_recall'] == .5
    assert summary['review_coverage'] == .5
    assert summary['generation_quality_certified'] is False


def test_saved_generation_report_is_utf8_and_does_not_invent_assessment(tmp_path):
    report = {'results': [dict(operation='generation', source_characters=100, text='整理后的文字。', generation_method='basic_cleanup', generation_assessment=None)]}
    target = tmp_path / '评测.json'
    evaluation.save_report(target, report)
    assert '整理后的文字' in target.read_text(encoding='utf-8')
    assert report['summary']['basic_cleanup_cases'] == 1
    assert report['summary']['assessed_generation_cases'] == 0
    assert not list(tmp_path.glob('*.tmp'))


def test_generation_observations_keep_unassessed_cases_and_identify_ai_review():
    assessment = dict(reviewer_type='independent_ai', facts_preserved=True,
                      readable=True, coherent=True, meaningful_improvement=True, notes='Compared against all source facts')
    summary = evaluation.summarize({'results': [
        dict(operation='generation', category='merge', source_characters=20, generation_assessment=assessment),
        dict(operation='generation', category='merge', source_characters=80, generation_assessment=None),
        dict(operation='generation', category='acceptable', source_characters=10, generation_assessment={**assessment, 'meaningful_improvement': None}),
    ]})
    assert summary['generation_assessment_reviewers'] == {'independent_ai': 2}
    assert summary['generation_assessment_coverage'] == 2 / 3
    assert summary['generation_observations']['facts_preserved']['unassessed'] == 1
    assert summary['generation_observations']['meaningful_improvement'] == dict(yes=1, no=0, unassessed=1, observed_success_rate=.5)
    assert summary['observed_improved_source_character_rate'] == .2
    assert summary['generation_quality_certified'] is False


def test_long_challenge_is_explicit_balanced_and_keeps_full_sources():
    corpus = evaluation.load_corpus(evaluation.CORPUS.with_name('optimization_quality_long.json'))
    assert len(corpus['cases']) == 12
    assert sum(case['expected_review'] == 'fact' for case in corpus['cases']) == 6
    assert all(180 <= sum(len(span['text']) for span in case['source_segments']) <= 400 for case in corpus['cases'])
    assert all(len(case['source_segments']) >= 3 and case['split'] == 'challenge' for case in corpus['cases'])


@pytest.mark.parametrize('mutation,match', [
    (lambda corpus: corpus.pop('kind'), 'at least 60'),
    (lambda corpus: corpus.update(review_label_counts={'pass': 12, 'fact': 0}), 'balanced-label'),
    (lambda corpus: corpus['cases'][0].update(split='holdout'), 'evaluation label'),
    (lambda corpus: corpus['cases'][0].update(expected_review='fact'), 'disagree'),
    (lambda corpus: corpus['cases'][6]['review_annotation'].update(source_quote='这句话并不存在'), 'must quote'),
    (lambda corpus: corpus['cases'][0]['source_segments'][1].update(speaker='Another_speaker'), 'same-speaker'),
    (lambda corpus: corpus['cases'][0].update(required_facts=[]), 'fact annotations'),
])
def test_invalid_small_corpora_cannot_bypass_baseline_or_annotation_contract(tmp_path, mutation, match):
    corpus = deepcopy(evaluation.load_corpus(evaluation.CORPUS.with_name('optimization_quality_long.json')))
    mutation(corpus)
    candidate = tmp_path / 'invalid.json'
    candidate.write_text(json.dumps(corpus, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match=match):
        evaluation.load_corpus(candidate)


def test_generation_evaluation_uses_parts_and_only_forced_seams():
    calls = []
    parts = [dict(original_text=text, forced_after=forced) for text, forced in [('甲半句', True), ('乙完整句。', False), ('丙完整句。', False)]]
    class InvalidOutput(Exception):
        pass
    class Output(list):
        method = 'model'
    async def generate(config, batch, hotwords, before, after, activity):
        source = batch[0]['original_text']
        calls.append(('generate', source, before, after))
        if source.startswith('乙'):
            raise InvalidOutput('Provider returned malformed text')
        return Output([(0, source + '改')])
    async def seam(config, left, right, hotwords):
        calls.append(('seam', left['source']['original_text'], right['source']['original_text']))
        return left['text'] + right['text'], ''
    engine = SimpleNamespace(prepare_parts=lambda *args: parts, generate=generate,
                             repair_seam=seam, basic_cleanup=lambda text: text + '清理',
                             InvalidOutput=InvalidOutput, ReviewFailed=type('ReviewFailed', (Exception,), {}))
    result = asyncio.run(evaluation.generate_prepared(engine, {}, {'original_text': '完整原文'}, []))
    assert [call[0] for call in calls] == ['generate', 'generate', 'generate', 'seam']
    assert all(call[2:] == ('', '') for call in calls if call[0] == 'generate')
    assert result['text'] == '甲半句改乙完整句。清理\n\n丙完整句。改'
    assert result['generation_method'] == 'mixed'
    assert ''.join(part['source']['original_text'] for part in result['generation_parts']) == '甲半句乙完整句。丙完整句。'
    assert result['generation_assessment'] is None
