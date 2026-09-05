from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
import sys
import wave

import numpy as np
import pytest

from audio_intel.config import settings
from audio_intel.performance import cpu_batch_size, gpu_batch_size, lower_batch_size, resolve_acceleration
from audio_intel.progress import ThrottledProgress, progress_snapshot_path, progress_snapshot_paths
from audio_intel.utils import atomic_json, safe_filename, timecode, waveform_peaks
from asr import pipeline as asr_pipeline
from asr import stage as asr_stage
from tts import pipeline as tts_pipeline
from tts.pipeline import split_text


def test_utilities() -> None:
    assert safe_filename("../访谈 录音?.wav") == "访谈_录音_.wav"
    assert timecode(3723.456) == "01:02:03.456"
    assert waveform_peaks([0, -0.5, 0.2, 1], 2) == [0.5, 1.0]


def test_mock_asr_examples_follow_explicit_capture_language() -> None:
    english_language, english_examples = asr_pipeline._mock_transcription_examples("English")
    chinese_language, chinese_examples = asr_pipeline._mock_transcription_examples("Chinese")

    assert english_language == "English"
    assert english_examples[0].startswith("Welcome")
    assert chinese_language == "Chinese"
    assert chinese_examples[0].startswith("欢迎")


def test_model_progress_is_throttled_and_boundary_can_be_forced(monkeypatch) -> None:
    emitted = []
    clock = iter((1.0, 1.1, 1.7, 1.8))
    monkeypatch.setattr("audio_intel.progress.time.monotonic", lambda: next(clock))
    reporter = ThrottledProgress(emitted.append, interval_seconds=.5)

    assert reporter.report({"current": 1})
    assert not reporter.report({"current": 2})
    assert reporter.report({"current": 2})
    assert reporter.report({"current": 3}, force=True)
    assert [item["current"] for item in emitted] == [1, 2, 3]


def test_asr_progress_uses_immutable_numbered_snapshots(tmp_path, monkeypatch) -> None:
    base_path = tmp_path / "transcription-progress.json"
    destinations = []
    real_atomic_json = atomic_json

    def publish(path, value):
        assert not path.exists()
        destinations.append(path)
        real_atomic_json(path, value)

    monkeypatch.setattr(asr_stage, "atomic_json", publish)
    payload = {"progress_path": str(base_path)}
    asr_stage._progress(payload, "transcription", 0, 2, stage_progress=0.1)
    first_path = progress_snapshot_path(base_path, 1)
    with first_path.open("r", encoding="utf-8") as first_snapshot:
        asr_stage._progress(payload, "transcription", 1, 2, stage_progress=0.5)
        assert '"completed": 0' in first_snapshot.read()

    assert destinations == [
        progress_snapshot_path(base_path, 1),
        progress_snapshot_path(base_path, 2),
    ]
    assert [sequence for sequence, _ in progress_snapshot_paths(base_path)] == [1, 2]


def test_asr_progress_publish_failure_warns_once_and_does_not_abort(tmp_path, monkeypatch, caplog) -> None:
    def fail_publish(_path, _value):
        raise PermissionError("simulated scanner lock")

    monkeypatch.setattr(asr_stage, "atomic_json", fail_publish)
    payload = {"progress_path": str(tmp_path / "transcription-progress.json")}
    asr_stage._progress(payload, "transcription", 0, 1)
    asr_stage._progress(payload, "transcription", 1, 1)

    assert caplog.text.count("Unable to publish ASR progress snapshot") == 1


def test_asr_progress_drain_uses_latest_valid_snapshot_and_cleans_consumed(tmp_path) -> None:
    base_path = tmp_path / "transcription-progress.json"
    atomic_json(progress_snapshot_path(base_path, 1), {
        "stage": "transcription", "completed": 1, "total": 3,
    })
    atomic_json(progress_snapshot_path(base_path, 2), {
        "stage": "transcription", "completed": 2, "total": 3,
    })
    progress_snapshot_path(base_path, 3).write_text("{broken", encoding="utf-8")
    emitted = []

    sequence = asr_pipeline._drain_stage_progress(base_path, emitted.append, 0)

    assert sequence == 2
    assert [item["completed"] for item in emitted] == [2]
    assert progress_snapshot_paths(base_path) == []


def test_asr_progress_drain_retries_transient_read_failure(tmp_path, monkeypatch) -> None:
    base_path = tmp_path / "transcription-progress.json"
    first_path = progress_snapshot_path(base_path, 1)
    second_path = progress_snapshot_path(base_path, 2)
    atomic_json(first_path, {"stage": "transcription", "completed": 1, "total": 2})
    atomic_json(second_path, {"stage": "transcription", "completed": 2, "total": 2})
    real_read_text = type(second_path).read_text

    def read_text(path, *args, **kwargs):
        if path == second_path:
            raise PermissionError("simulated scanner lock")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(second_path), "read_text", read_text)
    emitted = []
    sequence = asr_pipeline._drain_stage_progress(base_path, emitted.append, 0)

    assert sequence == 1
    assert [item["completed"] for item in emitted] == [1]
    assert second_path.is_file()

    monkeypatch.setattr(type(second_path), "read_text", real_read_text)
    sequence = asr_pipeline._drain_stage_progress(base_path, emitted.append, sequence)
    assert sequence == 2
    assert [item["completed"] for item in emitted] == [1, 2]
    assert progress_snapshot_paths(base_path) == []


