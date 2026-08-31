from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TLS_DIR = ROOT / "data" / "tls"
DEFAULT_HOSTS = ("localhost", "127.0.0.1", "::1")
PROFILE_VERSION = 1
RENEW_BEFORE_SECONDS = 30 * 24 * 60 * 60


def fingerprint(path: Path) -> str:
    try:
        der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"Unable to read root CA certificate {path}: {exc}") from exc
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[index:index + 2] for index in range(0, len(digest), 2))


def require_mkcert() -> str:
    executable = shutil.which("mkcert")
    if executable:
        return executable
    raise RuntimeError(
        "mkcert was not found. Install mkcert first, or place an offline mkcert binary on PATH; "
        "certificate generation itself makes no network requests."
    )


def _normalize_host(value: str) -> str:
    normalized = value.strip().strip("[]")
    if "%" in normalized:
        normalized = normalized.split("%", 1)[0]
    try:
        normalized = str(ipaddress.ip_address(normalized))
    except ValueError:
        pass
    return normalized


def _usable_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return not (address.is_unspecified or address.is_multicast or address.is_link_local)


def discover_hosts() -> list[str]:
    discovered: set[str] = set()
    for name in (socket.gethostname(), socket.getfqdn()):
        normalized = _normalize_host(name)
        if normalized and normalized.lower() != "localhost":
            discovered.add(normalized)
    try:
        import psutil

        for addresses in psutil.net_if_addrs().values():
            for item in addresses:
                if item.family not in {socket.AF_INET, socket.AF_INET6}:
                    continue
                normalized = _normalize_host(item.address)
                if normalized and _usable_address(normalized) and not ipaddress.ip_address(normalized).is_loopback:
                    discovered.add(normalized)
    except (ImportError, OSError):
        try:
            for item in socket.getaddrinfo(socket.gethostname(), None):
                normalized = _normalize_host(item[4][0])
                if normalized and _usable_address(normalized) and not ipaddress.ip_address(normalized).is_loopback:
                    discovered.add(normalized)
        except OSError:
            pass
    return sorted(discovered, key=lambda value: (":" in value, value.lower()))


def hosts(values: list[str], *, discover: bool = False) -> list[str]:
    result: list[str] = []
    candidates = [*DEFAULT_HOSTS]
    if discover:
        candidates.extend(discover_hosts())
    candidates.extend(values)
    for value in candidates:
        normalized = _normalize_host(value)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def paths(tls_dir: Path) -> dict[str, Path]:
    return {
        "cert": tls_dir / "server.pem",
        "key": tls_dir / "server-key.pem",
        "public_pem": tls_dir / "audio-intel-root-ca.pem",
        "public_cer": tls_dir / "audio-intel-root-ca.cer",
        "ca_pem": tls_dir / "ca" / "rootCA.pem",
        "ca_key": tls_dir / "ca" / "rootCA-key.pem",
        "profile": tls_dir / "service-profile.json",
    }


def write_public_ca(items: dict[str, Path]) -> None:
    pem = items["ca_pem"].read_text(encoding="ascii")
    der = ssl.PEM_cert_to_DER_cert(pem)
    temporary = items["public_pem"].with_suffix(".pem.tmp")
    temporary.write_text(pem, encoding="ascii")
    temporary.replace(items["public_pem"])
    temporary_cer = items["public_cer"].with_suffix(".cer.tmp")
    temporary_cer.write_bytes(der)
    temporary_cer.replace(items["public_cer"])


