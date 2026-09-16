from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil
import pytest


ROOT = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"
pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "win32"}, reason="native service platforms")


def clean_environment() -> dict[str, str]:
    # A developer's deployment and the CI mock settings must not mask file loading.
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AUDIO_INTEL_", "DOTENV_TEST_")) and key != "PYTHONPATH"
    }


def parse_file(tmp_path: Path, content: str | None, extra: dict[str, str] | None = None):
    env_file = tmp_path / ".env"
    if content is not None:
        env_file.write_bytes(content.encode("utf-8"))
    env = {**clean_environment(), **(extra or {})}
    if WINDOWS:
        harness = tmp_path / "parse.ps1"
        harness.write_text(
            "param($Helper, $EnvFile)\n"
            "$ErrorActionPreference = 'Stop'\n"
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding\n"
            ". $Helper\n"
            "Import-ServiceEnvironment $EnvFile | Out-Null\n"
            "$result = @{}\n"
            "foreach ($key in [Environment]::GetEnvironmentVariables('Process').Keys) {\n"
            "  if ($key.StartsWith('DOTENV_TEST_')) { $result[$key] = [Environment]::GetEnvironmentVariable($key) }\n"
            "}\n$result | ConvertTo-Json -Compress\n",
            encoding="utf-8",
        )
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness),
                   str(ROOT / "scripts/service_env.ps1"), str(env_file)]
    else:
        command = ["bash", "-c", 'set -euo pipefail; source "$1"; load_service_environment "$2"; '
                   '"$3" -c \'import json,os; print(json.dumps({k:v for k,v in os.environ.items() if k.startswith("DOTENV_TEST_")}))\'',
                   "dotenv-test", str(ROOT / "scripts/service_env.sh"), str(env_file), sys.executable]
    return subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)


def test_data_format_and_variable_precedence(tmp_path: Path) -> None:
    result = parse_file(tmp_path, "\ufeff# settings\r\n"
                        " export DOTENV_TEST_SEED = file\r\n"
                        "DOTENV_TEST_FIRST='  文本 # literal $DOTENV_TEST_SEED  '\r\n"
                        'DOTENV_TEST_SECOND="${DOTENV_TEST_SEED}/$DOTENV_TEST_MISSING" # note\r\n'
                        "DOTENV_TEST_DUP=first\r\nDOTENV_TEST_DUP=last\r\n"
                        "DOTENV_TEST_COPY=$DOTENV_TEST_DUP\r\n"
                        "DOTENV_TEST_SPACES=$DOTENV_TEST_FIRST # preserve referenced whitespace\r\n"
                        "DOTENV_TEST_PATH=C:\\new folder\\test\r\n"
                        "DOTENV_TEST_HASH=abc#def=ghi # comment\r\n"
                        'DOTENV_TEST_ESCAPE="say \\"hi\\" \\$DOTENV_TEST_SEED \\\\"\r\n'
                        "DOTENV_TEST_DOLLAR=$5\r\nDOTENV_TEST_EMPTY=ignored\r\n"
                        "DOTENV_TEST_LITERAL='$(not-a-command) `literal`'",
                        {"DOTENV_TEST_SEED": "external", "DOTENV_TEST_EMPTY": ""})
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert values["DOTENV_TEST_SEED"] == "external"
    assert values["DOTENV_TEST_FIRST"] == "  文本 # literal $DOTENV_TEST_SEED  "
    assert values["DOTENV_TEST_SECOND"] == "external/"
    assert values["DOTENV_TEST_COPY"] == "last"
    assert values["DOTENV_TEST_SPACES"] == values["DOTENV_TEST_FIRST"]
    assert values["DOTENV_TEST_PATH"] == r"C:\new folder\test"
    assert values["DOTENV_TEST_HASH"] == "abc#def=ghi"
    assert values["DOTENV_TEST_ESCAPE"] == 'say "hi" $DOTENV_TEST_SEED \\'
    assert values["DOTENV_TEST_DOLLAR"] == "$5"
    assert values.get("DOTENV_TEST_EMPTY", "") == ""
    assert values["DOTENV_TEST_LITERAL"] == "$(not-a-command) `literal`"


