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
