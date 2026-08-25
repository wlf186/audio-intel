from __future__ import annotations

from dataclasses import replace
from threading import Event
from types import SimpleNamespace

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
