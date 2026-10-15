from __future__ import annotations

import hashlib
import json
import io
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from audio_intel import api, db, worker
from audio_intel.asr_enhancement import request_mode
from audio_intel.config import settings
from audio_intel.model_registry import model_installation
from asr import enhancement, pipeline
from scripts import download_models


@pytest.fixture(params=["full", "cpu"])
def local(tmp_path, monkeypatch, request):
    value = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
                    mock_mode=True, api_key="enhancement-test", min_free_disk_bytes=0,
                    deployment_profile=request.param)
    for module in (api, db, worker, pipeline, enhancement):
        monkeypatch.setattr(module, "settings", value)
    hardware = {"name": "Fixture GPU", "memory_used_mib": 0, "memory_free_mib": 16000,
                "memory_total_mib": 16000, "utilization": 0}
    monkeypatch.setattr(api, "gpu_snapshot", lambda *_: hardware)
    monkeypatch.setattr(api, "cached_gpu_snapshot", lambda *_args, **_kwargs: hardware)
    return value


def test_defaults_off_validation_and_historical_idempotent_replay(local):
    files = {"file": ("public-fixture.wav", b"RIFF-public-fixture", "audio/wav")}
    headers = {"Authorization": "Bearer enhancement-test", "Idempotency-Key": "enhancement-default-01"}
    with TestClient(api.create_app()) as client:
        capability = client.get("/api/v1/capabilities", headers=headers).json()["asr"]["audio_enhancement"]
        assert capability["default"] == "auto" and capability["compute_device"] == "cpu"
        first = client.post("/api/v1/asr/jobs", headers=headers, files=files)
        assert first.status_code == 202
        assert first.json()["request"]["audio_enhancement"] == "auto"
        replay = client.post("/api/v1/asr/jobs", headers=headers, files=files)
        assert replay.status_code == 200 and replay.json()["id"] == first.json()["id"]
        assert client.post("/api/v1/asr/jobs", headers=headers, files=files,
                           data={"audio_enhancement": "off"}).status_code == 409
        assert client.post("/api/v1/asr/jobs", headers={**headers, "Idempotency-Key": "enhancement-invalid-01"},
                           files=files, data={"audio_enhancement": "strong"}).status_code == 422
        old_request = first.json()["request"].copy()
        old_request.pop("audio_enhancement")
        digest = hashlib.sha256(files["file"][1]).hexdigest()
        key = "enhancement-historical-01"
        old, _ = db.create_job_idempotent("asr", "public-fixture.wav", old_request, "a" * 32,
            "submit_asr", api.idempotency_key_hash(key), api.request_fingerprint(old_request, digest,
                {"model", "effective_context", "hotword_lists", "hotword_term_count", "hotword_list_ids",
                 "voiceprint_groups", "voiceprint_person_ids", "voiceprint_group_ids"}))
        before = db.get_job(old["id"])
        old_headers = {**headers, "Idempotency-Key": key}
        restored = client.post("/api/v1/asr/jobs", headers=old_headers, files=files)
        assert restored.status_code == 200, restored.text
        assert restored.json()["id"] == old["id"]
        assert db.get_job(old["id"]) == before
        assert "audio_enhancement" not in restored.json()["request"]
        assert client.post("/api/v1/asr/jobs", headers=old_headers, files=files,
                           data={"audio_enhancement": "auto"}).status_code == 409
        assert client.post("/api/v1/asr/jobs", headers={**headers, "Idempotency-Key": "enhancement-off-001"},
                           files=files, data={"audio_enhancement": "off"}).json()["request"]["audio_enhancement"] == "off"
    assert request_mode(None, {"request": {}}) == "off"


def test_openai_submission_defaults_and_opt_out(local, monkeypatch):
    async def complete(job_id, **kwargs):
        db.finish_job(job_id, "succeeded", result_json={"text": "public fixture"})
        return db.get_job(job_id)
    monkeypatch.setattr(api, "wait_for_job", complete)
    with TestClient(api.create_app(), headers={"Authorization": "Bearer enhancement-test"}) as client:
        for value in (None, "off"):
            response = client.post("/v1/audio/transcriptions",
                files={"file": ("public.wav", b"RIFF-fixture", "audio/wav")},
                data={} if value is None else {"audio_enhancement": value})
            assert response.status_code == 200
            assert db.get_job(response.headers["X-Job-ID"])["request"]["audio_enhancement"] == (value or "auto")


