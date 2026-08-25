from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audio_intel.config import settings
from audio_intel.model_registry import model_installation, models_for

MODELS = settings.models_dir
CACHE = settings.cache_dir


def hf(repo: str, directory: str, revision: str) -> None:
    target = MODELS / directory
    marker = target / ".complete"
    from huggingface_hub import snapshot_download
    print(f"[models] downloading {repo}@{revision} -> {target}")
    snapshot_download(repo_id=repo, revision=revision, local_dir=target, cache_dir=CACHE / "huggingface")
    temporary = marker.with_suffix(".partial")
    temporary.write_text(revision + "\n", encoding="utf-8")
    temporary.replace(marker)


def modelscope(repo: str, directory: str, revision: str) -> None:
    target = MODELS / directory
    marker = target / ".complete"
    from modelscope import snapshot_download
    print(f"[models] downloading {repo}@{revision} -> {target}")
    snapshot_download(repo, revision=revision, local_dir=str(target), cache_dir=str(CACHE / "modelscope"))
    temporary = marker.with_suffix(".partial")
    temporary.write_text(revision + "\n", encoding="utf-8")
    temporary.replace(marker)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=("all", "asr", "tts"), nargs="?", default="all")
    target = parser.parse_args().target
    MODELS.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(CACHE / "huggingface"))
    os.environ.setdefault("MODELSCOPE_CACHE", str(CACHE / "modelscope"))
    for model in models_for(target):
        installation = model_installation(MODELS, model)
        if installation["installed"]:
            print(f"[models] {model['name']}@{model['revision'][:12]}: already installed")
            continue
        if installation["state"] != "missing":
            print(f"[models] {model['name']}: repairing {installation['state']}")
        downloader = hf if model["provider"] == "huggingface" else modelscope
        downloader(model["repository"], model["name"], model["revision"])


if __name__ == "__main__":
    main()
