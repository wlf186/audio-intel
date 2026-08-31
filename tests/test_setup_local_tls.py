from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "setup_local_tls.py"
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("openssl"), reason="fake mkcert uses POSIX openssl")


def _fake_mkcert(tmp_path: Path) -> tuple[Path, Path]:
    executable = tmp_path / "mkcert"
    log = tmp_path / "mkcert-args.txt"
    executable.write_text("""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$MKCERT_TEST_LOG"
cert=''; key=''; hosts=()
while (($#)); do
  case "$1" in
    -cert-file) cert="$2"; shift 2 ;;
    -key-file) key="$2"; shift 2 ;;
    *) hosts+=("$1"); shift ;;
  esac
done
mkdir -p "$CAROOT"
if [[ ! -f "$CAROOT/rootCA.pem" ]]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj '/CN=Audio Intel Test Root' -keyout "$CAROOT/rootCA-key.pem" -out "$CAROOT/rootCA.pem" >/dev/null 2>&1
fi
san=''
for host in "${hosts[@]}"; do
  kind='DNS'
  [[ "$host" == *:* || "$host" =~ ^[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+$ ]] && kind='IP'
  [[ -z "$san" ]] || san+=','
  san+="$kind:$host"
done
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj '/CN=localhost' -addext "subjectAltName=$san" -keyout "$key" -out "$cert" >/dev/null 2>&1
""", encoding="utf-8")
    executable.chmod(0o755)
    return executable, log


def _run(action: str, tls_dir: Path, env: dict[str, str], *hosts: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(SCRIPT), action, "--tls-dir", str(tls_dir)]
    for host in hosts:
        command.extend(("--host", host))
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, check=False)


def test_create_and_renew_use_project_ca_without_installing_it(tmp_path: Path) -> None:
    executable, log = _fake_mkcert(tmp_path)
    tls_dir = tmp_path / "tls"
    env = {
        **os.environ, "PATH": f"{executable.parent}{os.pathsep}{os.environ['PATH']}",
        "MKCERT_TEST_LOG": str(log),
    }
    created = _run("create", tls_dir, env, "192.0.2.10")
    assert created.returncode == 0, created.stderr
    expected = [
        tls_dir / "server.pem", tls_dir / "server-key.pem",
        tls_dir / "audio-intel-root-ca.pem", tls_dir / "audio-intel-root-ca.cer",
        tls_dir / "ca" / "rootCA.pem", tls_dir / "ca" / "rootCA-key.pem",
    ]
    assert all(path.is_file() for path in expected)
    assert "Root CA SHA-256:" in created.stdout
    assert "-install" not in log.read_text(encoding="utf-8")
    assert "192.0.2.10" in log.read_text(encoding="utf-8")
    assert "localhost" in log.read_text(encoding="utf-8")
    assert "127.0.0.1" in log.read_text(encoding="utf-8")
    assert "::1" in log.read_text(encoding="utf-8")
    assert stat.S_IMODE((tls_dir / "server-key.pem").stat().st_mode) == 0o600
    assert stat.S_IMODE((tls_dir / "ca" / "rootCA-key.pem").stat().st_mode) == 0o600

    root_before = (tls_dir / "ca" / "rootCA.pem").read_bytes()
    fingerprint_before = _run("fingerprint", tls_dir, env).stdout.strip()
    refused = _run("create", tls_dir, env, "192.0.2.10")
    assert refused.returncode == 2
    assert "tls renew" in refused.stderr

    renewed = _run("renew", tls_dir, env, "192.0.2.11")
    assert renewed.returncode == 0, renewed.stderr
    assert (tls_dir / "ca" / "rootCA.pem").read_bytes() == root_before
    assert _run("fingerprint", tls_dir, env).stdout.strip() == fingerprint_before
    assert "192.0.2.11" in log.read_text(encoding="utf-8")


def test_missing_mkcert_explains_offline_binary_option(tmp_path: Path) -> None:
    env = {**os.environ, "PATH": str(tmp_path), "MKCERT_TEST_LOG": str(tmp_path / "unused")}
    result = _run("create", tmp_path / "tls", env, "127.0.0.1")
    assert result.returncode == 2
    assert "offline mkcert binary" in result.stderr
    assert "network requests" in result.stderr


def test_enable_persists_https_and_disable_keeps_certificates(tmp_path: Path) -> None:
    executable, log = _fake_mkcert(tmp_path)
    tls_dir = tmp_path / "tls"
    env = {
        **os.environ, "PATH": f"{executable.parent}{os.pathsep}{os.environ['PATH']}",
        "MKCERT_TEST_LOG": str(log),
    }

    enabled = _run("enable", tls_dir, env, "192.0.2.44")
    assert enabled.returncode == 0, enabled.stderr
    profile = json.loads((tls_dir / "service-profile.json").read_text(encoding="utf-8"))
    assert profile["enabled"] is True
    assert "192.0.2.44" in profile["hosts"]
    assert (tls_dir / "server-key.pem").is_file()

    enabled_again = _run("enable", tls_dir, env)
    assert enabled_again.returncode == 0, enabled_again.stderr
    profile = json.loads((tls_dir / "service-profile.json").read_text(encoding="utf-8"))
    assert "192.0.2.44" in profile["hosts"]

    values = _run("profile-values", tls_dir, env)
    assert values.stdout.splitlines()[0] == "https"
    certificate_before = (tls_dir / "server.pem").read_bytes()
    disabled = _run("disable", tls_dir, env)
    assert disabled.returncode == 0, disabled.stderr
    profile = json.loads((tls_dir / "service-profile.json").read_text(encoding="utf-8"))
    assert profile["enabled"] is False
    assert (tls_dir / "server.pem").read_bytes() == certificate_before
    assert _run("profile-values", tls_dir, env).stdout.splitlines()[0] == "http"


def test_disable_recovers_a_corrupt_profile_without_deleting_ca(tmp_path: Path) -> None:
    tls_dir = tmp_path / "tls"
    tls_dir.mkdir()
    (tls_dir / "service-profile.json").write_text("not json", encoding="utf-8")
    (tls_dir / "server.pem").write_text("keep", encoding="utf-8")

    disabled = _run("disable", tls_dir, os.environ.copy())

    assert disabled.returncode == 0, disabled.stderr
    assert (tls_dir / "server.pem").read_text(encoding="utf-8") == "keep"
    assert _run("profile-values", tls_dir, os.environ.copy()).stdout.splitlines()[0] == "http"