def test_clean_and_silent_signals_bypass_exactly():
    for audio in (np.zeros(32000, np.float32), (.2 * np.sin(np.arange(32000) * .1)).astype(np.float32)):
        output, stats = enhancement.enhance_block(audio, audio * .999, np.ones(100, bool))
        assert np.array_equal(output, audio)
        assert not stats["denoised"] and stats["gain_db"] == 0


def test_quiet_speech_gain_preserves_noise_gaps_and_limits_transients():
    audio = np.zeros(80000, np.float32)
    audio[32000:64000] = .003 * np.sin(np.arange(32000) * .1)
    audio[50000] = .98
    speech = np.zeros(250, bool)
    speech[100:200] = True
    output, stats = enhancement.enhance_block(audio, audio, speech)
    region = slice(34000, 45000)
    assert np.sqrt(np.mean(output[region] ** 2)) > np.sqrt(np.mean(audio[region] ** 2)) * 2
    assert np.array_equal(output[:16000], audio[:16000])
    assert np.max(np.abs(output)) <= enhancement.CEILING + 1e-6
    assert stats["gain_db"] <= enhancement.MAX_GAIN_DB
    assert not stats["denoised"]


def test_conservative_denoise_keeps_other_speakers_signal():
    speech = (.2 * np.sin(np.arange(32000) * .1)).astype(np.float32)
    noise = np.random.default_rng(42).normal(0, .12, len(speech)).astype(np.float32)
    audio = speech + noise
    output, stats = enhancement.enhance_block(audio, speech, np.ones(100, bool))
    assert stats["denoised"]
    assert np.mean((output - speech) ** 2) < np.mean((audio - speech) ** 2)
    # The dry signal remains present; this is not target-speaker extraction.
    assert np.mean((output - speech) ** 2) > 0


def test_streaming_length_progress_cleanup_and_cancel(local, tmp_path):
    source = tmp_path / "source.wav"
    samples = (.003 * np.sin(np.arange(31 * 16000 + 123) * .1)).astype(np.float32)
    sf.write(source, samples, 16000, subtype="PCM_16")
    target = tmp_path / "enhanced.wav"
    progress = []
    result = enhancement.enhance_file(source, target, [{"start": 0, "end": 32}],
        lambda current, total: progress.append((current, total)), denoiser=lambda x: x)
    assert result["status"] == "applied"
    assert sf.info(target).frames == len(samples)
    assert progress[-1] == (len(samples), len(samples))
    assert all(a[0] < b[0] for a, b in zip(progress, progress[1:]))
    assert result["waveform"]
    assert not target.with_suffix(".partial.wav").exists()
    target.unlink()
    def cancel(*args):
        raise worker.JobCancelled("Fixture cancellation")
    with pytest.raises(worker.JobCancelled):
        enhancement.enhance_file(source, target, [{"start": 0, "end": 32}], cancel, denoiser=lambda x: x)
    assert not target.exists() and not target.with_suffix(".partial.wav").exists()
    with pytest.raises(ValueError):
        enhancement.enhance_file(source, target, [], lambda *_: None, denoiser=lambda x: x * np.nan)
    assert not target.exists() and not target.with_suffix(".partial.wav").exists()


def test_model_marker_and_checksum_are_both_required(tmp_path):
    folder = tmp_path / "Enhancer"
    folder.mkdir()
    (folder / "weights").write_bytes(b"correct")
    model = {"name": "Enhancer", "revision": "pinned", "required_files": ["weights"],
             "sha256": {"weights": hashlib.sha256(b"correct").hexdigest()}}
    (folder / ".complete").write_text("wrong", encoding="utf-8")
    assert model_installation(tmp_path, model)["state"] == "revision_mismatch"
    (folder / ".complete").write_text("pinned", encoding="utf-8")
    assert model_installation(tmp_path, model)["installed"]
    (folder / "weights").write_bytes(b"corrupt")
    assert model_installation(tmp_path, model)["state"] == "incomplete"


