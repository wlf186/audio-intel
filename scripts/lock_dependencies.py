from __future__ import annotations

import argparse
import filecmp
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
UV = ROOT / ".runtime" / "bin" / ("uv.exe" if platform.system() == "Windows" else "uv")
ENVIRONMENTS = {
    "api": "requirements-api.txt",
    "asr": "requirements-asr.txt",
    "tts": "requirements-tts.txt",
    "aligner": "requirements-aligner.txt",
}
PLATFORMS = {
    "linux": "x86_64-unknown-linux-gnu",
    "windows": "x86_64-pc-windows-msvc",
}


def compile_lock(
    name: str, source: str, platform: str, output: Path, *, upgrade: bool = False,
) -> None:
    command = [
        str(UV), "pip", "compile", "--python-version", "3.12",
        "--python-platform", platform, "--generate-hashes", "--no-header",
        "--output-file", str(output), str(ROOT / source),
    ]
    if name != "api":
        command[3:3] = ["--torch-backend", "cu130"]
    if upgrade:
        command.insert(3, "--upgrade")
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate or verify pinned Python dependency locks.")
    parser.add_argument("--check", action="store_true", help="Fail if generated locks differ from the repository")
    parser.add_argument(
        "--upgrade", action="store_true",
        help="Upgrade all compatible dependencies instead of preserving versions from existing locks",
    )
    arguments = parser.parse_args()
    if arguments.check and arguments.upgrade:
        parser.error("--check and --upgrade cannot be used together")
    if not UV.is_file():
        raise SystemExit("Run ./service.sh setup api first so .runtime/bin/uv is available")
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="audio-intel-locks-") as temporary:
        temporary_root = Path(temporary)
        for os_name, platform in PLATFORMS.items():
            for name, source in ENVIRONMENTS.items():
                destination = ROOT / "requirements-lock" / os_name / f"{name}.txt"
                output = temporary_root / os_name / f"{name}.txt" if arguments.check else destination
                output.parent.mkdir(parents=True, exist_ok=True)
                if arguments.check and destination.is_file():
                    shutil.copyfile(destination, output)
                compile_lock(name, source, platform, output, upgrade=arguments.upgrade)
                if arguments.check and (not destination.is_file() or not filecmp.cmp(output, destination, shallow=False)):
                    failures.append(str(destination.relative_to(ROOT)))
    if failures:
        raise SystemExit("Dependency locks are stale: " + ", ".join(failures))


if __name__ == "__main__":
    main()
