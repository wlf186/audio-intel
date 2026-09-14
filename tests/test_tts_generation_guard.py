from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest

from tts import generation_guard as guard


@pytest.mark.parametrize(('tokens', 'expected'), [(1,750),(55,750),(69,932),(203,2741),(1000,8192)])
def test_budget_is_conservative_and_bounded(tokens, expected):
    assert guard.generation_budget(tokens) == expected


def test_cleanup_requires_directory_evidence_and_preserves_urls():
    text = '章节一 ........... 12\n章节二 ............ 15\n网址 https://example.com/............ \n普通省略号......'
    clean = guard.recovery_text(text)
    assert clean == '章节一   12\n章节二   15\n网址 https://example.com/............ \n普通省略号......'
    for plain in ['只有一行 .......... 12', '这是正文……\n1.23', 'https://example.com/.........12\nhttps://example.com/.........13']:
        assert guard.recovery_text(plain) == plain


def test_tokenizer_fallback_is_identified():
    assert guard.token_counts(object(), ['abc', '你好']) == ([3, 2], 'characters')


@pytest.fixture
def runtime(tmp_path):
    # Also run this module in the pinned TTS runtime; the API has no torch dependency.
    torch = pytest.importorskip('torch')
    pytest.importorskip('transformers')
    decoded, calls, progress = [], [], []
    pending = []
    def generate(**kwargs):
        entry = pending.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        return SimpleNamespace(sequences=torch.tensor(entry))
    def decode(items):
        decoded.extend(items)
        return [np.array([float(item['index']+1)], dtype=np.float32) for item in items], 24000
    model = SimpleNamespace(model=SimpleNamespace(device=torch.device('cpu'),
        talker=SimpleNamespace(generate=generate),
        speech_tokenizer=SimpleNamespace(decode=decode,get_output_sample_rate=lambda:24000),
        config=SimpleNamespace(talker_config=SimpleNamespace(codec_eos_token_id=9))),generate_defaults={'repetition_penalty':1.05})
    request = {'model':'qwen3-tts-0.6b','voice_mode':'preset','speaker':'Vivian'}
    context = SimpleNamespace(output_dir=tmp_path/'output',job={'attempts':1,'request':request},
        progress=lambda *a,**k:progress.append((a,k)))
    def invoke(model, request, texts, prompt, callback, metadata):
        calls.append((texts,prompt,metadata))
        torch.rand(1)
        model.model.talker.generate(inputs_embeds=torch.zeros(len(texts),1,1))
        if callback:
            callback(1)
        return model.model.speech_tokenizer.decode([{'index':i} for i in range(len(texts))])
    return SimpleNamespace(torch=torch,model=model,context=context,request=request,pending=pending,
                           calls=calls,decoded=decoded,progress=progress,invoke=invoke)


def test_padding_eos_is_not_natural_and_last_step_eos_is_accepted(runtime, monkeypatch):
    r=runtime
    attempt=guard.GenerationAttempt(r.model,['bad','good'])
    attempt.limits=[3,5]
    r.pending.append([[1,2,3,9,9],[1,2,3,4,9]])
    original_generate=r.model.model.talker.generate
    original_decode=r.model.model.speech_tokenizer.decode
    with attempt.observe():
        audio,_=r.invoke(r.model,r.request,['bad','good'],None,None,None)
    assert attempt.validate(audio)==[False,True]
    assert r.decoded==[{'index':1}]
    assert audio[0].size==0
    assert attempt.rows[1]['generated_steps']==5
    assert r.model.model.talker.generate is original_generate
    assert r.model.model.speech_tokenizer.decode is original_decode


def test_recovery_only_retries_bad_row_and_restores_rng(runtime):
    r=runtime
    r.pending.extend([[[1,2,3],[1,9,9]],[[1,9]]])
    r.torch.manual_seed(17)
    r.torch.rand(1)
    expected=r.torch.get_rng_state()
    r.torch.manual_seed(17)
    controller=guard.GenerationGuard(r.context,r.model)
    with controller.activate():
        audio,rate=controller.generate(r.invoke,r.request,['bad','good'],['prompt-a','prompt-b'],None,
                                      [{'speaker':'Vivian'},{'speaker':'Dylan'}])
    assert len(r.calls)==2 and r.calls[1]==(['bad'],['prompt-a'],[{'speaker':'Vivian'}])
    assert audio[1].tolist()==[2.0] and rate==24000
    assert r.torch.equal(r.torch.get_rng_state(),expected)
    assert controller.summary()=={'version':1,'checked_chunks':2,'retried_chunks':1,'retry_attempts':1,'recovered_chunks':1}
    assert list(controller.directory.glob('failure-*.json'))
    assert guard.current_guard() is None