def generate(action: str, tls_dir: Path, requested_hosts: list[str]) -> None:
    items = paths(tls_dir)
    certificate_files = [items[name] for name in ("cert", "key", "public_pem", "public_cer", "ca_pem", "ca_key")]
    if action == "create" and any(path.exists() for path in certificate_files):
        raise RuntimeError(f"TLS files already exist under {tls_dir}; use 'tls renew' to replace only the server certificate.")
    if action == "renew" and not all(items[name].is_file() for name in ("ca_pem", "ca_key")):
        raise RuntimeError(f"Existing project CA not found under {tls_dir / 'ca'}; run 'tls create' first.")
    mkcert = require_mkcert()
    tls_dir.mkdir(parents=True, exist_ok=True)
    items["ca_pem"].parent.mkdir(parents=True, exist_ok=True)
    selected_hosts = hosts(requested_hosts)
    with tempfile.TemporaryDirectory(dir=tls_dir, prefix="leaf-") as temporary_dir:
        temporary = Path(temporary_dir)
        cert = temporary / "server.pem"
        key = temporary / "server-key.pem"
        environment = os.environ.copy()
        environment["CAROOT"] = str(items["ca_pem"].parent)
        command = [mkcert, "-cert-file", str(cert), "-key-file", str(key), *selected_hosts]
        result = subprocess.run(command, env=environment, text=True, capture_output=True, check=False)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"mkcert failed: {detail or f'exit code {result.returncode}'}")
        if not cert.is_file() or not key.is_file() or not items["ca_pem"].is_file() or not items["ca_key"].is_file():
            raise RuntimeError("mkcert did not produce the expected project CA and server certificate files.")
        cert.replace(items["cert"])
        key.replace(items["key"])
    write_public_ca(items)
    if os.name != "nt":
        items["key"].chmod(0o600)
        items["ca_key"].chmod(0o600)
        for name in ("cert", "public_pem", "public_cer", "ca_pem"):
            items[name].chmod(0o644)
    print(f"Root CA SHA-256: {fingerprint(items['ca_pem'])}")
    print("Generated locally (mkcert -install was not used):")
    for name in ("cert", "key", "public_pem", "public_cer", "ca_pem", "ca_key"):
        path = items[name]
        print(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def _certificate_details(path: Path) -> dict[str, Any]:
    try:
        decoded = ssl._ssl._test_decode_cert(str(path))
        expires_at = float(ssl.cert_time_to_seconds(decoded["notAfter"]))
    except (KeyError, OSError, ValueError, ssl.SSLError) as exc:
        raise RuntimeError(f"Unable to inspect server certificate {path}: {exc}") from exc
    certificate_hosts: list[str] = []
    for _, value in decoded.get("subjectAltName", ()):
        normalized = _normalize_host(value)
        if normalized and normalized not in certificate_hosts:
            certificate_hosts.append(normalized)
    return {"expires_at": expires_at, "hosts": certificate_hosts, "not_after": decoded["notAfter"]}


def _certificate_is_current(path: Path, selected_hosts: list[str]) -> bool:
    if not path.is_file():
        return False
    details = _certificate_details(path)
    expected = {value.lower() for value in selected_hosts}
    available = {value.lower() for value in details["hosts"]}
    return expected <= available and details["expires_at"] - time.time() > RENEW_BEFORE_SECONDS


def _write_profile(tls_dir: Path, *, enabled: bool, selected_hosts: list[str]) -> None:
    items = paths(tls_dir)
    tls_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": PROFILE_VERSION,
        "enabled": enabled,
        "cert_file": str(items["cert"]),
        "key_file": str(items["key"]),
        "ca_file": str(items["public_pem"]),
        "hosts": selected_hosts,
    }
    temporary = items["profile"].with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        temporary.chmod(0o600)
    temporary.replace(items["profile"])