@pytest.mark.parametrize("assignment", [
    "missing-equals secret-do-not-print",
    "DOTENV_TEST_KEY='secret-do-not-print",
    'DOTENV_TEST_KEY="secret-do-not-print" trailing',
    "DOTENV_TEST_KEY=${secret-do-not-print:-fallback}",
    "DOTENV_TEST_KEY=$(touch secret-do-not-print)",
    "DOTENV_TEST_KEY=`touch secret-do-not-print`",
    "AUDIO_INTEL_LOAD_ENV=secret-do-not-print",
    "_ai_key=secret-do-not-print",
])
def test_invalid_files_fail_without_exposing_values(tmp_path: Path, assignment: str) -> None:
    result = parse_file(tmp_path, "# first line\n" + assignment)
    assert result.returncode != 0
    assert ".env:2" in result.stderr
    assert "secret-do-not-print" not in result.stdout + result.stderr
    assert not (tmp_path / "secret-do-not-print").exists()
    assert not (ROOT / "secret-do-not-print").exists()


@pytest.mark.parametrize("content,extra", [(None, {}), ("invalid secret", {"AUDIO_INTEL_LOAD_ENV": "0"})])
def test_missing_or_disabled_file(tmp_path: Path, content, extra) -> None:
    result = parse_file(tmp_path, content, extra)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {}


def stage_service(tmp_path: Path, *, runtime: bool = False) -> Path:
    stage = tmp_path / "project with spaces"
    stage.mkdir()
    for name in ("service.sh", "service.cmd", "service.ps1"):
        shutil.copy2(ROOT / name, stage / name)
    shutil.copytree(ROOT / "scripts", stage / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("audio_intel", "asr", "tts"):
        shutil.copytree(ROOT / name, stage / name, ignore=shutil.ignore_patterns("__pycache__"))
    if runtime:
        if WINDOWS:
            subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(stage / ".runtime"), str(ROOT / ".runtime")],
                           check=True, capture_output=True, text=True)
        else:
            (stage / ".runtime").symlink_to(ROOT / ".runtime", target_is_directory=True)
    return stage