def test_unreadable_enhancement_weights_leave_base_service_available(tmp_path, monkeypatch):
    folder = tmp_path / "Enhancer"
    folder.mkdir()
    (folder / "weights").write_bytes(b"correct")
    (folder / ".complete").write_text("pinned", encoding="utf-8")
    model = {"name": "Enhancer", "revision": "pinned", "required_files": ["weights"],
             "sha256": {"weights": hashlib.sha256(b"correct").hexdigest()}}
    original = Path.open
    def unreadable(path, *args, **kwargs):
        if path == folder / "weights":
            raise PermissionError("Fixture file lock")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", unreadable)
    assert model_installation(tmp_path, model)["state"] == "incomplete"


@pytest.mark.parametrize("valid", [True, False])
def test_pinned_enhancer_download_validates_bytes_before_installing(tmp_path, monkeypatch, valid):
    model = {"name": "Enhancer", "repository": "fixture/model", "revision": "immutable",
             "required_files": ["weights.tar"], "source_files": {"weights.tar": "checkpoints/weights.tar"},
             "sha256": {"weights.tar": hashlib.sha256(b"correct").hexdigest()}}
    urls = []
    def download(url, **kwargs):
        urls.append(url)
        return io.BytesIO(b"correct" if valid else b"corrupt")
    monkeypatch.setattr(download_models, "MODELS", tmp_path)
    monkeypatch.setattr(download_models.urllib.request, "urlopen", download)
    if valid:
        download_models.github(model)
        assert model_installation(tmp_path, model)["installed"]
    else:
        with pytest.raises(RuntimeError, match="Checksum mismatch"):
            download_models.github(model)
        assert not (tmp_path / "Enhancer/.complete").exists()
        assert not (tmp_path / "Enhancer/weights.tar").exists()
    assert urls == ["https://raw.githubusercontent.com/fixture/model/immutable/checkpoints/weights.tar"]
    assert not list((tmp_path / "Enhancer").glob("*.partial"))


def test_streamed_boundary_blend_respects_peak_ceiling(local, tmp_path):
    source, target = tmp_path / "source.wav", tmp_path / "enhanced.wav"
    samples = (.003 * np.sin(np.arange(31 * 16000) * .1)).astype(np.float32)
    samples[30 * 16000] = .98
    sf.write(source, samples, 16000, subtype="PCM_16")
    enhancement.enhance_file(source, target, [{"start": 0, "end": 31}],
                            lambda *_: None, denoiser=lambda x: x)
    output, _ = sf.read(target, dtype="float32")
    assert len(output) == len(samples)
    assert np.max(np.abs(output)) <= enhancement.CEILING + 1e-6


@pytest.mark.parametrize("extra", [123, 16000])
def test_no_raw_noise_returns_at_processing_boundaries(local, tmp_path, extra):
    source, target = tmp_path / "source.wav", tmp_path / "enhanced.wav"
    samples = np.full(30 * 16000 + extra, .2, np.float32)
    sf.write(source, samples, 16000, subtype="PCM_16")
    enhancement.enhance_file(source, target, [], lambda *_: None,
                             denoiser=lambda x: np.zeros_like(x), enable_gain=False)
    output, _ = sf.read(target, dtype="float32")
    assert len(output) == len(samples)
    # The old boundary ramp returned to raw .2 every thirty seconds.
    assert np.max(np.abs(output - .1)) < 4e-5


def test_whole_clean_file_bypasses_sample_exactly_across_blocks(local, tmp_path):
    source, target = tmp_path / "source.wav", tmp_path / "enhanced.wav"
    audio = np.random.default_rng(17).normal(0, .1, 31 * 16000 + 43).astype(np.float32)
    audio[30 * 16000] = .97
    sf.write(source, audio, 16000, subtype="PCM_16")
    result = enhancement.enhance_file(source, target, [], lambda *_: None, denoiser=lambda x: x)
    assert result["status"] == "bypassed" and not target.exists()
    assert not target.with_suffix(".partial.wav").exists()


