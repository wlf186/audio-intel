from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace
import wave

from audio_intel.config import settings
from audio_intel.utils import safe_filename, timecode, waveform_peaks
from asr import pipeline as asr_pipeline
from tts import pipeline as tts_pipeline
from tts.pipeline import split_text


def test_utilities() -> None:
    assert safe_filename("../访谈 录音?.wav") == "访谈_录音_.wav"
    assert timecode(3723.456) == "01:02:03.456"
    assert waveform_peaks([0, -0.5, 0.2, 1], 2) == [0.5, 1.0]


def test_tts_sentence_chunking_preserves_text() -> None:
    source = "第一句话。第二句话！" + "长" * 350
    chunks = split_text(source, 100)
    assert "".join(chunks) == source
    assert all(len(item) <= 100 for item in chunks)


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
    )
    context.work_dir.mkdir()

    result = asr_pipeline.process_job(context)

    assert operations == ["transcribe", "align"]
    assert result["timestamp_precision"] == "segment"
    assert [item["speaker"] for item in result["segments"]] == ["Speaker_0", "Speaker_1"]
    assert [item["text"] for item in result["segments"]] == ["甲说。", "乙答。"]
    assert all(item["words"] == [] for item in result["segments"])


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