def read_profile(tls_dir: Path) -> dict[str, Any] | None:
    path = paths(tls_dir)["profile"]
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to read TLS service profile {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != PROFILE_VERSION or not isinstance(payload.get("enabled"), bool):
        raise RuntimeError(f"Unsupported or invalid TLS service profile: {path}")
    for name in ("cert_file", "key_file", "ca_file"):
        if not isinstance(payload.get(name), str):
            raise RuntimeError(f"TLS service profile is missing {name}: {path}")
    if not isinstance(payload.get("hosts"), list) or not all(isinstance(item, str) for item in payload["hosts"]):
        raise RuntimeError(f"TLS service profile has invalid hosts: {path}")
    return payload


def enable(tls_dir: Path, requested_hosts: list[str]) -> None:
    items = paths(tls_dir)
    existing_hosts: list[str] = []
    if items["cert"].is_file():
        try:
            existing_hosts = _certificate_details(items["cert"])["hosts"]
        except RuntimeError:
            pass
    selected_hosts = hosts([*existing_hosts, *requested_hosts], discover=True)
    complete_ca = all(items[name].is_file() for name in ("ca_pem", "ca_key"))
    any_certificates = any(items[name].exists() for name in ("cert", "key", "public_pem", "public_cer", "ca_pem", "ca_key"))
    complete_leaf = all(items[name].is_file() for name in ("cert", "key", "public_pem", "public_cer"))
    if complete_ca and complete_leaf and _certificate_is_current(items["cert"], selected_hosts):
        validate_config("https", str(items["cert"]), str(items["key"]), str(items["public_pem"]))
        print("Existing server certificate already covers the selected hosts.")
    elif complete_ca:
        generate("renew", tls_dir, selected_hosts)
    elif any_certificates:
        raise RuntimeError(
            f"Incomplete TLS state exists under {tls_dir}; restore the project CA or move the incomplete files before enabling HTTPS."
        )
    else:
        generate("create", tls_dir, selected_hosts)
    validate_config("https", str(items["cert"]), str(items["key"]), str(items["public_pem"]))
    _write_profile(tls_dir, enabled=True, selected_hosts=selected_hosts)
    print("HTTPS mode saved for future start/restart commands.")
    print("Certificate hosts: " + ", ".join(selected_hosts))


def disable(tls_dir: Path) -> None:
    try:
        existing = read_profile(tls_dir)
    except RuntimeError:
        existing = None
    selected_hosts = existing.get("hosts", []) if existing else []
    _write_profile(tls_dir, enabled=False, selected_hosts=selected_hosts)
    print("HTTP mode saved for future start/restart commands; existing TLS certificates were kept.")


def profile_values(tls_dir: Path) -> None:
    profile = read_profile(tls_dir)
    if profile is None or not profile["enabled"]:
        print("http")
        print()
        print()
        print()
        print("default" if profile is None else "saved profile")
        return
    print("https")
    print(profile["cert_file"])
    print(profile["key_file"])
    print(profile["ca_file"])
    print("saved profile")


def profile_json(tls_dir: Path) -> None:
    profile = read_profile(tls_dir)
    if profile is None or not profile["enabled"]:
        payload = {
            "protocol": "http", "cert_file": "", "key_file": "", "ca_file": "",
            "source": "default" if profile is None else "saved profile",
        }
    else:
        payload = {
            "protocol": "https", "cert_file": profile["cert_file"], "key_file": profile["key_file"],
            "ca_file": profile["ca_file"], "source": "saved profile",
        }
    print(json.dumps(payload))


def profile_status(tls_dir: Path) -> None:
    profile = read_profile(tls_dir)
    if profile is None:
        print("Configured mode: http (default; no saved TLS profile)")
    else:
        mode = "https" if profile["enabled"] else "http"
        print(f"Configured mode: {mode} (saved TLS profile)")
    items = paths(tls_dir)
    if items["cert"].is_file():
        details = _certificate_details(items["cert"])
        print("Certificate hosts: " + ", ".join(details["hosts"]))
        print(f"Certificate expires: {details['not_after']}")
    if items["ca_pem"].is_file():
        print(f"Root CA SHA-256: {fingerprint(items['ca_pem'])}")
        print(f"Root CA download: {items['public_cer']}")


def validate_config(protocol: str, cert: str, key: str, ca: str) -> None:
    normalized = protocol.strip().lower() or "http"
    configured = [value for value in (cert, key, ca) if value.strip()]
    if normalized not in {"http", "https"}:
        raise RuntimeError("AUDIO_INTEL_PROTOCOL must be 'http' or 'https'.")
    if normalized == "http":
        if configured:
            raise RuntimeError("TLS file settings require AUDIO_INTEL_PROTOCOL=https; remove them for HTTP mode.")
        return
    if not cert.strip() or not key.strip():
        raise RuntimeError("HTTPS requires AUDIO_INTEL_TLS_CERT_FILE and AUDIO_INTEL_TLS_KEY_FILE.")
    cert_path = resolve_path(cert)
    key_path = resolve_path(key)
    if not cert_path.is_file() or not key_path.is_file():
        raise RuntimeError(f"HTTPS certificate or key file does not exist: {cert_path}, {key_path}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(cert_path, key_path, password="")
    except (OSError, ssl.SSLError) as exc:
        raise RuntimeError(f"HTTPS certificate/key validation failed: {exc}") from exc
    details = _certificate_details(cert_path)
    if details["expires_at"] <= time.time():
        raise RuntimeError(f"HTTPS certificate has expired: {cert_path}")
    if ca.strip():
        ca_path = resolve_path(ca)
        if not ca_path.is_file():
            raise RuntimeError(f"TLS root CA file does not exist: {ca_path}")
        fingerprint(ca_path)


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and inspect the project-local HTTPS certificate authority.")
    parser.add_argument(
        "action",
        choices=(
            "create", "renew", "fingerprint", "validate-config", "enable", "disable", "status",
            "profile-values", "profile-json",
        ),
    )
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--tls-dir", type=Path, default=DEFAULT_TLS_DIR)
    parser.add_argument("--protocol", default=os.getenv("AUDIO_INTEL_PROTOCOL", "http"))
    parser.add_argument("--cert", default=os.getenv("AUDIO_INTEL_TLS_CERT_FILE", ""))
    parser.add_argument("--key", default=os.getenv("AUDIO_INTEL_TLS_KEY_FILE", ""))
    parser.add_argument("--ca", default=os.getenv("AUDIO_INTEL_TLS_CA_FILE", ""))
    args = parser.parse_args()
    tls_dir = args.tls_dir.resolve()
    try:
        if args.action in {"create", "renew"}:
            generate(args.action, tls_dir, args.host)
        elif args.action == "enable":
            enable(tls_dir, args.host)
        elif args.action == "disable":
            disable(tls_dir)
        elif args.action == "status":
            profile_status(tls_dir)
        elif args.action == "profile-values":
            profile_values(tls_dir)
        elif args.action == "profile-json":
            profile_json(tls_dir)
        elif args.action == "fingerprint":
            print(f"Root CA SHA-256: {fingerprint(paths(tls_dir)['ca_pem'])}")
        else:
            validate_config(args.protocol, args.cert, args.key, args.ca)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
