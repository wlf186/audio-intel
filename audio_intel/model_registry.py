from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")


def model_manifest() -> list[dict[str, Any]]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("models"), list):
        raise RuntimeError(f"Unsupported model manifest: {MANIFEST_PATH}")
    return payload["models"]


def models_for(target: str) -> list[dict[str, Any]]:
    return [item for item in model_manifest() if target == "all" or target in item["targets"]]


def asr_models() -> list[dict[str, Any]]:
    return [item for item in models_for("asr") if item.get("public_id")]


def default_asr_model() -> dict[str, Any]:
    defaults = [item for item in asr_models() if item.get("default") is True]
    if len(defaults) != 1:
        raise RuntimeError("The model manifest must declare exactly one default ASR model")
    return defaults[0]


def resolve_asr_model(identifier: str | None) -> dict[str, Any] | None:
    value = (identifier or default_asr_model()["public_id"]).strip()
    normalized = value.casefold()
    for model in asr_models():
        candidates = [model["public_id"], model["repository"], *model.get("aliases", [])]
        if any(str(candidate).casefold() == normalized for candidate in candidates):
            return model
    return None


def tts_checkpoints() -> list[dict[str, Any]]:
    return [item for item in models_for("tts") if item.get("tts_model_id")]


def tts_models() -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for checkpoint in tts_checkpoints():
        identifier = str(checkpoint["tts_model_id"])
        model = grouped.setdefault(identifier, {
            "public_id": identifier,
            "name": checkpoint["tts_model_name"],
            "default": bool(checkpoint.get("tts_default")),
            "minimum_gpu_memory_mib": int(checkpoint.get("minimum_gpu_memory_mib") or 0),
            "batch_penalty_steps": int(checkpoint.get("batch_penalty_steps") or 0),
            "checkpoints": {},
            "aliases": [],
        })
        if (
            model["name"] != checkpoint["tts_model_name"]
            or model["default"] != bool(checkpoint.get("tts_default"))
            or model["minimum_gpu_memory_mib"] != int(checkpoint.get("minimum_gpu_memory_mib") or 0)
            or model["batch_penalty_steps"] != int(checkpoint.get("batch_penalty_steps") or 0)
        ):
            raise RuntimeError(f"Inconsistent TTS model metadata for {identifier}")
        variant = str(checkpoint["tts_variant"])
        if variant in model["checkpoints"]:
            raise RuntimeError(f"Duplicate TTS checkpoint variant {identifier}/{variant}")
        model["checkpoints"][variant] = checkpoint
        model["aliases"].extend([checkpoint["repository"], *checkpoint.get("aliases", [])])
    return list(grouped.values())


def default_tts_model() -> dict[str, Any]:
    defaults = [item for item in tts_models() if item["default"]]
    if len(defaults) != 1:
        raise RuntimeError("The model manifest must declare exactly one default TTS model")
    return defaults[0]


def resolve_tts_model(identifier: str | None) -> dict[str, Any] | None:
    value = (identifier or default_tts_model()["public_id"]).strip().casefold()
    for model in tts_models():
        candidates = [model["public_id"], *model["aliases"]]
        if any(str(candidate).casefold() == value for candidate in candidates):
            return model
    return None


def tts_variant_for_voice_mode(voice_mode: str) -> str | None:
    if voice_mode == "preset":
        return "custom_voice"
    if voice_mode == "voice_design":
        return "voice_design"
    if voice_mode in {"profile", "inline_clone", "voiceprint"}:
        return "base"
    return None


def resolve_tts_checkpoint(model: dict[str, Any], voice_mode: str) -> dict[str, Any] | None:
    variant = tts_variant_for_voice_mode(voice_mode)
    return model["checkpoints"].get(variant) if variant else None


def model_installation(models_dir: Path, model: dict[str, Any]) -> dict[str, Any]:
    directory = models_dir / model["name"]
    marker = directory / ".complete"
    missing_files = [
        name for name in model.get("required_files", [])
        if not (directory / name).is_file() or (directory / name).stat().st_size == 0
    ]
    if not marker.is_file():
        state = "missing"
        actual_revision = None
    else:
        actual_revision = marker.read_text(encoding="utf-8").strip()
        if not actual_revision:
            state = "empty_marker"
        elif actual_revision != model["revision"]:
            state = "revision_mismatch"
        elif missing_files:
            state = "incomplete"
        else:
            state = "installed"
    return {
        "installed": state == "installed",
        "state": state,
        "revision": model["revision"],
        "actual_revision": actual_revision,
        "missing_files": missing_files,
        "marker": marker,
    }


def target_ready(models_dir: Path, target: str) -> bool:
    return all(model_installation(models_dir, model)["installed"] for model in models_for(target))