def run_service(stage: Path, *args: str, extra: dict[str, str] | None = None):
    command = ([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(stage / "service.cmd")]
               if WINDOWS else [str(stage / "service.sh")])
    return subprocess.run([*command, *args], cwd=stage.parent, env={**clean_environment(), **(extra or {})},
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_status_loads_only_project_env_before_resolving_directories(tmp_path: Path) -> None:
    stage = stage_service(tmp_path)
    (tmp_path / ".env").write_text("AUDIO_INTEL_PORT=20899\n", encoding="utf-8")
    (stage / ".env").write_text("AUDIO_INTEL_PORT=20815\nAUDIO_INTEL_RUN_DIR=custom-run\n", encoding="utf-8")
    (stage / ".env.local-deploy").write_text("AUDIO_INTEL_PORT=20816\n", encoding="utf-8")
    result = run_service(stage, "status")
    assert result.returncode == 0, result.stderr
    assert "configured listener: 0.0.0.0:20815" in result.stdout
    assert str(stage / ".env") in result.stdout
    assert (stage / "custom-run").is_dir()
    assert not (stage / "run").exists()
    override = run_service(stage, "status", extra={"AUDIO_INTEL_PORT": "20817"})
    assert override.returncode == 0, override.stderr
    assert "configured listener: 0.0.0.0:20817" in override.stdout
    disabled = run_service(stage, "status", extra={"AUDIO_INTEL_LOAD_ENV": "0"})
    assert disabled.returncode == 0, disabled.stderr
    assert "configured listener: 0.0.0.0:20810" in disabled.stdout
    assert "disabled (AUDIO_INTEL_LOAD_ENV=0)" in disabled.stdout


def test_fresh_shell_lifecycle_and_authenticated_client_on_file_port(tmp_path: Path) -> None:
    stage = stage_service(tmp_path, runtime=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    settings = {
        "AUDIO_INTEL_HOST": "127.0.0.1", "AUDIO_INTEL_PORT": str(port),
        "AUDIO_INTEL_API_KEY": "isolated-dotenv-key", "AUDIO_INTEL_MOCK_MODE": "1",
        "AUDIO_INTEL_RUN_DIR": "custom-run", "AUDIO_INTEL_DATA_DIR": "custom-data",
        "AUDIO_INTEL_FRONTEND_DIR": str(ROOT / "frontend/dist"), "AUDIO_INTEL_MIN_FREE_DISK_BYTES": "0",
    }
    (stage / ".env").write_text("".join(f"{key}='{value}'\n" for key, value in settings.items()), encoding="utf-8")
    tracked: list[tuple[int, float]] = []
    try:
        started = run_service(stage, "start", "all")
        assert started.returncode == 0, started.stdout + started.stderr
        for component in ("api", "asr", "tts"):
            proc = psutil.Process(int((stage / "custom-run" / f"{component}.pid").read_text()))
            tracked.append((proc.pid, proc.create_time()))
        status = run_service(stage, "status")
        assert status.returncode == 0, status.stderr
        assert f"http://127.0.0.1:{port}" in status.stdout
        base_url = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(base_url + "/api/v1/health", timeout=10) as response:
            assert response.status == 200
        with pytest.raises(urllib.error.HTTPError) as unauthenticated:
            urllib.request.urlopen(base_url + "/api/v1/capabilities", timeout=10)
        assert unauthenticated.value.code == 401
        request = urllib.request.Request(base_url + "/api/v1/capabilities",
                                         headers={"Authorization": "Bearer isolated-dotenv-key"})
        with urllib.request.urlopen(request, timeout=10) as response:
            assert "tts" in json.load(response)
        if not WINDOWS:
            # Execute the published example against a nondefault, authenticated mock instance.
            blocks = re.findall(r"```bash\n(.*?)```", (ROOT / "docs/API.md").read_text(encoding="utf-8"), re.S)
            submit = next(block for block in blocks if "TTS_KEY=" in block and "speaker=Ryan" in block)
            client = subprocess.run(["bash", "-e", "-c", blocks[0] + "\n" + submit], cwd=stage,
                                    env={**clean_environment(), "AUDIO_INTEL_BASE_URL": base_url,
                                         "AUDIO_INTEL_API_KEY": "isolated-dotenv-key"},
                                    capture_output=True, text=True, timeout=15)
            assert client.returncode == 0, client.stderr
            job_id = json.loads(client.stdout)["id"]
            for _ in range(100):
                request = urllib.request.Request(base_url + "/api/v1/jobs/" + job_id,
                                                 headers={"Authorization": "Bearer isolated-dotenv-key"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    job = json.load(response)
                if job["state"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.1)
            assert job["state"] == "succeeded", job.get("error_message")
        repeated = run_service(stage, "start", "all", extra={"AUDIO_INTEL_PORT": "20817"})
        assert repeated.returncode == 0, repeated.stderr
        assert "use restart to apply it" in repeated.stdout
        assert int((stage / "custom-run/api.pid").read_text()) == tracked[0][0]
        restarted = run_service(stage, "restart", "all")
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        for pid, created in tracked:
            assert not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created
        stopped = run_service(stage, "stop", "all")
        assert stopped.returncode == 0, stopped.stderr
        assert not list((stage / "custom-run").glob("*.pid"))
    finally:
        run_service(stage, "stop", "all")


def test_doctor_checks_configured_listener(tmp_path: Path) -> None:
    stage = stage_service(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        (stage / ".env").write_text(f"AUDIO_INTEL_HOST=127.0.0.1\nAUDIO_INTEL_PORT={port}\n", encoding="utf-8")
        result = run_service(stage, "doctor")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["listener"]["host"] == "127.0.0.1"
    assert report["listener"]["port"] == port
    assert report["listener"]["status"].startswith("in use")
    assert "port_20810" not in report


def test_tls_restart_does_not_reload_file_overrides(tmp_path: Path) -> None:
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is unavailable")
    stage = stage_service(tmp_path, runtime=True)
    cert, key = stage / "server.pem", stage / "server-key.pem"
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
                    "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)],
                   check=True, capture_output=True, text=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (stage / ".env").write_text(
        f"AUDIO_INTEL_HOST=127.0.0.1\nAUDIO_INTEL_PORT={port}\nAUDIO_INTEL_MOCK_MODE=1\n"
        f"AUDIO_INTEL_FRONTEND_DIR='{ROOT / 'frontend/dist'}'\nAUDIO_INTEL_PROTOCOL=https\n"
        f"AUDIO_INTEL_TLS_CERT_FILE='{cert}'\nAUDIO_INTEL_TLS_KEY_FILE='{key}'\n",
        encoding="utf-8",
    )
    try:
        started = run_service(stage, "start", "api")
        assert started.returncode == 0, started.stdout + started.stderr
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/api/v1/health",
                                    context=ssl._create_unverified_context(), timeout=10) as response:
            assert response.status == 200
        switched = run_service(stage, "tls", "disable", "--restart")
        assert switched.returncode == 0, switched.stdout + switched.stderr
        assert switched.stdout.count("started ") == 3
        assert f"http://127.0.0.1:{port}" in switched.stdout
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=10) as response:
            assert response.status == 200
        status = run_service(stage, "status")
        assert "running protocol differs" in status.stdout
        assert "AUDIO_INTEL_PROTOCOL=https" in (stage / ".env").read_text(encoding="utf-8")
    finally:
        run_service(stage, "stop", "all")
