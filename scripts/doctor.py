from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def command_version(command: str, argument: str = "--version") -> str | None:
    if shutil.which(command) is None:
        return None
    try:
        return subprocess.run(
            [command, argument], capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip().splitlines()[0]
    except Exception:
        return "installed (version query failed)"


def memory_total() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def check_port() -> str:
    sock = socket.socket()
    try:
        sock.bind(("0.0.0.0", 20810))
        return "available"
    except OSError:
        return "in use (expected if the service is running)"
    finally:
        sock.close()


report = {
    "root": str(ROOT),
    "python": sys.version.split()[0],
    "platform": {"system": platform.system(), "machine": platform.machine()},
    "supported_platform": platform.system() == "Linux" and platform.machine() == "x86_64",
    "tools": {name: command_version(name) for name in ("git", "curl", "node", "corepack")},
    "memory_total_bytes": memory_total(),
    "disk_free_bytes": shutil.disk_usage(ROOT).free,
    "download_proxy_configured": any(os.getenv(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")),
    "port_20810": check_port(),
    "ffmpeg_required": False,
    "nvidia_smi": shutil.which("nvidia-smi") is not None,
    "runtime_environments": {name: (ROOT / ".runtime" / name / "bin/python").is_file() for name in ("api", "asr", "tts")},
    "frontend": (ROOT / "frontend/dist/index.html").is_file(),
    "models": {name: (ROOT / "models" / name / ".complete").is_file() for name in (
        "Qwen3-ASR-0.6B", "Qwen3-ForcedAligner-0.6B", "FSMN-VAD", "CAM++",
        "Qwen3-TTS-12Hz-0.6B-Base", "Qwen3-TTS-12Hz-0.6B-CustomVoice",
    )},
}
try:
    output = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=3, check=True)
    report["gpu"] = output.stdout.strip()
except Exception as exc:
    report["gpu"] = f"unavailable: {exc}"
print(json.dumps(report, ensure_ascii=False, indent=2))
