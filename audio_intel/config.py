from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else (ROOT / value).resolve()


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = _path("AUDIO_INTEL_DATA_DIR", "data")
    temp_dir: Path = _path("AUDIO_INTEL_TEMP_DIR", "tmp")
    cache_dir: Path = _path("AUDIO_INTEL_CACHE_DIR", "cache")
    log_dir: Path = _path("AUDIO_INTEL_LOG_DIR", "logs")
    run_dir: Path = _path("AUDIO_INTEL_RUN_DIR", "run")
    models_dir: Path = _path("AUDIO_INTEL_MODELS_DIR", "models")
    frontend_dir: Path = _path("AUDIO_INTEL_FRONTEND_DIR", "frontend/dist")
    host: str = os.getenv("AUDIO_INTEL_HOST", "0.0.0.0")
    port: int = int(os.getenv("AUDIO_INTEL_PORT", "20810"))
    api_key: str = os.getenv("AUDIO_INTEL_API_KEY", "").strip()
    enabled_services: frozenset[str] = frozenset(
        item.strip() for item in os.getenv("AUDIO_INTEL_SERVICES", "asr,tts").split(",") if item.strip()
    )
    max_upload_bytes: int = int(os.getenv("AUDIO_INTEL_MAX_UPLOAD_BYTES", str(4 * 1024**3)))
    max_tts_chars: int = int(os.getenv("AUDIO_INTEL_MAX_TTS_CHARS", "50000"))
    max_queued_asr: int = int(os.getenv("AUDIO_INTEL_MAX_QUEUED_ASR", "5"))
    max_queued_tts: int = int(os.getenv("AUDIO_INTEL_MAX_QUEUED_TTS", "5"))
    max_concurrent_submissions: int = int(
        os.getenv("AUDIO_INTEL_MAX_CONCURRENT_SUBMISSIONS", "2")
    )
    min_free_disk_bytes: int = int(
        os.getenv("AUDIO_INTEL_MIN_FREE_DISK_BYTES", str(5 * 1024**3))
    )
    worker_poll_seconds: float = float(os.getenv("AUDIO_INTEL_WORKER_POLL_SECONDS", "1"))
    cancel_grace_seconds: float = float(os.getenv("AUDIO_INTEL_CANCEL_GRACE_SECONDS", "1"))
    executor_idle_seconds: float = max(
        0.0, float(os.getenv("AUDIO_INTEL_EXECUTOR_IDLE_SECONDS", "60"))
    )
    mock_mode: bool = os.getenv("AUDIO_INTEL_MOCK_MODE", "0").lower() in {"1", "true", "yes"}

    @property
    def database_path(self) -> Path:
        return self.data_dir / "audio_intel.sqlite3"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def voices_dir(self) -> Path:
        return self.data_dir / "voices"

    @property
    def voiceprints_dir(self) -> Path:
        return self.data_dir / "voiceprints"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.jobs_dir,
            self.voices_dir,
            self.voiceprints_dir,
            self.temp_dir,
            self.cache_dir,
            self.log_dir,
            self.run_dir,
            self.models_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