def test_pause_suppression_protects_both_vad_passes_and_bounds_noise(local, tmp_path):
    path = tmp_path / "enhanced.wav"
    audio = np.full(6 * 16000, .05, np.float32)
    sf.write(path, audio, 16000, subtype="PCM_16")
    original, _ = sf.read(path, dtype="float32")
    speech = enhancement.merge_regions([{"start": 1., "end": 2.}], [{"start": 2.5, "end": 3.}])
    progress = []
    result = enhancement.suppress_pauses(path, speech, [{"start": 0., "end": 5.}],
        lambda current, total: progress.append((current, total)))
    output, _ = sf.read(path, dtype="float32")
    assert np.array_equal(output[int(.8 * 16000):int(3.4 * 16000)], original[int(.8 * 16000):int(3.4 * 16000)])
    assert np.array_equal(output[5 * 16000:], original[5 * 16000:])
    assert np.max(np.abs(output[int(3.6 * 16000):int(4.8 * 16000)])) < .026
    assert result["pause_suppression_reason"] == "applied"
    assert result["max_pause_attenuation_db"] == 6
    assert 2 < result["pause_attenuated_seconds"] < 3
    assert progress[-1] == (len(audio), len(audio))
    assert not path.with_suffix(".pause.partial.wav").exists()


@pytest.mark.parametrize("speech,noise", [([], [{"start": 0., "end": 5.}]),
    ([{"start": 1., "end": 2.}], []),
    ([{"start": 0., "end": 1.}, {"start": 1.8, "end": 5.}], [{"start": 0., "end": 5.}])])
def test_no_speech_no_noise_or_short_gap_never_attenuates(local, tmp_path, speech, noise):
    path = tmp_path / "enhanced.wav"
    sf.write(path, np.full(5 * 16000, .01, np.float32), 16000, subtype="PCM_16")
    before = path.read_bytes()
    result = enhancement.suppress_pauses(path, speech, noise, lambda *_: None)
    assert path.read_bytes() == before and result["pause_attenuated_seconds"] == 0


def test_pause_cancellation_closes_handles_and_removes_temporary(local, tmp_path):
    path = tmp_path / "enhanced.wav"
    sf.write(path, np.full(31 * 16000, .01, np.float32), 16000, subtype="PCM_16")
    before = path.read_bytes()
    def cancel(*_):
        raise worker.JobCancelled("Public cancellation fixture")
    with pytest.raises(worker.JobCancelled):
        enhancement.suppress_pauses(path, [{"start": 0., "end": 1.}],
                                    [{"start": 0., "end": 31.}], cancel)
    assert path.read_bytes() == before
    assert not path.with_suffix(".pause.partial.wav").exists()
    path.unlink()  # Also checks reader handles are closed on Windows.


def test_full_enhancement_uses_final_waveform_and_keeps_quiet_detected_speech(local, tmp_path, monkeypatch):
    source, target = tmp_path / "source.wav", tmp_path / "enhanced.wav"
    sf.write(source, np.full(5 * 16000, .1, np.float32), 16000, subtype="PCM_16")
    observed = []
    def waveform(path):
        audio, _ = sf.read(path, dtype="float32")
        observed.append(float(audio[-8000]))
        return [observed[-1]]
    monkeypatch.setattr(enhancement, "_waveform", waveform)
    result = enhancement.enhance_audio(source, target, [{"start": 0., "end": 1.}],
        lambda *_: None, lambda *_: None, lambda *_: None, denoiser=lambda x: x * .5,
        speech_detector=lambda *_: [{"start": 2., "end": 3.}])
    output, _ = sf.read(target, dtype="float32")
    assert np.min(output[int(1.8 * 16000):int(3.4 * 16000)]) > .074
    assert output[-8000] < .04
    assert result["waveform"] == observed and len(observed) == 1
    assert result["processing_version"] == 2
    assert "_noisy_regions" not in result


def test_second_vad_failure_keeps_enhancement_but_skips_pause_attenuation(local, tmp_path):
    source, target = tmp_path / "source.wav", tmp_path / "enhanced.wav"
    sf.write(source, np.full(3 * 16000, .1, np.float32), 16000, subtype="PCM_16")
    def unavailable(*_):
        raise RuntimeError("Public detector failure fixture")
    result = enhancement.enhance_audio(source, target, [{"start": 0., "end": 1.}],
        lambda *_: None, lambda *_: None, lambda *_: None, denoiser=lambda x: x * .5,
        speech_detector=unavailable)
    output, _ = sf.read(target, dtype="float32")
    assert np.min(output) > .074 and result["status"] == "applied"
    assert result["pause_suppression_reason"] == "vad_unavailable"
    assert result["pause_attenuated_seconds"] == 0


