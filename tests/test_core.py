from __future__ import annotations

from dataclasses import replace

from audio_intel.config import settings
from audio_intel.utils import safe_filename, timecode, waveform_peaks
from asr import pipeline as asr_pipeline
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
