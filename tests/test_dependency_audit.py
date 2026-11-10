"""Security gates must retain coverage and fail closed without hiding evidence."""

import json
from pathlib import Path

import pytest
import yaml

from scripts import audit_dependencies as audit


def test_all_runtime_locks_and_official_cpu_identity():
    if audit.PROJECT == "audio":
        environments = audit.scopes()
        assert len(environments) == 15
        assert "requirements-lock/windows/aligner-cpu.txt" in environments
    assert audit.audit_version("torch", "2.11.0+cpu") == "2.11.0"
    with pytest.raises(ValueError, match="Unmapped"):
        audit.audit_version("other", "1+cpu")


@pytest.mark.parametrize("first_fails", [False, True, "malformed"])
def test_audit_collects_every_scope_and_frontend_after_failure(
    tmp_path, monkeypatch, first_fails
):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/dependency-exceptions.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(
        audit, "scopes", lambda: {"first": {"pkg": "1"}, "second": {"pkg": "2"}}
    )
    monkeypatch.setattr(audit, "versions", lambda: {"pnpm": "10.15.1"})
    calls = []

    def query(pins):
        calls.append(pins["pkg"])
        if first_fails and pins["pkg"] == "1":
            if first_fails == "malformed":
                return [None]
            raise RuntimeError("registry offline")
        return [{"name": "pkg", "version": pins["pkg"], "vulns": [{"id": "CVE-test"}]}]

    monkeypatch.setattr(audit, "audit_python", query)
    monkeypatch.setattr(
        audit,
        "run_json",
        lambda command: ({"metadata": {"vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 1, "critical": 0}}}, 1),
    )
    monkeypatch.setattr(audit, "audit_tools", dict)
    output = tmp_path / "audit.json"
    monkeypatch.setattr(audit.sys, "argv", ["audit", "--output", str(output)])
    with pytest.raises(SystemExit) as result:
        audit.main()
    assert result.value.code == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert calls == ["1", "2"]
    assert report["scopes"]["frontend"]["status"] == "failed"
    assert report["scopes"]["tools-pnpm"]["status"] == "passed"
    assert report["scopes"]["first"]["status"] == ("error" if first_fails else "failed")
    assert report["rejected"][-1]["scope"] == "second"
    assert "UTC:" in output.with_suffix(".md").read_text(encoding="utf-8")


def test_network_retry_is_bounded_and_only_transient(monkeypatch):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return audit.subprocess.CompletedProcess([], 2, "", "HTTP 503")

    monkeypatch.setattr(audit.subprocess, "run", run)
    monkeypatch.setattr(audit.time, "sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="503"):
        audit.run_json(["audit"])
    assert len(calls) == 3
    calls.clear()
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        lambda *a, **k: audit.subprocess.CompletedProcess([], 1, "not JSON", ""),
    )
    with pytest.raises(RuntimeError, match="not JSON"):
        audit.run_json(["audit"])


def test_exception_is_bound_to_version_date_and_exact_advisory():
    exception = {
        "package": "pkg",
        "version": "1",
        "ids": ["CVE-known"],
        "reason": "specific path not used",
        "evidence": ["source.py"],
        "reviewed_at": "2099-01-01",
        "review_after": "2099-01-31",
    }
    deps = [
        {
            "name": "pkg",
            "version": "1",
            "vulns": [
                {"id": "GHSA-known", "aliases": ["CVE-known"]},
                {"id": "CVE-new"},
            ],
        }
    ]
    accepted, rejected = audit.apply_exceptions(deps, {"pkg": "1"}, [exception])
    assert len(accepted) == 1 and rejected[0]["advisory"]["id"] == "CVE-new"
    for changes in (
        {"review_after": "2000-01-01"},
        {"version": "2"},
        {"review_after": "2099-03-01"},
        {"ids": ["CVE-absent"]},
    ):
        with pytest.raises(ValueError):
            audit.apply_exceptions(deps, {"pkg": "1"}, [{**exception, **changes}])
    with pytest.raises(ValueError, match="omitted"):
        audit.apply_exceptions([], {"pkg": "1"}, [])


def test_weekly_security_and_monthly_functional_workflows():
    root = Path(__file__).resolve().parents[1]
    security = yaml.safe_load(
        (root / ".github/workflows/dependency-security.yml").read_text(encoding="utf-8")
    )
    linux = yaml.safe_load(
        (root / ".github/workflows/linux.yml").read_text(encoding="utf-8")
    )
    assert security["on"]["schedule"] == [{"cron": "23 4 * * 1"}]
    assert "workflow_call" in security["on"] and "workflow_dispatch" in security["on"]
    assert linux["on"]["schedule"] == [{"cron": "43 4 1 * *"}]
    assert set(linux["jobs"]["linux"]["needs"]) == {"backend", "browser", "security"}
    assert "needs" not in linux["jobs"]["browser"]
    assert (
        linux["jobs"]["security"]["uses"]
        == "./.github/workflows/dependency-security.yml"
    )
    assert security["jobs"]["audit"]["steps"][-1]["if"] == "always()"


def test_query_cache_is_per_invocation_and_validates_before_reuse(monkeypatch):
    calls = []

    def query(pins):
        calls.append(pins)
        return [{"name": "torch", "version": "2.11.0", "vulns": []}]

    monkeypatch.setattr(audit, "audit_python", query)
    cache = {}
    assert audit.collect_python({"torch": "2.11.0+cu130"}, cache)
    assert audit.collect_python({"torch": "2.11.0+cpu"}, cache)
    assert len(calls) == 1
    assert audit.collect_python({"torch": "2.11.0+cpu"}, {})
    assert len(calls) == 2
    monkeypatch.setattr(audit, "audit_python", lambda pins: [])
    incomplete = {}
    with pytest.raises(ValueError, match="omitted"):
        audit.collect_python({"torch": "2.11.0+cpu"}, incomplete)
    assert incomplete == {}