def test_asr_progress_cleanup_failure_does_not_repeat_callback(tmp_path, monkeypatch) -> None:
    base_path = tmp_path / "transcription-progress.json"
    snapshot_path = progress_snapshot_path(base_path, 1)
    atomic_json(snapshot_path, {"stage": "transcription", "completed": 1, "total": 1})
    real_unlink = type(snapshot_path).unlink

    def unlink(path, *args, **kwargs):
        if path == snapshot_path:
            raise PermissionError("simulated scanner lock")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(snapshot_path), "unlink", unlink)
    emitted = []
    sequence = asr_pipeline._drain_stage_progress(base_path, emitted.append, 0)
    sequence = asr_pipeline._drain_stage_progress(base_path, emitted.append, sequence)

    assert sequence == 1
    assert [item["completed"] for item in emitted] == [1]
    assert snapshot_path.is_file()

    monkeypatch.setattr(type(snapshot_path), "unlink", real_unlink)
    asr_pipeline._drain_stage_progress(base_path, emitted.append, sequence)
    assert progress_snapshot_paths(base_path) == []


def test_asr_run_stage_drains_final_progress_after_fast_exit(tmp_path, monkeypatch) -> None:
    def launch(args, **_kwargs):
        atomic_json(progress_snapshot_path(tmp_path / "transcribe-progress.json", 1), {
            "stage": "transcription", "completed": 1, "total": 1,
        })
        atomic_json(tmp_path / "transcribe-output.json", {"chunks": []})

        class ExitedProcess:
            returncode = 0

            @staticmethod
            def poll():
                return 0

        process = ExitedProcess()
        process.args = args
        return process

    monkeypatch.setattr(asr_pipeline.subprocess, "Popen", launch)
    emitted = []

    result = asr_pipeline.run_stage("transcribe", {}, tmp_path, emitted.append)

    assert result == {"chunks": []}
    assert [item["completed"] for item in emitted] == [1]
    assert progress_snapshot_paths(tmp_path / "transcribe-progress.json") == []


def test_single_task_acceleration_hardware_tiers() -> None:
    gib = 1024**3
    assert [gpu_batch_size(value * 1024) for value in (4, 8, 12, 16, 24, 32)] == [2, 4, 6, 8, 12, 16]
    assert gpu_batch_size(8 * 1024 - 1) == 2
    assert cpu_batch_size(7, 64 * gib) == 1
    assert cpu_batch_size(8, 12 * gib) == 2
    assert cpu_batch_size(16, 24 * gib) == 4
    assert cpu_batch_size(32, 48 * gib) == 6
    assert cpu_batch_size(48, 64 * gib) == 8
    assert [lower_batch_size(value) for value in (16, 12, 8, 6, 4, 2, 1)] == [12, 8, 6, 4, 2, 1, 1]


def test_larger_asr_model_uses_conservative_acceleration_without_changing_device(monkeypatch) -> None:
    monkeypatch.setattr(
        "audio_intel.performance.gpu_snapshot",
        lambda *_: {"memory_total_mib": 16 * 1024},
    )
    standard = resolve_acceleration(True, "gpu")
    large = resolve_acceleration(True, "gpu", batch_penalty_steps=2)
    assert standard["target_batch_size"] == 8
    assert large["target_batch_size"] == 4
    assert large["device"] == "gpu"
    assert large["batch_penalty_steps"] == 2


def test_asr_stage_batches_in_order_and_falls_back_after_oom(monkeypatch) -> None:
    class OutOfMemoryError(RuntimeError):
        pass

    class FakeCuda:
        cleared = 0

        @staticmethod
        def is_available() -> bool:
            return True

        @classmethod
        def empty_cache(cls) -> None:
            cls.cleared += 1

    class HookHandle:
        def __init__(self, hooks, hook) -> None:
            self.hooks = hooks
            self.hook = hook

        def remove(self) -> None:
            self.hooks.remove(self.hook)

    class HookModule:
        def __init__(self) -> None:
            self.hooks = []

        def register_forward_hook(self, hook):
            self.hooks.append(hook)
            return HookHandle(self.hooks, hook)

        def forward(self) -> None:
            for hook in list(self.hooks):
                hook(self, (), None)

    class FakeModel:
        max_inference_batch_size = 4

        def __init__(self) -> None:
            self.audio_layers = [HookModule(), HookModule()]
            self.thinker = HookModule()
            self.thinker.audio_tower = SimpleNamespace(layers=self.audio_layers)
            self.model = SimpleNamespace(thinker=self.thinker)

        def transcribe(self, *, audio, **_kwargs):
            for layer in self.audio_layers:
                layer.forward()
            self.thinker.forward()
            self.thinker.forward()
            if len(audio) > 2:
                raise OutOfMemoryError("simulated")
            return [SimpleNamespace(text=f" text-{path} ", language="Chinese") for path in audio]

    class FakeFactory:
        model = None

        @staticmethod
        def from_pretrained(*_args, **_kwargs):
            FakeFactory.model = FakeModel()
            return FakeFactory.model

    fake_torch = SimpleNamespace(
        float32="float32", bfloat16="bfloat16", cuda=FakeCuda,
        OutOfMemoryError=OutOfMemoryError,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(Qwen3ASRModel=FakeFactory))
    progress_events = []
    monkeypatch.setattr(
        asr_stage, "_progress",
        lambda *_args, **kwargs: progress_events.append(kwargs),
    )
    chunks = [{"path": str(index), "index": index} for index in range(5)]

    result = asr_stage.transcribe({
        "model_path": "unused", "compute_device": "gpu", "chunks": chunks,
        "language": "Chinese", "batch_size": 4,
    })

    assert [item["index"] for item in result["chunks"]] == list(range(5))
    assert [item["text"] for item in result["chunks"]] == [f"text-{index}" for index in range(5)]
    assert result["acceleration"] == {
        "stage": "transcription", "target_batch_size": 4,
        "effective_batch_size": 2, "fallbacks": [{"from": 4, "to": 2}],
    }
    assert FakeCuda.cleared == 1
    assert progress_events[0]["activity"]["unit"] == "model_load"
    assert all(
        event["activity"]["sequence"] >= 1
        for event in progress_events if event.get("activity")
    )
    assert not FakeFactory.model.thinker.hooks
    assert all(not layer.hooks for layer in FakeFactory.model.audio_layers)


