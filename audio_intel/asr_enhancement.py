"""Lightweight enhancement contract; importing this module never loads inference."""
from __future__ import annotations

from typing import Any
import os

from .model_registry import asr_denoise_model, asr_enhancement_model, model_installation


ENHANCEMENT_DEFAULT = "auto"
CONTROL_FIELDS = ("speech_enhancement", "background_denoise", "enhancement_policy_version", "denoise_backend")


def denoise_backend() -> str:
    backend = os.getenv("AUDIO_INTEL_ASR_DENOISE_BACKEND", "gtcrn").strip().lower()
    if backend not in {"ulunas", "gtcrn"}:
        raise ValueError("AUDIO_INTEL_ASR_DENOISE_BACKEND must be ulunas or gtcrn")
    return backend


def processing_key(request: dict[str, Any]) -> tuple[Any, ...]:
    legacy = request.get("audio_enhancement", "off") == "auto"
    return (request.get("speech_enhancement", legacy), request.get("background_denoise", legacy),
            request.get("enhancement_policy_version", 4 if legacy else 0), request.get("denoise_backend", "gtcrn"))


def request_controls(value: str | None, speech: bool | None, noise: bool | None,
                     existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is not None and (speech is not None or noise is not None):
        raise ValueError("audio_enhancement cannot be combined with speech_enhancement or background_denoise")
    mode = request_mode(value, existing)
    saved = (existing or {}).get("request") or {}
    old_on = saved.get("audio_enhancement", "off") == "auto"
    gain = speech if speech is not None else saved.get("speech_enhancement", old_on if existing else True)
    denoise = noise if noise is not None else saved.get("background_denoise", old_on if existing else True)
    if value is not None:
        gain = denoise = mode == "auto"
    mode = "auto" if gain or denoise else "off"
    if existing and "enhancement_policy_version" not in saved:
        if gain == denoise == old_on:
            return {"audio_enhancement": mode}
    return {"audio_enhancement": mode, "speech_enhancement": gain, "background_denoise": denoise,
            "enhancement_policy_version": 5, "denoise_backend": saved.get("denoise_backend") or denoise_backend()}


def request_mode(value: str | None, existing: dict[str, Any] | None = None) -> str:
    if value is not None:
        if value not in {"auto", "off"}:
            raise ValueError("audio_enhancement must be auto or off")
        return value
    if existing is not None:
        return (existing.get("request") or {}).get("audio_enhancement", "off")
    return ENHANCEMENT_DEFAULT


def capabilities(config: Any) -> dict[str, Any]:
    model = asr_denoise_model(denoise_backend())
    installed = model_installation(config.models_dir, model)["installed"]
    return {
        "supported": True, "available": bool(installed or config.mock_mode),
        "default": ENHANCEMENT_DEFAULT, "modes": ["auto", "off"],
        "model": model["name"], "model_revision": model["revision"],
        "compute_device": "cpu", "preserves_all_speakers": True,
        "enhanced_playback": True,
        "speech_enhancement": {"supported": True, "available": True, "default": True},
        "background_denoise": {"supported": True, "available": bool(installed or config.mock_mode), "default": True,
                               "model": model["name"], "model_revision": model["revision"]},
    }
