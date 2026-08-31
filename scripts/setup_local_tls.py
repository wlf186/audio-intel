from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TLS_DIR = ROOT / "data" / "tls"
DEFAULT_HOSTS = ("localhost", "127.0.0.1", "::1")


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


def hosts(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in [*values, *DEFAULT_HOSTS]:
        normalized = value.strip().strip("[]")
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
    if action == "create" and any(path.exists() for path in items.values()):
        raise RuntimeError(f"TLS files already exist under {tls_dir}; use 'tls renew' to replace only the server certificate.")
    if action == "renew" and not all(items[name].is_file() for name in ("ca_pem", "ca_key")):
        raise RuntimeError(f"Existing project CA not found under {tls_dir / 'ca'}; run 'tls create' first.")
    mkcert = require_mkcert()
    tls_dir.mkdir(parents=True, exist_ok=True)
    items["ca_pem"].parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=tls_dir, prefix="leaf-") as temporary_dir:
        temporary = Path(temporary_dir)
        cert = temporary / "server.pem"
        key = temporary / "server-key.pem"
        environment = os.environ.copy()
        environment["CAROOT"] = str(items["ca_pem"].parent)
        command = [mkcert, "-cert-file", str(cert), "-key-file", str(key), *hosts(requested_hosts)]
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
        print(items[name].relative_to(ROOT) if items[name].is_relative_to(ROOT) else items[name])


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
    parser.add_argument("action", choices=("create", "renew", "fingerprint", "validate-config"))
    parser.add_argument("--host", action="append", default=[])
    parser.add_argument("--tls-dir", type=Path, default=DEFAULT_TLS_DIR)
    parser.add_argument("--protocol", default=os.getenv("AUDIO_INTEL_PROTOCOL", "http"))
    parser.add_argument("--cert", default=os.getenv("AUDIO_INTEL_TLS_CERT_FILE", ""))
    parser.add_argument("--key", default=os.getenv("AUDIO_INTEL_TLS_KEY_FILE", ""))
    parser.add_argument("--ca", default=os.getenv("AUDIO_INTEL_TLS_CA_FILE", ""))
    args = parser.parse_args()
    try:
        if args.action in {"create", "renew"}:
            generate(args.action, args.tls_dir.resolve(), args.host)
        elif args.action == "fingerprint":
            print(f"Root CA SHA-256: {fingerprint(paths(args.tls_dir.resolve())['ca_pem'])}")
        else:
            validate_config(args.protocol, args.cert, args.key, args.ca)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
