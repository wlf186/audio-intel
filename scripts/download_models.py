from __future__ import annotations

import argparse
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
CACHE = ROOT / "cache"

HF_MODELS = {
    "Qwen3-ASR-0.6B": ("Qwen/Qwen3-ASR-0.6B", "5eb144179a02acc5e5ba31e748d22b0cf3e303b0"),
    "Qwen3-ForcedAligner-0.6B": ("Qwen/Qwen3-ForcedAligner-0.6B", "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"),
    "Qwen3-TTS-12Hz-0.6B-Base": ("Qwen/Qwen3-TTS-12Hz-0.6B-Base", "5d83992436eae1d760afd27aff78a71d676296fc"),
    "Qwen3-TTS-12Hz-0.6B-CustomVoice": ("Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice", "85e237c12c027371202489a0ec509ded67b5e4b5"),
}
MODELSCOPE_MODELS = {
    "FSMN-VAD": ("iic/speech_fsmn_vad_zh-cn-16k-common-pytorch", "v2.0.4"),
    "CAM++": ("iic/speech_campplus_sv_zh-cn_16k-common", "v2.0.2"),
}


def _installed(marker: Path, revision: str) -> bool:
    return marker.is_file() and marker.read_text(encoding="utf-8").strip() == revision


def hf(repo: str, directory: str, revision: str) -> None:
    target = MODELS / directory
    marker = target / ".complete"
    if _installed(marker, revision):
        print(f"[models] {directory}@{revision[:12]}: already installed")
        return
    from huggingface_hub import snapshot_download
    print(f"[models] downloading {repo}@{revision} -> {target}")
    snapshot_download(repo_id=repo, revision=revision, local_dir=target, cache_dir=CACHE / "huggingface")
    marker.write_text(revision + "\n", encoding="utf-8")


def modelscope(repo: str, directory: str, revision: str) -> None:
    target = MODELS / directory
    marker = target / ".complete"
    if _installed(marker, revision):
        print(f"[models] {directory}@{revision}: already installed")
        return
    from modelscope import snapshot_download
    print(f"[models] downloading {repo}@{revision} -> {target}")
    snapshot_download(repo, revision=revision, local_dir=str(target), cache_dir=str(CACHE / "modelscope"))
    marker.write_text(revision + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=("all", "asr", "tts"), nargs="?", default="all")
    target = parser.parse_args().target
    MODELS.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(CACHE / "huggingface"))
    os.environ.setdefault("MODELSCOPE_CACHE", str(CACHE / "modelscope"))
    if target in {"all", "asr"}:
        for directory in ("Qwen3-ASR-0.6B", "Qwen3-ForcedAligner-0.6B"):
            repo, revision = HF_MODELS[directory]
            hf(repo, directory, revision)
        for directory in ("FSMN-VAD", "CAM++"):
            repo, revision = MODELSCOPE_MODELS[directory]
            modelscope(repo, directory, revision)
    if target in {"all", "tts"}:
        for directory in (
            "Qwen3-TTS-12Hz-0.6B-Base", "Qwen3-TTS-12Hz-0.6B-CustomVoice",
            "Qwen3-ForcedAligner-0.6B",
        ):
            repo, revision = HF_MODELS[directory]
            hf(repo, directory, revision)


if __name__ == "__main__":
    main()