def test_tts_sentence_chunking_preserves_text() -> None:
    source = "第一句话。第二句话！" + "长" * 350
    chunks = split_text(source, 100)
    assert "".join(chunks) == source
    assert all(len(item) <= 100 for item in chunks)


def test_tts_uses_dedicated_aligner_runtime(monkeypatch) -> None:
    monkeypatch.delenv("AUDIO_INTEL_ALIGNER_PYTHON", raising=False)
    expected = "Scripts" if __import__("platform").system() == "Windows" else "bin"
    assert expected in tts_pipeline.aligner_python().parts
    assert "aligner" in tts_pipeline.aligner_python().parts
    monkeypatch.setenv("AUDIO_INTEL_ALIGNER_PYTHON", "/opt/audio-intel/aligner-python")
    assert tts_pipeline.aligner_python() == Path("/opt/audio-intel/aligner-python")


def test_asr_merge_and_exports(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp")
    local.ensure_directories()
    monkeypatch.setattr(asr_pipeline, "settings", local)
    chunks = [{"start": 0.0, "end": 2.0, "text": "你好。", "language": "Chinese", "words": [{"text": "你", "start": 0.1, "end": 0.4}, {"text": "好", "start": 0.5, "end": 0.8}]}]
    result = asr_pipeline.assemble(chunks, [{"start": 0, "end": 2, "speaker": "Speaker_0"}], 2, True)
    exported = asr_pipeline.write_asr_exports("test", result, ["json", "srt", "vtt", "txt"])
    assert exported["timestamp_precision"] == "word_or_character"
    assert len(exported["artifacts"]) == 4
    assert all((tmp_path / "data/jobs/test/output" / item["name"]).is_file() for item in exported["artifacts"])


def test_asr_splits_aligned_text_into_speaker_turns() -> None:
    chunks = [{
        "start": 0.0,
        "end": 6.0,
        "text": "甲先说。乙回答？甲补充！",
        "language": "Chinese",
        "words": [
            {"text": "甲先说", "start": 0.2, "end": 1.2},
            {"text": "乙回答", "start": 2.2, "end": 3.2},
            {"text": "甲补充", "start": 4.2, "end": 5.2},
        ],
    }]
    diarization = [
        {"start": 0.0, "end": 1.8, "speaker": "Speaker_0"},
        {"start": 1.8, "end": 3.8, "speaker": "Speaker_1"},
        {"start": 3.8, "end": 6.0, "speaker": "Speaker_0"},
    ]

    result = asr_pipeline.assemble(chunks, diarization, 6.0, True)

    assert [item["speaker"] for item in result["segments"]] == ["Speaker_0", "Speaker_1", "Speaker_0"]
    assert [item["text"] for item in result["segments"]] == ["甲先说。", "乙回答？", "甲补充！"]
    assert "".join(item["text"] for item in result["segments"]) == result["text"]
    assert [item["id"] for item in result["segments"]] == [0, 1, 2]
    assert [item["id"] for item in result["speakers"]] == ["Speaker_0", "Speaker_1"]


def test_asr_turn_split_falls_back_without_losing_text() -> None:
    chunks = [{
        "start": 0.0,
        "end": 2.0,
        "text": "原始文本。",
        "language": "Chinese",
        "words": [
            {"text": "无法匹配", "start": 0.1, "end": 0.8},
            {"text": "文本", "start": 1.2, "end": 1.8},
        ],
    }]
    diarization = [
        {"start": 0.0, "end": 1.0, "speaker": "Speaker_0"},
        {"start": 1.0, "end": 2.0, "speaker": "Speaker_1"},
    ]

    result = asr_pipeline.assemble(chunks, diarization, 2.0, True)

    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "原始文本。"
    assert result["text"] == "原始文本。"
    assert [item["id"] for item in result["speakers"]] == ["Speaker_0", "Speaker_1"]


def test_asr_voiceprint_match_changes_labels_without_changing_speaker_ids() -> None:
    chunks = [{"start": 0.0, "end": 2.0, "text": "你好", "language": "Chinese"}]
    diarization = [{"start": 0.0, "end": 2.0, "speaker": "Speaker_0"}]
    result = asr_pipeline.assemble(
        chunks, diarization, 2.0, False,
        voiceprint_matches={"Speaker_0": {"person_id": "voice_nick", "name": "尼克杨", "note": "产品部", "score": .72}},
    )
    assert result["segments"][0]["speaker"] == "Speaker_0"
    assert result["segments"][0]["speaker_label"] == "尼克杨（产品部）"
    assert result["speakers"] == [{
        "id": "Speaker_0", "label": "尼克杨（产品部）", "label_source": "voiceprint",
        "voiceprint_match": {"person_id": "voice_nick", "name": "尼克杨", "note": "产品部", "score": .72},
    }]


def test_asr_voiceprint_note_label_is_written_to_all_exports(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp")
    local.ensure_directories()
    monkeypatch.setattr(asr_pipeline, "settings", local)
    result = asr_pipeline.assemble(
        [{"start": 0.0, "end": 1.0, "text": "你好", "language": "Chinese"}],
        [{"start": 0.0, "end": 1.0, "speaker": "Speaker_0"}],
        1.0, False,
        voiceprint_matches={"Speaker_0": {
            "person_id": "voice_nick", "name": "尼克杨", "note": "研发&平台", "score": .8,
        }},
    )
    asr_pipeline.write_asr_exports("note-label", result, ["json", "srt", "vtt", "txt"])
    output = local.jobs_dir / "note-label" / "output"
    assert "尼克杨（研发&平台）" in (output / "transcript.json").read_text(encoding="utf-8")
    assert "尼克杨（研发&平台）" in (output / "transcript.srt").read_text(encoding="utf-8")
    assert "尼克杨（研发&平台）" in (output / "transcript.txt").read_text(encoding="utf-8")
    assert "<v 尼克杨（研发&amp;平台）>" in (output / "transcript.vtt").read_text(encoding="utf-8")


def test_asr_auto_speaker_refinement_finds_split_stable_speaker() -> None:
    labels = np.asarray([0, 0, 1, 1, 2, 2, 2, 3])
    vectors = np.asarray([
        [1.0, 0.0, 0.0], [0.99, 0.01, 0.0],
        [0.0, 1.0, 0.0], [0.01, 0.99, 0.0],
        [0.0, 0.0, 1.0], [0.0, 0.02, 0.98], [0.0, 0.0, 1.0],
        [0.02, 0.0, 0.98],
    ])
    turns = [
        (0.0, 1.0, 0), (3.0, 4.0, 0), (6.0, 7.0, 0),
        (1.0, 2.0, 1), (4.0, 5.0, 1), (7.0, 8.0, 1),
        (2.0, 3.0, 2), (5.0, 6.0, 2), (8.0, 9.0, 2),
        (9.0, 10.0, 3),
    ]

    candidates = asr_pipeline._candidate_auto_merges(labels, vectors, turns)
    assert candidates == [(3, 2)]

    merges, _ = asr_pipeline._accepted_auto_merges(candidates, {
        0: np.asarray([1.0, 0.0, 0.0]),
        1: np.asarray([0.0, 1.0, 0.0]),
        2: np.asarray([0.0, 0.0, 1.0]),
        3: np.asarray([0.02, 0.0, 0.9998]),
    })
    assert merges == {3: 2}


def test_asr_auto_speaker_refinement_preserves_uncertain_one_off_speaker() -> None:
    candidates = [(3, 2)]
    merges, accepted = asr_pipeline._accepted_auto_merges(candidates, {
        0: np.asarray([1.0, 0.0, 0.0]),
        1: np.asarray([0.0, 1.0, 0.0]),
        2: np.asarray([0.0, 0.0, 1.0]),
        # Similar enough to warrant a second look, but not enough independent
        # evidence to erase a genuine participant who only spoke once.
        3: np.asarray([0.84, 0.0, 0.5426]),
    })
    assert merges == {}
    assert accepted == []


def test_asr_auto_speaker_labels_are_canonical_after_merge() -> None:
    labels = asr_pipeline._canonical_labels(np.asarray([7, 7, 2, 9, 2]))
    assert labels.tolist() == [0, 0, 1, 2, 1]


def test_asr_gpu_overlaps_batched_diarization(tmp_path, monkeypatch) -> None:
    local = replace(settings, temp_dir=tmp_path / "tmp", data_dir=tmp_path / "data", mock_mode=False)
    local.ensure_directories()
    monkeypatch.setattr(asr_pipeline, "settings", local)
    monkeypatch.setattr(asr_pipeline, "model_installation", lambda *_: {"installed": True})
    monkeypatch.setattr(asr_pipeline, "decode_audio", lambda *_: ([0.0] * 32000, 16000))
    monkeypatch.setattr(asr_pipeline, "run_vad", lambda *_: [{"start": 0.0, "end": 2.0}])
    chunks = [{"index": 0, "path": "chunk.wav", "start": 0.0, "end": 2.0}]
    monkeypatch.setattr(asr_pipeline, "write_chunks", lambda *_: chunks)
    monkeypatch.setattr(asr_pipeline, "_parallel_diarization_enabled", lambda *_: True)
    monkeypatch.setattr(asr_pipeline, "compute_device_name", lambda *_: "Test GPU")
    monkeypatch.setattr(asr_pipeline, "write_asr_exports", lambda _job_id, result, _formats: result)
    monkeypatch.setattr(asr_pipeline, "list_voiceprint_people", lambda: [])
    diarization_started = Event()
    release_diarization = Event()

    def fake_diarize(_audio, _vad, _speakers, batch_size=1):
        assert batch_size == asr_pipeline.GPU_DIARIZATION_BATCH_SIZE
        diarization_started.set()
        assert release_diarization.wait(2)
        return [{"start": 0.0, "end": 2.0, "speaker": "Speaker_0"}]

    def fake_model_stage(_context, operation, _payload, _directory, _device, _progress):
        assert operation == "transcribe"
        assert diarization_started.wait(2)
        release_diarization.set()
        return {"chunks": [{**chunks[0], "text": "测试", "language": "Chinese"}]}

    monkeypatch.setattr(asr_pipeline, "diarize", fake_diarize)
    monkeypatch.setattr(asr_pipeline, "run_model_stage", fake_model_stage)
    context = SimpleNamespace(
        job={"request": {"input_path": "input.wav", "compute_device": "gpu", "diarize": True, "align": False}},
        job_id="test",
        work_dir=tmp_path / "work",
        progress=lambda *_: None,
        set_input_duration=lambda *_: None,
    )
    context.work_dir.mkdir()

    result = asr_pipeline.process_job(context)

    assert result["text"] == "测试"
    assert result["compute_device"] == "gpu"


def test_asr_segment_mode_aligns_multi_speaker_text_internally(tmp_path, monkeypatch) -> None:
    local = replace(settings, temp_dir=tmp_path / "tmp", data_dir=tmp_path / "data", mock_mode=False)
    local.ensure_directories()
    monkeypatch.setattr(asr_pipeline, "settings", local)
    monkeypatch.setattr(asr_pipeline, "model_installation", lambda *_: {"installed": True})
    monkeypatch.setattr(asr_pipeline, "decode_audio", lambda *_: ([0.0] * 32000, 16000))
    monkeypatch.setattr(asr_pipeline, "run_vad", lambda *_: [{"start": 0.0, "end": 2.0}])
    chunks = [{"index": 0, "path": "chunk.wav", "start": 0.0, "end": 2.0}]
    monkeypatch.setattr(asr_pipeline, "write_chunks", lambda *_: chunks)
    monkeypatch.setattr(
        asr_pipeline,
        "diarize",
        lambda *_args, **_kwargs: [
            {"start": 0.0, "end": 1.0, "speaker": "Speaker_0"},
            {"start": 1.0, "end": 2.0, "speaker": "Speaker_1"},
        ],
    )
    monkeypatch.setattr(asr_pipeline, "compute_device_name", lambda *_: "CPU")
    monkeypatch.setattr(asr_pipeline, "write_asr_exports", lambda _job_id, result, _formats: result)
    monkeypatch.setattr(asr_pipeline, "list_voiceprint_people", lambda: [])
    operations = []

    def fake_model_stage(_context, operation, _payload, _directory, _device, _progress):
        operations.append(operation)
        if operation == "transcribe":
            return {"chunks": [{**chunks[0], "text": "甲说。乙答。", "language": "Chinese"}]}
        return {"chunks": [{
            **chunks[0],
            "text": "甲说。乙答。",
            "language": "Chinese",
            "words": [
                {"text": "甲说", "start": 0.2, "end": 0.8},
                {"text": "乙答", "start": 1.2, "end": 1.8},
            ],
        }]}

    monkeypatch.setattr(asr_pipeline, "run_model_stage", fake_model_stage)
    context = SimpleNamespace(
        job={"request": {
            "input_path": "input.wav", "compute_device": "cpu", "diarize": True,
            "align": False, "speaker_count": None,
        }},
        job_id="test",
        work_dir=tmp_path / "work",
        progress=lambda *_: None,
        set_input_duration=lambda *_: None,
    )
    context.work_dir.mkdir()

    result = asr_pipeline.process_job(context)

    assert operations == ["transcribe", "align"]
    assert result["timestamp_precision"] == "segment"
    assert [item["speaker"] for item in result["segments"]] == ["Speaker_0", "Speaker_1"]
    assert [item["text"] for item in result["segments"]] == ["甲说。", "乙答。"]
    assert all(item["words"] == [] for item in result["segments"])


def test_asr_auto_detection_outside_aligner_languages_returns_segments(tmp_path, monkeypatch) -> None:
    local = replace(settings, temp_dir=tmp_path / "tmp", data_dir=tmp_path / "data", mock_mode=False)
    local.ensure_directories()
    monkeypatch.setattr(asr_pipeline, "settings", local)
    monkeypatch.setattr(asr_pipeline, "model_installation", lambda *_: {"installed": True})
    monkeypatch.setattr(asr_pipeline, "decode_audio", lambda *_: ([0.0] * 32000, 16000))
    monkeypatch.setattr(asr_pipeline, "run_vad", lambda *_: [{"start": 0.0, "end": 2.0}])
    chunks = [{"index": 0, "path": "chunk.wav", "start": 0.0, "end": 2.0}]
    monkeypatch.setattr(asr_pipeline, "write_chunks", lambda *_: chunks)
    monkeypatch.setattr(asr_pipeline, "compute_device_name", lambda *_: "CPU")
    monkeypatch.setattr(asr_pipeline, "write_asr_exports", lambda _job_id, result, _formats: result)
    operations = []

    def fake_model_stage(_context, operation, _payload, _directory, _device, _progress):
        operations.append(operation)
        return {"chunks": [{**chunks[0], "text": "مرحبا بالعالم", "language": "Arabic"}]}

    monkeypatch.setattr(asr_pipeline, "run_model_stage", fake_model_stage)
    context = SimpleNamespace(
        job={"request": {
            "input_path": "input.wav", "compute_device": "cpu", "language": "Auto",
            "diarize": False, "align": True, "use_voiceprint_library": False,
        }},
        job_id="test-auto-arabic",
        work_dir=tmp_path / "work",
        progress=lambda *_: None,
        set_input_duration=lambda *_: None,
    )
    context.work_dir.mkdir()

    result = asr_pipeline.process_job(context)

    assert operations == ["transcribe"]
    assert result["language"] == "Arabic"
    assert result["timestamp_precision"] == "segment"
    assert result["segments"][0]["words"] == []


def test_tts_gpu_memory_gate_counts_reclaimable_cache() -> None:
    cuda = SimpleNamespace(
        mem_get_info=lambda: (600 * 1024**2, 4096 * 1024**2),
        memory_reserved=lambda: 1000 * 1024**2,
        memory_allocated=lambda: 400 * 1024**2,
    )
    assert tts_pipeline._gpu_can_microbatch(SimpleNamespace(cuda=cuda))
    cuda.mem_get_info = lambda: (300 * 1024**2, 4096 * 1024**2)
    assert not tts_pipeline._gpu_can_microbatch(SimpleNamespace(cuda=cuda))


def test_tts_cpu_model_cache_reuses_one_checkpoint_and_clears_before_switch_or_gpu(
    monkeypatch,
) -> None:
    class Factory:
        calls = []

        @classmethod
        def from_pretrained(cls, path, **kwargs):
            model = SimpleNamespace(path=path, options=kwargs)
            cls.calls.append(model)
            return model

    fake_torch = SimpleNamespace(float32="float32", bfloat16="bfloat16")
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_tts", SimpleNamespace(Qwen3TTSModel=Factory))
    tts_pipeline._cpu_models.clear()
    model_definition = tts_pipeline.resolve_tts_model("qwen3-tts-0.6b")

    try:
        first = tts_pipeline.load_model(model_definition, "preset", "cpu")
        repeated = tts_pipeline.load_model(model_definition, "preset", "cpu")
        switched = tts_pipeline.load_model(model_definition, "inline_clone", "cpu")

        assert first is repeated
        assert switched is not first
        assert len(Factory.calls) == 2
        assert list(tts_pipeline._cpu_models.values()) == [switched]

        gpu = tts_pipeline.load_model(model_definition, "preset", "gpu")

        assert gpu is Factory.calls[-1]
        assert len(Factory.calls) == 3
        assert tts_pipeline._cpu_models == {}
        assert gpu.options["device_map"] == "cuda:0"
        assert gpu.options["dtype"] == "bfloat16"
    finally:
        tts_pipeline._cpu_models.clear()


@pytest.mark.parametrize(("model_id", "mode"), (
    ("qwen3-tts-0.6b", "preset"),
    ("qwen3-tts-0.6b", "inline_clone"),
    ("qwen3-tts-1.7b", "preset"),
    ("qwen3-tts-1.7b", "inline_clone"),
    ("qwen3-tts-1.7b", "voice_design"),
))
def test_tts_gpu_models_load_directly(model_id: str, mode: str, monkeypatch) -> None:
    class Factory:
        calls = []

        @classmethod
        def from_pretrained(cls, path, **kwargs):
            loaded = SimpleNamespace(path=path, options=kwargs)
            cls.calls.append(loaded)
            return loaded

    fake_torch = SimpleNamespace(float32="float32", bfloat16="bfloat16")
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_tts", SimpleNamespace(Qwen3TTSModel=Factory))

    loaded = tts_pipeline.load_model(
        tts_pipeline.resolve_tts_model(model_id), mode, "gpu",
    )

    assert loaded.options["device_map"] == "cuda:0"


def test_tts_terminal_cuda_oom_records_diagnostics_and_marks_executor(
    tmp_path, monkeypatch,
) -> None:
    class OutOfMemoryError(RuntimeError):
        pass

    cleared = []
    fake_torch = SimpleNamespace(
        OutOfMemoryError=OutOfMemoryError,
        cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: cleared.append(True)),
    )
    local = replace(
        settings, mock_mode=False, models_dir=tmp_path / "models",
        temp_dir=tmp_path / "tmp", data_dir=tmp_path / "data",
    )
    monkeypatch.setattr(tts_pipeline, "settings", local)
    monkeypatch.setattr(tts_pipeline, "model_installation", lambda *_: {"installed": True})
    monkeypatch.setattr(tts_pipeline, "gpu_lease", lambda *_: nullcontext())
    snapshots = iter((
        {"snapshot": {"memory_free_mib": 3600}, "compute_processes": []},
        {"snapshot": {"memory_free_mib": 20}, "compute_processes": [{"pid": 42}]},
    ))
    monkeypatch.setattr(tts_pipeline, "gpu_diagnostics", lambda *_: next(snapshots))
    monkeypatch.setattr(
        tts_pipeline, "load_model",
        lambda *_: (_ for _ in ()).throw(OutOfMemoryError("CUDA out of memory")),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    context = SimpleNamespace(
        job={"request": {
            "text": "你好。", "model": "qwen3-tts-0.6b", "voice_mode": "preset",
            "speaker": "Vivian", "compute_device": "gpu", "response_format": "wav",
            "language": "Chinese", "accelerate_single_task": False,
        }},
        progress=lambda *_args, **_kwargs: None,
    )

    with pytest.raises(OutOfMemoryError) as caught:
        tts_pipeline.process_job(context)

    assert getattr(caught.value, "_audio_intel_executor_recycle_reason") == "cuda_oom"
    assert '"memory_free_mib": 3600' in caught.value.__notes__[0]
    assert '"memory_free_mib": 20' in caught.value.__notes__[0]
    assert cleared == [True]


def test_tts_batch_uses_sequential_decoder_and_restores_it() -> None:
    class Tokenizer:
        def __init__(self) -> None:
            self.batch_sizes = []

        def decode(self, encoded):
            self.batch_sizes.append(len(encoded))
            return [item["audio_codes"] for item in encoded], 24000

    class Model:
        def __init__(self) -> None:
            self.tokenizer = Tokenizer()
            self.model = SimpleNamespace(speech_tokenizer=self.tokenizer)
            self.arguments = None

        def generate_custom_voice(self, **kwargs):
            self.arguments = kwargs
            return self.tokenizer.decode([{"audio_codes": text} for text in kwargs["text"]])

    model = Model()
    original_decode = model.tokenizer.decode
    request = {"voice_mode": "preset", "language": "Chinese", "speaker": "Vivian", "instruct": ""}

    generated, rate = tts_pipeline._generate_tts_batch(model, request, ["第一句。", "第二句。"], None)

    assert generated == ["第一句。", "第二句。"]
    assert rate == 24000
    assert model.tokenizer.batch_sizes == [1, 1]
    assert model.tokenizer.decode == original_decode
    assert model.arguments["language"] == ["Chinese", "Chinese"]


def test_tts_batch_accepts_per_item_speakers_in_order() -> None:
    class Tokenizer:
        def decode(self, encoded):
            return [item["audio_codes"] for item in encoded], 24000

    class Model:
        def __init__(self) -> None:
            self.model = SimpleNamespace(speech_tokenizer=Tokenizer())
            self.arguments = None

        def generate_custom_voice(self, **kwargs):
            self.arguments = kwargs
            return self.model.speech_tokenizer.decode([
                {"audio_codes": speaker} for speaker in kwargs["speaker"]
            ])

    model = Model()
    request = {"voice_mode": "preset", "language": "Chinese"}
    metadata = [
        {"speaker": "Vivian", "instruct": "沉稳"},
        {"speaker": "Dylan", "instruct": "亲切"},
    ]

    generated, rate = tts_pipeline._generate_tts_batch(
        model, request, ["第一句。", "第二句。"], None, item_requests=metadata,
    )

    assert generated == ["Vivian", "Dylan"] and rate == 24000
    assert model.arguments["speaker"] == ["Vivian", "Dylan"]
    assert model.arguments["instruct"] == ["沉稳", "亲切"]


def test_tts_sequence_flattens_voice_clone_prompt_items(tmp_path, monkeypatch) -> None:
    class Prompt:
        pass

    prompt = Prompt()

    class Model:
        model = SimpleNamespace(speech_tokenizer=SimpleNamespace())

        def create_voice_clone_prompt(self, **_kwargs):
            return [prompt]

    captured: list[list[dict]] = []

    def generate(_model, _request, texts, _prompt, progress_callback=None, item_requests=None):
        captured.append(item_requests)
        return [np.zeros(240, dtype=np.float32) for _ in texts], 24000

    def encode(path, _audio, _rate, _format):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    fake_torch = SimpleNamespace(
        OutOfMemoryError=RuntimeError,
        cuda=SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(tts_pipeline, "settings", replace(settings, mock_mode=False))
    monkeypatch.setattr(tts_pipeline, "_tts_text_token_counts", lambda _model, texts: [len(text) for text in texts])
    monkeypatch.setattr(tts_pipeline, "_generate_tts_batch", generate)
    monkeypatch.setattr(tts_pipeline, "encode", encode)
    context = SimpleNamespace(output_dir=tmp_path / "output", progress=lambda *_, **__: None)
    sample_id = "sample-one"

    result = tts_pipeline._process_sequence_loaded(
        context,
        {"voice_mode": "voiceprint", "language": "Chinese", "compute_device_name": "CPU"},
        [
            {"id": "turn_0001", "text": "第一句。", "voiceprint_sample_id": sample_id},
            {"id": "turn_0002", "text": "第二句。", "voiceprint_sample_id": sample_id},
        ],
        {sample_id: {"reference_audio_path": "reference.wav", "reference_text": "参考。"}},
        Model(),
        "cpu",
        {"requested": True, "device": "cpu", "target_batch_size": 2},
        tts_pipeline.resolve_tts_model("qwen3-tts-0.6b"),
        tts_pipeline.resolve_tts_model("qwen3-tts-0.6b")["checkpoints"]["base"],
    )

    assert result["sequence"]["items"][0]["id"] == "turn_0001"
    assert captured and [item["_clone_prompt"] for item in captured[0]] == [prompt, prompt]
    assert all(not isinstance(item["_clone_prompt"], list) for item in captured[0])


def test_tts_sequence_writes_one_ordered_artifact_per_item(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(tts_pipeline, "settings", replace(settings, mock_mode=True))
    output = tmp_path / "output"
    output.mkdir()
    context = SimpleNamespace(output_dir=output, progress=lambda *_, **__: None)
    request = {
        "voice_mode": "preset", "language": "Chinese", "compute_device_name": "Test GPU",
    }
    items = [
        {"id": "turn_0001", "text": "第一句。", "speaker": "Vivian", "instruct": ""},
        {"id": "turn_0002", "text": "第二句。", "speaker": "Dylan", "instruct": ""},
    ]
    model = tts_pipeline.resolve_tts_model("qwen3-tts-0.6b")

    result = tts_pipeline._process_sequence_loaded(
        context, request, items, {}, None, "gpu",
        {"requested": True, "device": "gpu", "target_batch_size": 2},
        model, model["checkpoints"]["custom_voice"],
    )

    assert result["sequence"] == {
        "contract_version": 1,
        "items": [
            {"id": "turn_0001", "artifact_name": "item-0000.wav", "duration": 1.5, "sample_rate": 24000},
            {"id": "turn_0002", "artifact_name": "item-0001.wav", "duration": 1.5, "sample_rate": 24000},
        ],
    }
    assert [item["name"] for item in result["artifacts"]] == ["item-0000.wav", "item-0001.wav"]
    assert all((output / item["name"]).is_file() for item in result["artifacts"])
    assert result["acceleration"]["stage_batch_sizes"] == {"generation": 2, "decoder": 1}


def test_tts_voice_design_batches_natural_language_instruction() -> None:
    class Tokenizer:
        def decode(self, encoded):
            return [item["audio_codes"] for item in encoded], 24000

    class Model:
        def __init__(self) -> None:
            self.model = SimpleNamespace(speech_tokenizer=Tokenizer())
            self.arguments = None

        def generate_voice_design(self, **kwargs):
            self.arguments = kwargs
            return [[0.1], [0.2]], 24000

    model = Model()
    request = {
        "voice_mode": "voice_design", "language": "Chinese",
        "instruct": "温暖成熟的声音，语速舒缓。",
    }

    generated, rate = tts_pipeline._generate_tts_batch(
        model, request, ["第一句。", "第二句。"], None,
    )

    assert generated == [[0.1], [0.2]]
    assert rate == 24000
    assert model.arguments["instruct"] == [request["instruct"], request["instruct"]]
    assert model.arguments["language"] == ["Chinese", "Chinese"]


def test_tts_worker_rejects_legacy_nonempty_instruction_before_model_loading() -> None:
    context = SimpleNamespace(job={"request": {"instruct": "Very happy."}})

    with pytest.raises(ValueError, match="not supported"):
        tts_pipeline.process_job(context)


def test_tts_decode_observer_reports_frames_and_removes_hook_after_error() -> None:
    class Handle:
        def __init__(self, owner) -> None:
            self.owner = owner

        def remove(self) -> None:
            self.owner.hook = None

    class Talker:
        hook = None

        def register_forward_hook(self, hook):
            self.hook = hook
            return Handle(self)

    talker = Talker()
    model = SimpleNamespace(model=SimpleNamespace(talker=talker))
    observed = []

    try:
        with tts_pipeline._observe_tts_decode(model, observed.append):
            talker.hook(None, None, SimpleNamespace(generation_step=0))
            talker.hook(None, None, SimpleNamespace(generation_step=4))
            raise RuntimeError("generation failed")
    except RuntimeError:
        pass

    assert observed == [1, 5]
    assert talker.hook is None


def test_tts_acceleration_retries_current_batch_after_oom(tmp_path, monkeypatch) -> None:
    class OutOfMemoryError(RuntimeError):
        pass

    fake_torch = SimpleNamespace(
        OutOfMemoryError=OutOfMemoryError,
        cuda=SimpleNamespace(is_available=lambda: False, empty_cache=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr(tts_pipeline, "settings", replace(settings, mock_mode=False))
    calls: list[int] = []

    def generate(_model, _request, texts, _prompt, progress_callback=None):
        calls.append(len(texts))
        if progress_callback:
            progress_callback(1)
        if len(texts) > 2:
            raise OutOfMemoryError("simulated")
        return [np.zeros(240, dtype=np.float32) for _ in texts], 24000

    def encode(path, _audio, _rate, _format):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(tts_pipeline, "_generate_tts_batch", generate)
    monkeypatch.setattr(tts_pipeline, "encode", encode)
    context = SimpleNamespace(output_dir=tmp_path / "output", progress=lambda *_, **__: None)
    request = {"voice_mode": "preset", "response_format": "wav", "compute_device_name": "CPU"}

    result = tts_pipeline._process_loaded(
        context, request, ["一", "二", "三", "四"], object(), "cpu",
        {"requested": True, "device": "cpu", "target_batch_size": 4},
        tts_pipeline.resolve_tts_model("qwen3-tts-0.6b"),
        tts_pipeline.resolve_tts_model("qwen3-tts-0.6b")["checkpoints"]["custom_voice"],
    )

    assert calls == [4, 2, 2]
    assert result["acceleration"]["stage_batch_sizes"] == {"generation": 2, "decoder": 1}
    assert result["acceleration"]["oom_fallbacks"] == [{"stage": "generation", "from": 4, "to": 2}]


def test_tts_overlong_reference_is_clipped_with_matching_text(tmp_path) -> None:
    source = tmp_path / "reference.wav"
    with wave.open(str(source), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 16000 * 20)
    work = tmp_path / "work"
    work.mkdir()
    request = {
        "reference_audio_path": str(source), "reference_text": "一二三四", "language": "Chinese",
        "reference_words": [
            {"text": "一", "start": 0.1, "end": 4.0},
            {"text": "二", "start": 4.2, "end": 9.0},
            {"text": "三", "start": 9.2, "end": 14.0},
            {"text": "四", "start": 14.2, "end": 18.0},
        ],
    }
    context = SimpleNamespace(work_dir=work, progress=lambda *_: None)
    tts_pipeline._prepare_clone_reference(context, request, "cpu")
    with wave.open(request["reference_audio_path"], "rb") as handle:
        duration = handle.getnframes() / handle.getframerate()
    assert request["reference_text"] == "一二三"
    assert duration == 14.0
    assert request["reference_duration_original"] == 20.0
    assert request["reference_duration_used"] == 14.0
    assert request["reference_truncated"] is True
