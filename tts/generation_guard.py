"""Bounded Qwen generation; only failed rows change their sampling trajectory.

Imports of inference libraries stay inside the TTS runtime. The job scope wraps
our pinned model temporarily, never modifies its weights or generation config.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator

from audio_intel.progress import ThrottledProgress

MAX_RETRIES = 3
_active: ContextVar[GenerationGuard | None] = ContextVar("tts_generation_guard", default=None)


class TtsGenerationGuardError(RuntimeError):
    """An original text chunk exhausted its quality-recovery allowance."""


def token_counts(model: Any, texts: list[str]) -> tuple[list[int], str]:
    try:
        prompt = int(model.processor(text=model._build_assistant_text(""), return_tensors="pt", padding=True)["input_ids"].shape[-1])
        counts = [max(1, int(model.processor(text=model._build_assistant_text(text), return_tensors="pt", padding=True)["input_ids"].shape[-1]) - prompt) for text in texts]
        return counts, "tokenizer"
    except (AttributeError, IndexError, TypeError, ValueError):
        return [max(1, len(text)) for text in texts], "characters"


def generation_budget(tokens: int) -> int:
    return min(8192, max(750, math.ceil(tokens * 13.5)))


def recovery_text(text: str) -> str:
    # Mask URLs without changing positions, including a URL cut by a chunk end.
    urls = list(re.finditer(r"(?:https?://|www\.)\S+", text, re.IGNORECASE))
    masked = list(text)
    for match in urls:
        masked[match.start():match.end()] = "U" * len(match.group())
    if len(re.findall(r"\.{8,}[ \t]*\d{1,6}[ \t]*(?:\n|$)", "".join(masked))) < 2:
        return text
    return re.sub(r"\.{8,}", lambda m: m.group() if any(u.start() <= m.start() < u.end() for u in urls) else " ", text)


def current_guard() -> GenerationGuard | None:
    return _active.get()


def locate(scope: str, index: int) -> None:
    guard = current_guard()
    if guard is not None:
        guard.location = (scope, index)


def _write_json(path: Path, value: Any) -> None:
    # Strict, durable accounting. A failed write must not allow an uncounted retry.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class GenerationAttempt:
    """Observe actual EOS before padding and skip decoding rejected batch rows."""

    def __init__(self, model: Any, texts: list[str]) -> None:
        self.model = model
        counts, self.basis = token_counts(model, texts)
        self.counts = counts
        self.limits = [generation_budget(count) for count in counts]
        self.rows: list[dict[str, Any]] = []
        self.rng: dict[str, Any] = {}

    @contextmanager
    def observe(self) -> Iterator[None]:
        import numpy as np
        import torch
        from transformers import StoppingCriteria, StoppingCriteriaList

        talker = self.model.model.talker
        tokenizer = self.model.model.speech_tokenizer
        original_generate, original_decode = talker.generate, tokenizer.decode
        device = self.model.model.device
        self.rng = {"cpu": torch.get_rng_state()}
        if device.type == "cuda":
            self.rng["cuda"] = torch.cuda.get_rng_state(device)
            self.rng["cuda_device"] = str(device)
        limits = torch.tensor(self.limits, device=device, dtype=torch.long)

        class RowBudget(StoppingCriteria):
            def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> Any:
                return input_ids.shape[-1] >= limits

        def generate(*args: Any, **kwargs: Any) -> Any:
            kwargs["max_new_tokens"] = max(self.limits)
            kwargs["stopping_criteria"] = StoppingCriteriaList([*(kwargs.get("stopping_criteria") or []), RowBudget()])
            result = original_generate(*args, **kwargs)
            sequences = result.sequences.detach().cpu()
            if len(sequences) != len(self.limits):
                raise RuntimeError("TTS generation row count changed")
            eos = int(self.model.model.config.talker_config.codec_eos_token_id)
            for sequence, limit, count in zip(sequences, self.limits, self.counts):
                # Later EOS values can be synthetic padding after our row stop.
                positions = (sequence[:limit] == eos).nonzero().flatten().tolist()
                self.rows.append({"text_tokens": count, "count_basis": self.basis, "budget": limit,
                                  "generated_steps": positions[0] + 1 if positions else min(len(sequence), limit),
                                  "natural_eos": bool(positions),
                                  "reason": "eos" if positions else "missing_eos"})
            return result

        def decode(encoded: Any) -> tuple[list[Any], int]:
            items = encoded if isinstance(encoded, list) else [encoded]
            if len(items) != len(self.rows):
                raise RuntimeError("TTS decoder row count does not match generation diagnostics")
            rate = int(tokenizer.get_output_sample_rate())
            waveforms = []
            for item, row in zip(items, self.rows):
                if not row["natural_eos"]:
                    # Preserve indices for Qwen's clone-reference trimming wrapper.
                    waveforms.append(np.empty(0, dtype=np.float32))
                    continue
                decoded, item_rate = original_decode([item])
                if item_rate != rate or len(decoded) != 1:
                    raise RuntimeError("TTS decoder returned inconsistent audio")
                waveforms.append(decoded[0])
            return waveforms, rate

        talker.generate, tokenizer.decode = generate, decode
        try:
            yield
        finally:
            talker.generate, tokenizer.decode = original_generate, original_decode

    def validate(self, audio: list[Any]) -> list[bool]:
        import numpy as np
        if len(audio) != len(self.limits) or len(self.rows) != len(self.limits):
            raise RuntimeError("TTS returned an unexpected number of audio rows")
        valid = []
        for waveform, row in zip(audio, self.rows):
            good = bool(row["natural_eos"] and np.asarray(waveform).size and np.isfinite(waveform).all())
            if row["natural_eos"] and not good:
                row["reason"] = "invalid_waveform"
            valid.append(good)
        return valid

    def saved_rng(self) -> dict[str, Any]:
        return {key: base64.b64encode(value.numpy().tobytes()).decode("ascii") if hasattr(value, "numpy") else value for key, value in self.rng.items()}


class GenerationGuard:
    def __init__(self, context: Any, model: Any) -> None:
        self.context, self.model = context, model
        self.location = ("speech", 0)
        self.directory = context.output_dir.parent / "diagnostics" / "tts-generation" / str(int(context.job.get("attempts") or 1))
        self.path = self.directory / "ledger.json"
        # Corruption is an error, never a reason to silently reset retry counts.
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"version": 1, "chunks": {}}
        if self.state.get("version") != 1 or not isinstance(self.state.get("chunks"), dict):
            raise RuntimeError("Invalid TTS generation retry ledger")
        # Per-chunk files keep journal writes O(1) per generation, even for books
        # with thousands of chunks. Read legacy v1 snapshots without resetting them.
        for path in (self.directory / "chunks").glob("*.json"):
            if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                raise RuntimeError("Invalid TTS generation ledger key")
            row = json.loads(path.read_text())
            if not isinstance(row, dict) or type(row.get("retries")) is not int or not 0 <= row["retries"] <= MAX_RETRIES:
                raise RuntimeError("Invalid TTS generation retry counter")
            self.state["chunks"][path.stem] = row
        self.activity_sequence = 0
        self.progress_value = float(context.job.get("progress") or 0.01)
        self.reference_hashes: dict[str, str] = {}
        from audio_intel.model_registry import resolve_tts_checkpoint, resolve_tts_model
        request = context.job.get("request") or {}
        definition = resolve_tts_model(request.get("model"))
        checkpoint = resolve_tts_checkpoint(definition, request.get("voice_mode", "preset")) if definition else None
        self.revision = checkpoint["revision"] if checkpoint else None

    @contextmanager
    def activate(self) -> Iterator[GenerationGuard]:
        original_progress = self.context.progress

        def progress(value: float, *args: Any, **kwargs: Any) -> None:
            self.progress_value = max(self.progress_value, value)
            if (kwargs.get("activity") or {}).get("unit") == "codec_frame":
                kwargs["activity"] = {**kwargs["activity"], "sequence": max(1, self.activity_sequence)}
            original_progress(self.progress_value, *args, **kwargs)

        token = _active.set(self)
        self.context.progress = progress
        try:
            yield self
        finally:
            self.context.progress = original_progress
            _active.reset(token)

    def summary(self) -> dict[str, int]:
        rows = list(self.state["chunks"].values())
        return {"version": 1, "checked_chunks": len(rows),
                "retried_chunks": sum(row["retries"] > 0 for row in rows),
                "retry_attempts": sum(row["retries"] for row in rows),
                "recovered_chunks": sum(row["retries"] > 0 and row["accepted"] for row in rows)}

    def _save(self, keys: list[str]) -> None:
        if not self.path.exists():
            _write_json(self.path, {"version": 1, "chunks": {}})
        for key in keys:
            _write_json(self.directory / "chunks" / f"{key}.json", self.state["chunks"][key])

    def _reference(self, request: dict[str, Any]) -> dict[str, Any]:
        path = request.get("reference_audio_path")
        if path and path not in self.reference_hashes:
            self.reference_hashes[path] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return {"audio_sha256": self.reference_hashes.get(path), "text_sha256": _digest(request.get("reference_text") or ""),
                **{key: request.get(key) for key in ("id", "speaker", "instruct", "reference_start_seconds_used", "reference_end_seconds_used")}}

    def generate(self, invoke: Callable[..., tuple[list[Any], int]], request: dict[str, Any], texts: list[str],
                 clone_prompt: Any, progress_callback: Any, item_requests: list[dict[str, Any]] | None) -> tuple[list[Any], int]:
        import torch

        metadata = item_requests if item_requests is not None else [request] * len(texts)
        if len(metadata) != len(texts):
            raise ValueError("TTS batch metadata does not match the text batch")
        scope, index = self.location
        keys = [_digest(f"{scope}:{index + i}:{_digest(text)}") for i, text in enumerate(texts)]
        records = []
        for i, (key, text) in enumerate(zip(keys, texts)):
            row = self.state["chunks"].setdefault(key, {"scope": scope, "index": index + i,
                "text_sha256": _digest(text), "retries": 0, "accepted": False, "attempts": []})
            records.append(row)
        self.activity_sequence += 1
        attempt = GenerationAttempt(self.model, texts)
        with attempt.observe():
            audio, rate = invoke(self.model, request, texts, clone_prompt, progress_callback, item_requests)
        valid = attempt.validate(audio)
        for row, detail, accepted in zip(records, attempt.rows, valid):
            row["accepted"] = accepted
            row["attempts"].append(detail)
        self._save(keys)
        if not all(valid):
            fingerprint = secrets.token_hex(16)
            _write_json(self.directory / f"failure-{fingerprint}.json", {
                "version": 1, "model": request.get("model"), "model_revision": self.revision,
                "generation_config": self.model.generate_defaults,
                "chunk_keys": keys, "texts_sha256": [_digest(text) for text in texts],
                "references": [self._reference(item) for item in metadata], "rows": attempt.rows, "rng": attempt.saved_rng(),
            })
        for i, accepted in enumerate(valid):
            if accepted:
                continue
            row = records[i]
            retry_text = recovery_text(texts[i])
            while row["retries"] < MAX_RETRIES:
                row["retries"] += 1
                seed = secrets.randbits(63)
                row["attempts"].append({"retry": row["retries"], "seed": seed, "text_sha256": _digest(retry_text), "reason": "started"})
                self._save([keys[i]])
                self.activity_sequence += 1
                def report(current: int) -> None:
                    self.context.progress(self.progress_value, "tts_chunk_retry", row["retries"], MAX_RETRIES,
                        unit="attempt", activity={"sequence": self.activity_sequence, "current": current, "unit": "codec_frame", "basis": "observed"})
                report(0)  # Also checks cancellation before retry inference.
                reporter = ThrottledProgress(lambda value: report(value["current"]))
                device = self.model.model.device
                devices = [device.index if device.index is not None else torch.cuda.current_device()] if device.type == "cuda" else []
                retry_prompt = clone_prompt
                if isinstance(clone_prompt, list) and len(clone_prompt) == len(texts):
                    retry_prompt = [clone_prompt[i]]
                with torch.random.fork_rng(devices=devices):
                    torch.random.default_generator.manual_seed(seed)
                    for cuda_device in devices:
                        torch.cuda.default_generators[cuda_device].manual_seed(seed)
                    retry = GenerationAttempt(self.model, [retry_text])
                    with retry.observe():
                        recovered, new_rate = invoke(self.model, request, [retry_text], retry_prompt,
                            lambda current: reporter.report({"current": current}), [metadata[i]])
                    good = retry.validate(recovered)[0]
                row["attempts"][-1].update(retry.rows[0])
                row["accepted"] = good
                self._save([keys[i]])
                if new_rate != rate:
                    raise RuntimeError("TTS sample rate changed during recovery")
                if good:
                    audio[i] = recovered[0]
                    break
            if not row["accepted"]:
                raise TtsGenerationGuardError(f"TTS chunk {scope}:{index + i + 1} did not produce valid, naturally ended audio after {MAX_RETRIES} retries")
        return audio, rate


@contextmanager
def job_guard(context: Any, model: Any) -> Iterator[GenerationGuard | None]:
    if model is None:
        yield None
        return
    guard = GenerationGuard(context, model)
    try:
        with guard.activate():
            yield guard
    finally:
        # process_job releases GPU weights before releasing its GPU lease. Its
        # local guard variable must not keep those weights alive during cleanup.
        guard.model = None
