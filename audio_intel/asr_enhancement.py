"""Lightweight enhancement contract; importing this module never loads inference."""
from __future__ import annotations

from typing import Any
import os
import math

from .model_registry import asr_denoise_model, asr_enhancement_model, model_installation


ENHANCEMENT_DEFAULT = "auto"
STRENGTH_FIELDS = ("speech_enhancement_strength", "background_denoise_strength")
CONTROL_FIELDS = ("speech_enhancement", "background_denoise", "enhancement_policy_version", "denoise_backend", *STRENGTH_FIELDS)


class StrengthConflict(ValueError):
    """A saved historical request cannot acquire new processing controls."""


def strength_capabilities(backend: str) -> dict[str, dict[str, Any]]:
    return {
        "speech_enhancement": {"min": 0.0, "max": 2.0, "step": 0.01, "default": 1.0, "unit": "gain_db_multiplier"},
        "background_denoise": {"min": 0.0, "max": 1.0, "step": 0.01,
                               "default": 1.0 if backend == "ulunas" else 0.5, "unit": "mix_ratio"},
    }


def validate_strength(value: float, maximum: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= maximum:
        raise ValueError(f"Enhancement strength must be finite and between 0 and {maximum:g}")
    return float(value) if value else 0.0


def denoise_backend() -> str:
    backend = os.getenv("AUDIO_INTEL_ASR_DENOISE_BACKEND", "gtcrn").strip().lower()
    if backend not in {"ulunas", "gtcrn"}:
        raise ValueError("AUDIO_INTEL_ASR_DENOISE_BACKEND must be ulunas or gtcrn")
    return backend


def processing_key(request: dict[str, Any]) -> tuple[Any, ...]:
    legacy = request.get("audio_enhancement", "off") == "auto"
    key = (request.get("speech_enhancement", legacy), request.get("background_denoise", legacy),
           request.get("enhancement_policy_version", 4 if legacy else 0), request.get("denoise_backend", "gtcrn"))
    return key + tuple(request[name] for name in STRENGTH_FIELDS) if request.get("enhancement_policy_version") == 6 else key


def request_controls(value: str | None, speech: bool | None, noise: bool | None,
                     existing: dict[str, Any] | None = None, *,
                     speech_enhancement_strength: float | None = None,
                     background_denoise_strength: float | None = None) -> dict[str, Any]:
    if value is not None and (speech is not None or noise is not None):
        raise ValueError("audio_enhancement cannot be combined with speech_enhancement or background_denoise")
    mode = request_mode(value, existing)
    saved = (existing or {}).get("request") or {}
    supplied = (speech_enhancement_strength, background_denoise_strength)
    for strength_value, maximum in zip(supplied, (2.0, 1.0)):
        if strength_value is not None:
            validate_strength(strength_value, maximum)
    if existing and saved.get("enhancement_policy_version") != 6 and any(value is not None for value in supplied):
        raise StrengthConflict("Historical enhancement requests retain their strengths; submit a new Idempotency-Key")
    old_on = saved.get("audio_enhancement", "off") == "auto"
    gain = speech if speech is not None else saved.get("speech_enhancement", old_on if existing else True)
    denoise = noise if noise is not None else saved.get("background_denoise", old_on if existing else True)
    if value is not None:
        gain = denoise = mode == "auto"
    mode = "auto" if gain or denoise else "off"
    if existing and "enhancement_policy_version" not in saved:
        if gain == denoise == old_on:
            return {"audio_enhancement": mode}
    backend = saved.get("denoise_backend") or denoise_backend()
    result = {"audio_enhancement": mode, "speech_enhancement": gain, "background_denoise": denoise,
              "enhancement_policy_version": saved.get("enhancement_policy_version", 5) if existing else 6,
              "denoise_backend": backend}
    if result["enhancement_policy_version"] == 6:
        defaults = strength_capabilities(backend)
        for name, value, control in zip(STRENGTH_FIELDS, supplied, ("speech_enhancement", "background_denoise")):
            result[name] = validate_strength(value if value is not None else saved.get(name, defaults[control]["default"]), defaults[control]["max"])
    return result


def request_mode(value: str | None, existing: dict[str, Any] | None = None) -> str:
    if value is not None:
        if value not in {"auto", "off"}:
            raise ValueError("audio_enhancement must be auto or off")
        return value
    if existing is not None:
        return (existing.get("request") or {}).get("audio_enhancement", "off")
    return ENHANCEMENT_DEFAULT


def capabilities(config: Any) -> dict[str, Any]:
    backend = denoise_backend()
    model = asr_denoise_model(backend)
    strengths = strength_capabilities(backend)
    installed = model_installation(config.models_dir, model)["installed"]
    return {
        "supported": True, "available": bool(installed or config.mock_mode),
        "default": ENHANCEMENT_DEFAULT, "modes": ["auto", "off"],
        "model": model["name"], "model_revision": model["revision"],
        "compute_device": "cpu", "preserves_all_speakers": True,
        "enhanced_playback": True,
        "speech_enhancement": {"supported": True, "available": True, "default": True, "strength": strengths["speech_enhancement"]},
        "background_denoise": {"supported": True, "available": bool(installed or config.mock_mode), "default": True,
                               "model": model["name"], "model_revision": model["revision"], "strength": strengths["background_denoise"]},
    }
