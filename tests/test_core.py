from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace
import sys
import wave

import numpy as np

from audio_intel.config import settings
from audio_intel.performance import cpu_batch_size, gpu_batch_size, lower_batch_size
from audio_intel.utils import safe_filename, timecode, waveform_peaks
from asr import pipeline as asr_pipeline
from asr import stage as asr_stage
from tts import pipeline as tts_pipeline
from tts.pipeline import split_text


def test_utilities() -> None:
    assert safe_filename("../访谈 录音?.wav") == "访谈_录音_.wav"
    assert timecode(3723.456) == "01:02:03.456"
    assert waveform_peaks([0, -0.5, 0.2, 1], 2) == [0.5, 1.0]


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

    class FakeModel:
        max_inference_batch_size = 4

        def transcribe(self, *, audio, **_kwargs):
            if len(audio) > 2:
                raise OutOfMemoryError("simulated")
            return [SimpleNamespace(text=f" text-{path} ", language="Chinese") for path in audio]

    class FakeFactory:
        @staticmethod
        def from_pretrained(*_args, **_kwargs):
            return FakeModel()

    fake_torch = SimpleNamespace(
        float32="float32", bfloat16="bfloat16", cuda=FakeCuda,
        OutOfMemoryError=OutOfMemoryError,
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_asr", SimpleNamespace(Qwen3ASRModel=FakeFactory))
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
    assert str(tts_pipeline.aligner_python()) == "/opt/audio-intel/aligner-python"


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
        voiceprint_matches={"Speaker_0": {"person_id": "voice_nick", "name": "尼克杨", "score": .72}},
    )
    assert result["segments"][0]["speaker"] == "Speaker_0"
    assert result["segments"][0]["speaker_label"] == "尼克杨"
    assert result["speakers"] == [{
        "id": "Speaker_0", "label": "尼克杨", "label_source": "voiceprint",
        "voiceprint_match": {"person_id": "voice_nick", "name": "尼克杨", "score": .72},
    }]


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
    monkeypatch.setattr(asr_pipeline, "decode_audio", lambda *_: ([0.0] * 32000, 16000))
    monkeypatch.setattr(asr_pipeline, "run_vad", lambda *_: [{"start": 0.0, "end": 2.0}])
    chunks = [{"index": 0, "path": "chunk.wav", "start": 0.0, "end": 2.0}]
    monkeypatch.setattr(asr_pipeline, "write_chunks", lambda *_: chunks)
    monkeypatch.setattr(asr_pipeline, "_parallel_diarization_enabled", lambda *_: True)
    monkeypatch.setattr(asr_pipeline, "compute_device_name", lambda *_: "Test GPU")
    monkeypatch.setattr(asr_pipeline, "write_asr_exports", lambda _job_id, result, _formats: result)
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

    def generate(_model, _request, texts, _prompt):
        calls.append(len(texts))
        if len(texts) > 2:
            raise OutOfMemoryError("simulated")
        return [np.zeros(240, dtype=np.float32) for _ in texts], 24000

    def encode(path, _audio, _rate, _format):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(tts_pipeline, "_generate_tts_batch", generate)
    monkeypatch.setattr(tts_pipeline, "encode", encode)
    context = SimpleNamespace(output_dir=tmp_path / "output", progress=lambda *_: None)
    request = {"voice_mode": "preset", "response_format": "wav", "compute_device_name": "CPU"}

    result = tts_pipeline._process_loaded(
        context, request, ["一", "二", "三", "四"], object(), "cpu",
        {"requested": True, "device": "cpu", "target_batch_size": 4},
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
