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
sys.path.insert(0, str(ROOT))

from audio_intel.model_registry import model_installation, model_manifest
from audio_intel.deployment import deployment_profile_path, read_deployment_profile


def runtime_python(name: str) -> Path:
    if platform.system() == "Windows":
        return ROOT / ".runtime" / name / "Scripts" / "python.exe"
    return ROOT / ".runtime" / name / "bin" / "python"


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


def runtime_profile(name: str) -> str:
    python = runtime_python(name)
    if not python.is_file():
        return "missing"
    try:
        return subprocess.run(
            [str(python), str(ROOT / "scripts" / "runtime_profile.py"), "detect"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except Exception as exc:
        return f"unavailable: {exc}"


system = platform.system()
machine = platform.machine().lower()
report = {
    "root": str(ROOT),
    "python": sys.version.split()[0],
    "platform": {"system": platform.system(), "machine": platform.machine()},
    "supported_platform": system in {"Linux", "Windows"} and machine in {"x86_64", "amd64"},
    "tools": {name: command_version(name) for name in ("git", "curl", "node", "corepack")},
    "memory_total_bytes": memory_total(),
    "disk_free_bytes": shutil.disk_usage(ROOT).free,
    "download_proxy_configured": any(os.getenv(name) for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")),
    "port_20810": check_port(),
    "deployment_profile": read_deployment_profile(ROOT),
    "deployment_profile_path": str(deployment_profile_path(ROOT)),
    "ffmpeg_required": False,
    "nvidia_smi": shutil.which("nvidia-smi") is not None,
    "runtime_environments": {name: runtime_python(name).is_file() for name in ("api", "asr", "tts", "aligner")},
    "inference_runtime_profiles": {name: runtime_profile(name) for name in ("asr", "tts", "aligner")},
    "frontend": (ROOT / "frontend/dist/index.html").is_file(),
    "api_docs_local_assets": all(
        (ROOT / "frontend/dist/docs-assets" / name).is_file()
        for name in ("swagger-ui-bundle.js", "swagger-ui.css")
    ),
    "models": {
        model["name"]: {
            key: value for key, value in model_installation(ROOT / "models", model).items()
            if key != "marker"
        }
        for model in model_manifest()
    },
}
try:
    output = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True, timeout=3, check=True)
    report["gpu"] = output.stdout.strip()
except Exception as exc:
    report["gpu"] = f"unavailable: {exc}"
print(json.dumps(report, ensure_ascii=False, indent=2))