def test_second_vad_cancellation_propagates_and_removes_enhanced_file(local, tmp_path):
    source, target = tmp_path / "source.wav", tmp_path / "enhanced.wav"
    sf.write(source, np.full(3 * 16000, .1, np.float32), 16000, subtype="PCM_16")
    def cancelled(*_):
        raise worker.JobCancelled("Public VAD cancellation fixture")
    with pytest.raises(worker.JobCancelled):
        enhancement.enhance_audio(source, target, [{"start": 0., "end": 1.}],
            lambda *_: None, lambda *_: None, lambda *_: None, denoiser=lambda x: x * .5,
            speech_detector=cancelled)
    assert not target.exists()
    assert not target.with_suffix(".partial.wav").exists()
    assert not target.with_suffix(".pause.partial.wav").exists()


def test_enhancement_stage_does_not_convert_cancellation_to_fallback(local, tmp_path, monkeypatch):
    from asr import stage
    def cancelled(*args, **kwargs):
        raise worker.JobCancelled("Public stage cancellation fixture")
    monkeypatch.setattr(enhancement, "enhance_audio", cancelled)
    with pytest.raises(worker.JobCancelled):
        stage.enhance({"input_path": str(tmp_path / "source.wav"),
                       "enhanced_path": str(tmp_path / "enhanced.wav"), "speech_regions": []})


def test_enhanced_playback_is_separate_authenticated_and_range_capable(local, monkeypatch):
    value = replace(local, mock_mode=False)
    for module in (api, db, worker, pipeline):
        monkeypatch.setattr(module, "settings", value)
    monkeypatch.setattr(pipeline, "model_installation", lambda *_: {"installed": True})
    monkeypatch.setattr(pipeline, "run_vad", lambda *_: [{"start": 0., "end": 2.}])
    def stage(operation, payload, directory, report):
        assert operation == "enhance"
        data, rate = sf.read(payload["input_path"], dtype="float32")
        sf.write(payload["enhanced_path"], data * 2, rate, subtype="PCM_16")
        return {"mode": "auto", "status": "applied", "reason": "quiet_speech",
                "speech_regions": [{"start": 0., "end": 2.}], "waveform": [.2] * 240}
    def transcribe(context, operation, payload, directory, device, progress):
        return {"chunks": [{**x, "text": "public fixture", "language": "English"} for x in payload["chunks"]]}
    monkeypatch.setattr(pipeline, "run_stage", stage)
    monkeypatch.setattr(pipeline, "run_model_stage", transcribe)
    with TestClient(api.create_app()) as client:
        request = {"compute_device": "cpu", "diarize": False, "align": False, "audio_enhancement": "auto"}
        job = db.create_job("asr", "public fixture.wav", request)
        source = value.jobs_dir / job["id"] / "input/source.wav"
        source.parent.mkdir(parents=True)
        sf.write(source, .04 * np.sin(np.arange(32000) * .1), 16000, subtype="PCM_16")
        request["input_path"] = str(source)
        with db.connect() as connection:
            connection.execute("UPDATE jobs SET request_json=? WHERE id=?", (json.dumps(request), job["id"]))
        original = source.read_bytes()
        claimed = db.claim_job("asr", "enhancement-fixture")
        assert claimed["id"] == job["id"]
        assert worker._run_one_job("asr", job["id"], "enhancement-fixture", 1, pipeline.process_job)["state"] == "succeeded"
        result = db.get_job(job["id"])["result"]
        url = result["audio_enhancement"]["playback_url"]
        assert client.get(url).status_code == 401
        headers = {"Authorization": "Bearer enhancement-test"}
        enhanced = client.get(url, headers=headers)
        raw = client.get(result["playback_url"], headers=headers)
        assert enhanced.status_code == raw.status_code == 200
        assert enhanced.content != raw.content
        assert source.read_bytes() == original
        assert client.get(url, headers={**headers, "Range": "bytes=0-43"}).status_code == 206
        assert client.get(url + "/waveform", headers=headers).status_code == 200
        assert client.get('/api/v1/jobs', headers=headers).json()['items'][0].get('result') is None
        assert client.post('/api/v1/auth/session', headers=headers).status_code == 204
        assert client.get(url, headers={'Range': 'bytes=0-43'}).status_code == 206
