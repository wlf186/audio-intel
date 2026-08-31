from __future__ import annotations

import hashlib
import ssl
from dataclasses import replace

from fastapi.testclient import TestClient

import audio_intel.api as api_module
import audio_intel.db as db_module
from audio_intel.config import settings
from scripts.setup_local_tls import fingerprint, validate_config


TEST_CA = """-----BEGIN CERTIFICATE-----
MIIBjTCCATOgAwIBAgIUJYbOajHkTqXAFb4NiV6Xjo1YFjgwCgYIKoZIzj0EAwIw
GDEWMBQGA1UEAwwNYXVkaW8taW50ZWwtY2EwHhcNMjYwODMxMDAwMDAwWhcNMjcw
ODMxMDAwMDAwWjAYMRYwFAYDVQQDDA1hdWRpby1pbnRlbC1jYTBZMBMGByqGSM49
AgEGCCqGSM49AwEHA0IABLWKXpp8mCU1L4F5rZUx5ubDLdp8POZw1ar8Ucr8+fJt
9f/R25GZqlaL3CkF5/DF0cO08qgWq7xqfEwdojD+aPWjUzBRMB0GA1UdDgQWBBSR
fsSxYQqQflpSkXp5TNq3sm7ABDAfBgNVHSMEGDAWgBSRfsSxYQqQflpSkXp5TNq3
sm7ABDAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0gAMEUCIQCRjX4j6+fS
Hj8TjFWlLB79op8p58zkk9k7VqdC5YdgDAIgG4HDBXUZwTc9oYhHoNdsszr09V28
Ozz2xNOLqylUK1k=
-----END CERTIFICATE-----
"""


def test_public_tls_bootstrap_and_downloads_are_pre_auth(tmp_path, monkeypatch) -> None:
    ca = tmp_path / "root.pem"
    ca.write_bytes(TEST_CA.encode("ascii"))
    local = replace(
        settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp",
        api_key="secret", protocol="https", tls_ca_file=ca,
    )
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    der = ssl.PEM_cert_to_DER_cert(TEST_CA)
    digest = hashlib.sha256(der).hexdigest().upper()
    expected = ":".join(digest[index:index + 2] for index in range(0, len(digest), 2))

    with TestClient(api_module.create_app(), base_url="https://testserver") as client:
        metadata = client.get("/api/v1/tls/bootstrap")
        assert metadata.status_code == 200
        assert metadata.json() == {
            "protocol": "https", "ca_installation_available": True,
            "ca_sha256_fingerprint": expected,
            "ca_download_urls": {"cer": "/api/v1/tls/root-ca.cer", "pem": "/api/v1/tls/root-ca.pem"},
        }
        cer = client.get("/api/v1/tls/root-ca.cer")
        assert cer.content == der
        assert cer.headers["content-type"].startswith("application/pkix-cert")
        assert cer.headers["cache-control"] == "no-store"
        pem = client.get("/api/v1/tls/root-ca.pem")
        assert pem.content == TEST_CA.encode("ascii")
        assert b"PRIVATE KEY" not in cer.content + pem.content

        login = client.post("/api/v1/auth/session", headers={"Authorization": "Bearer secret"})
        assert "secure" in login.headers["set-cookie"].lower()
        created = client.post(
            "/api/v1/voiceprints/people", json={"name": "secure origin"},
            headers={"Origin": "https://testserver"},
        )
        assert created.status_code == 201
        removed = client.delete(
            f"/api/v1/voiceprints/people/{created.json()['id']}?purge=true",
            headers={"Origin": "https://testserver"},
        )
        assert removed.status_code == 204


def test_http_bootstrap_has_no_ca_download(tmp_path, monkeypatch) -> None:
    local = replace(settings, data_dir=tmp_path / "data", temp_dir=tmp_path / "tmp", protocol="http", tls_ca_file=None)
    monkeypatch.setattr(api_module, "settings", local)
    monkeypatch.setattr(db_module, "settings", local)
    with TestClient(api_module.create_app()) as client:
        assert client.get("/api/v1/tls/bootstrap").json() == {"protocol": "http", "ca_installation_available": False}
        assert client.get("/api/v1/tls/root-ca.cer").status_code == 404
        assert client.get("/api/v1/tls/root-ca.pem").status_code == 404


def test_tls_config_rejects_ambiguous_and_missing_settings(tmp_path) -> None:
    validate_config("http", "", "", "")
    try:
        validate_config("ftp", "", "", "")
    except RuntimeError as exc:
        assert "http" in str(exc) and "https" in str(exc)
    else:
        raise AssertionError("invalid protocol accepted")
    try:
        validate_config("http", str(tmp_path / "server.pem"), "", "")
    except RuntimeError as exc:
        assert "require" in str(exc)
    else:
        raise AssertionError("HTTP with TLS settings accepted")
    try:
        validate_config("https", "", "", "")
    except RuntimeError as exc:
        assert "requires" in str(exc)
    else:
        raise AssertionError("HTTPS without a certificate accepted")


def test_ca_fingerprint_is_colon_separated_sha256(tmp_path) -> None:
    ca = tmp_path / "root.pem"
    ca.write_text(TEST_CA, encoding="ascii")
    value = fingerprint(ca)
    assert len(value.split(":")) == 32
    assert value == value.upper()