def test_three_retries_are_not_reset_by_resource_recycle(runtime):
    r=runtime
    r.pending.extend([[[1,2]],[[1,2]],RuntimeError('executor resource failure')])
    controller=guard.GenerationGuard(r.context,r.model)
    with controller.activate(),pytest.raises(RuntimeError,match='executor resource failure'):
        controller.generate(r.invoke,r.request,['bad'],None,None,None)
    assert controller.summary()['retry_attempts']==2
    r.pending.extend([[[1,2]],[[1,2]]])
    restored=guard.GenerationGuard(r.context,r.model)
    with restored.activate(),pytest.raises(guard.TtsGenerationGuardError,match='3 retries'):
        restored.generate(r.invoke,r.request,['bad'],None,None,None)
    assert restored.summary()['retry_attempts']==3
    assert len(r.calls)==5  # Two original passes and exactly three recovery calls.
    assert r.decoded==[]
    assert guard.current_guard() is None


def test_all_failed_rows_never_decode_and_stop_after_three_retries(runtime):
    r=runtime
    r.pending.extend([[[1,2]]]*4)
    controller=guard.GenerationGuard(r.context,r.model)
    with controller.activate(),pytest.raises(guard.TtsGenerationGuardError):
        controller.generate(r.invoke,r.request,['bad'],None,None,None)
    assert len(r.calls)==4 and r.decoded==[]
    assert controller.summary()['recovered_chunks']==0


def test_oom_is_propagated_and_wrappers_are_restored(runtime):
    r=runtime
    r.pending.append(r.torch.OutOfMemoryError('memory'))
    original=r.model.model.talker.generate
    controller=guard.GenerationGuard(r.context,r.model)
    with controller.activate(),pytest.raises(r.torch.OutOfMemoryError):
        controller.generate(r.invoke,r.request,['bad'],None,None,None)
    assert len(r.calls)==1 and r.model.model.talker.generate is original
    assert controller.summary()['retry_attempts']==0


def test_cancel_prevents_retry_inference(runtime):
    from audio_intel.worker import JobCancelled
    r=runtime
    r.pending.append([[1,2]])
    def cancel(*a,**k):
        raise JobCancelled('cancelled')
    r.context.progress=cancel
    with guard.GenerationGuard(r.context,r.model).activate() as controller,pytest.raises(JobCancelled):
        controller.generate(r.invoke,r.request,['bad'],None,None,None)
    assert len(r.calls)==1


def test_nonfinite_and_empty_waveforms_are_rejected(runtime):
    attempt=guard.GenerationAttempt(runtime.model,['a','b','c'])
    attempt.rows=[{'natural_eos':True} for _ in range(3)]
    assert attempt.validate([np.array([np.nan]),np.array([]),np.array([1.0])])==[False,False,True]


def test_corrupt_ledger_fails_closed(runtime):
    controller=guard.GenerationGuard(runtime.context,runtime.model)
    controller.path.parent.mkdir(parents=True)
    controller.path.write_text('{broken')
    with pytest.raises(ValueError):
        guard.GenerationGuard(runtime.context,runtime.model)


def test_new_job_attempt_has_a_fresh_budget(runtime):
    r=runtime
    r.pending.extend([[[1,2]]]*4)
    with guard.GenerationGuard(r.context,r.model).activate() as first,pytest.raises(guard.TtsGenerationGuardError):
        first.generate(r.invoke,r.request,['bad'],None,None,None)
    r.context.job['attempts']=2
    assert guard.GenerationGuard(r.context,r.model).summary()['retry_attempts']==0


def test_progress_is_monotonic_during_recovery(runtime):
    r=runtime
    with guard.GenerationGuard(r.context,r.model).activate():
        r.context.progress(.6,'synthesis')
        r.context.progress(.3,'tts_chunk_retry')
    assert [row[0][0] for row in r.progress]==[.6,.6]


def test_job_guard_does_not_retain_model_during_pipeline_cleanup(tmp_path):
    context=SimpleNamespace(job={'attempts':1,'request':{}},output_dir=tmp_path/'output',progress=lambda *a,**k:None)
    model=object()
    with guard.job_guard(context,model) as controller:
        assert controller.model is model
    assert controller.model is None
    assert guard.current_guard() is None


def test_journal_partials_are_cleaned_without_resetting_committed_counts(tmp_path):
    from audio_intel.document_store import clean_partials
    root=tmp_path/'jobs';directory=root/'job'/'diagnostics'/'tts-generation'/'1'
    (directory/'chunks').mkdir(parents=True)
    saved=directory/'chunks'/'committed.json';saved.write_text('{"retries":3}')
    for path in [directory/'ledger.partial',directory/'failure-test.partial',directory/'chunks'/'chunk.partial']:
        path.write_text('incomplete')
    clean_partials('job',root)
    assert not list(directory.rglob('*.partial'))
    assert saved.read_text()=='{"retries":3}'
