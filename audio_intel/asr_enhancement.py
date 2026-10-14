"""Lightweight enhancement contract; importing this module never loads inference."""
from __future__ import annotations

from typing import Any

from .model_registry import asr_enhancement_model, model_installation


ENHANCEMENT_DEFAULT = "auto"


def request_mode(value: str | None, existing: dict[str, Any] | None = None) -> str:
    if value is not None:
        if value not in {"auto", "off"}:
            raise ValueError("audio_enhancement must be auto or off")
        return value
    if existing is not None:
        return (existing.get("request") or {}).get("audio_enhancement", "off")
    return ENHANCEMENT_DEFAULT


def capabilities(config: Any) -> dict[str, Any]:
    model = asr_enhancement_model()
    installed = model_installation(config.models_dir, model)["installed"]
    return {
        "supported": True, "available": bool(installed or config.mock_mode),
        "default": ENHANCEMENT_DEFAULT, "modes": ["auto", "off"],
        "model": model["name"], "model_revision": model["revision"],
        "compute_device": "cpu", "preserves_all_speakers": True,
        "enhanced_playback": True,
    }
