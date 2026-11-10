"""Audit every locked environment without installing inference packages.

Exit 1 for unaccepted advisories, incomplete queries or invalid exceptions.
JSON preserves raw advisories; the Markdown summary groups their aliases.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "audio"


def versions() -> dict[str, str]:
    if PROJECT == "video":
        from bootstrap_tools import VERSIONS

        return VERSIONS
    bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    return {
        "uv": re.search(r'UV_VERSION="([^"]+)"', bootstrap)[1],
        "pnpm": json.loads(
            (ROOT / "frontend/package.json").read_text(encoding="utf-8")
        )["packageManager"].split("@")[-1],
    }


def read_pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s+\\)?", line)
        if not match:
            raise ValueError(f"Non-pinned dependency in {path.name}: {line}")
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        if name in pins:
            raise ValueError(f"Duplicate pin: {name}")
        pins[name] = match[2]
    if not pins:
        raise ValueError(f"Empty lock: {path}")
    return pins


def audit_version(name: str, version: str) -> str:
    if name in {"torch", "torchvision", "torchaudio"}:
        for suffix in ("+cu130", "+cpu"):
            if version.endswith(suffix):
                return version.removesuffix(suffix)
    if "+" in version:
        raise ValueError(f"Unmapped local distribution: {name}=={version}")
    return version


def apply_exceptions(dependencies: list[dict[str, Any]], pins: dict[str, str], exceptions: list[dict[str, Any]], *, today: date | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    today = today or datetime.now(timezone.utc).date()
    for item in exceptions:
        if not item["reason"] or not item["evidence"] or not item["ids"]:
            raise ValueError("An exception requires rationale and evidence")
        review = date.fromisoformat(item["review_after"])
        if review <= today:
            raise ValueError(f"Expired exception: {item['ids']}")
        if (
            "reviewed_at" in item
            and not 0 < (review - date.fromisoformat(item["reviewed_at"])).days <= 30
        ):
            raise ValueError("Exception review window exceeds 30 days")
        if pins.get(item["package"]) != item["version"]:
            raise ValueError(f"Exception version differs from lock: {item['package']}")
    accepted, rejected, used, seen = [], [], set(), set()
    for dep in dependencies:
        if "skip_reason" in dep:
            raise ValueError(
                f"Unaudited dependency: {dep['name']}: {dep['skip_reason']}"
            )
        name = dep["name"]
        if (
            name in seen
            or name not in pins
            or dep["version"] != audit_version(name, pins[name])
        ):
            raise ValueError(f"Audit result does not match requested pin: {name}")
        seen.add(name)
        for vuln in dep.get("vulns", []):
            ids = {vuln["id"], *vuln.get("aliases", [])}
            item = {
                "package": name,
                "installed_version": pins[name],
                "queried_version": dep["version"],
                "advisory": vuln,
            }
            matches = [
                i
                for i, e in enumerate(exceptions)
                if e["package"] == name and ids.intersection(e["ids"])
            ]
            if matches:
                used.update(matches)
                item["exception"] = exceptions[matches[0]]
                accepted.append(item)
            else:
                rejected.append(item)
    if seen != set(pins):
        raise ValueError("Audit omitted dependencies")
    if used != set(range(len(exceptions))):
        raise ValueError(
            "Stale exception no longer matches an advisory; remove/review it"
        )
    return accepted, rejected


def check_evidence(exceptions: list[dict[str, Any]]) -> None:
    for item in exceptions:
        if PROJECT == "video":
            from bootstrap_tools import VERSIONS
            from download_models import REVISION

            if (
                item["comfyui_commit"] != VERSIONS["comfyui_commit"]
                or item["model_revision"] != REVISION
            ):
                raise ValueError(
                    "Inference stack changed; reassess dependency exceptions"
                )
        elif (
            item["model_manifest_sha256"]
            != hashlib.sha256(
                (ROOT / "audio_intel/model_manifest.json").read_bytes()
            ).hexdigest()
        ):
            raise ValueError("Model manifest changed; reassess dependency exceptions")
        for path in item["evidence"]:
            resolved = (ROOT / path).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.is_file():
                raise ValueError(f"Missing exception evidence: {path}")


def transient(message: str) -> bool:
    return bool(re.search(r"timeout|timed out|429|\b5\d\d\b", message, re.IGNORECASE))


def run_json(command: list[str]) -> tuple[dict[str, Any], int]:
    for attempt in range(3):
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
            continue
        try:
            data = json.loads(result.stdout)
            if (
                result.returncode not in {0, 1}
                or not isinstance(data, dict)
                or data.get("error")
            ):
                raise ValueError(
                    str(data.get("error"))
                    if isinstance(data, dict) and data.get("error")
                    else result.stderr or result.stdout
                )
            return data, result.returncode
        except ValueError as error:
            message = result.stderr or result.stdout or str(error)
            if attempt == 2 or not transient(message):
                raise RuntimeError(message) from error
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def audit_python(pins: dict[str, str]) -> list[dict[str, Any]]:
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="audit-", dir=ROOT / "tmp") as folder:
        normalized = Path(folder) / "requirements.txt"
        normalized.write_text(
            "".join(f"{n}=={audit_version(n, v)}\n" for n, v in sorted(pins.items())),
            encoding="utf-8",
        )
        data, code = run_json(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--no-deps",
                "--disable-pip",
                "--vulnerability-service",
                "osv",
                "--format",
                "json",
                "--progress-spinner",
                "off",
                "-r",
                str(normalized),
            ]
        )
    if not isinstance(data.get("dependencies"), list) or (
        code and not any(d.get("vulns") for d in data["dependencies"])
    ):
        raise RuntimeError(
            "Python audit failed without a complete vulnerability report"
        )
    return data["dependencies"]


def audit_tools() -> dict[str, list[dict[str, Any]]]:
    request = urllib.request.Request(
        "https://registry.npmjs.org/-/npm/v1/security/advisories/bulk",
        data=json.dumps({"pnpm": [versions()["pnpm"]]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                data = json.load(response)
            if not isinstance(data, dict) or any(
                not isinstance(v, list) for v in data.values()
            ):
                raise ValueError("Invalid tool audit response")
            return data
        except (TimeoutError, urllib.error.URLError) as error:
            if attempt == 2 or not transient(str(error)):
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def scopes() -> dict[str, dict[str, str]]:
    if PROJECT == "video":
        full = read_pins(ROOT / "requirements.lock")
        ci = read_pins(ROOT / "requirements-ci.lock")
        if any(full.get(n) != v for n, v in ci.items()):
            raise ValueError("CI/runtime dependency drift")
        return {"runtime": full, "ci": ci, "tools": {"uv": versions()["uv"]}}
    result = {
        p.relative_to(ROOT).as_posix(): read_pins(p)
        for p in sorted((ROOT / "requirements-lock").glob("*/*.txt"))
    }
    expected = {
        f"requirements-lock/{platform}/{runtime}{suffix}.txt"
        for platform in ("linux", "windows")
        for runtime in ("api", "asr", "tts", "aligner")
        for suffix in (("",) if runtime == "api" else ("", "-cpu"))
    }
    if set(result) != expected:
        raise ValueError("Missing or unexpected environment locks")
    result["tools"] = {"uv": versions()["uv"], "pip-audit": "2.10.1"}
    return result


def selected_exceptions(name: str, pins: dict[str, str], exceptions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if PROJECT == "video":
        return [e for e in exceptions if name == "runtime"]
    return [e for e in exceptions if name in e["scopes"]]


def collect_python(pins: dict[str, str], cache: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """Reuse only validated queries from this invocation, preserving each scope."""
    missing = {
        name: version
        for name, version in pins.items()
        if (name, audit_version(name, version)) not in cache
    }
    if missing:
        dependencies = audit_python(missing)
        apply_exceptions(dependencies, missing, [])
        for dep in dependencies:
            cache[(dep["name"], dep["version"])] = dep
    return [
        cache[(name, audit_version(name, version))] for name, version in pins.items()
    ]


def summary(report: dict[str, Any]) -> str:
    lines = [
        "# Dependency security audit",
        "",
        f"Status: **{report['status']}**",
        f"UTC: {report['timestamp']}",
        f"Trigger: {report['event']} · SHA: {report['sha']}",
        "",
        "| Scope | Status | Unaccepted records |",
        "| --- | --- | ---: |",
    ]
    for name, scope in report["scopes"].items():
        lines.append(
            f"| {name} | {scope['status']} | {scope.get('finding_count', len(scope.get('rejected', [])))} |"
        )
    if report.get("error"):
        lines.extend(["", "Failure: " + report["error"], ""])
    groups = []
    for item in report["rejected"]:
        ids = {item["advisory"]["id"], *item["advisory"].get("aliases", [])}
        matching = [
            g
            for g in groups
            if g["package"] == item["package"]
            and g["version"] == item["installed_version"]
            and ids.intersection(g["ids"])
        ]
        group = {
            "package": item["package"],
            "version": item["installed_version"],
            "ids": ids,
            "scopes": {item["scope"]},
        }
        for old in matching:
            group["ids"].update(old["ids"])
            group["scopes"].update(old["scopes"])
            groups.remove(old)
        groups.append(group)
    lines.extend(["", "## Unaccepted Python advisories", ""])
    for g in groups:
        lines.append(
            f"- {g['package']} {g['version']}: {', '.join(sorted(g['ids']))}; scopes: {', '.join(sorted(g['scopes']))}"
        )
    lines.extend(["", "## Frontend and tools", ""])
    for advisory in report.get("frontend", {}).get("advisories", {}).values():
        lines.append(
            f"- {advisory.get('module_name')}: {advisory.get('severity')}; {advisory.get('title')}; patched: {advisory.get('patched_versions')}"
        )
    for tool, advisories in report.get("tools", {}).items():
        for advisory in advisories:
            lines.append(f"- {tool}: {advisory.get('title', advisory.get('id'))}")
    for name, scope in report["scopes"].items():
        if scope.get("error"):
            lines.extend(["", f"{name}: {scope['error']}"])
    lines.extend(
        [
            "",
            "Raw Python advisories, accepted exceptions, frontend advisories and tool reports are retained in the JSON artifact.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "tmp/dependency-audit.json"
    )
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    report = {
        "status": "failed",
        "date": now.date().isoformat(),
        "timestamp": now.isoformat(),
        "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "sha": os.environ.get("GITHUB_SHA", "local working tree"),
        "scopes": {},
        "python": [],
        "accepted": [],
        "rejected": [],
    }
    errors = []
    try:
        exceptions = json.loads(
            (ROOT / "docs/dependency-exceptions.json").read_text(encoding="utf-8")
        )
        check_evidence(exceptions)
        environments = scopes()
        if PROJECT == "audio" and any(
            not e["scopes"]
            or set(e["scopes"]) - set(environments)
            or any("/api" in s or s == "tools" for s in e["scopes"])
            for e in exceptions
        ):
            raise ValueError("Invalid exception scope")
        cache = {}
        for name, pins in environments.items():
            scope = report["scopes"][name] = {"status": "error", "pins": pins}
            try:
                print(f"Auditing {name}...", flush=True)
                deps = collect_python(pins, cache)
                scope["python"] = deps
                report["python"].extend(deps)
                accepted, rejected = apply_exceptions(
                    deps, pins, selected_exceptions(name, pins, exceptions)
                )
                scope.update(
                    accepted=accepted,
                    rejected=rejected,
                    status="failed" if rejected else "passed",
                )
                for key, items in (("accepted", accepted), ("rejected", rejected)):
                    report[key].extend({**item, "scope": name} for item in items)
            except (
                OSError,
                ValueError,
                RuntimeError,
                KeyError,
                TypeError,
                subprocess.SubprocessError,
            ) as error:
                scope["error"] = str(error)
                errors.append(f"{name}: {error}")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        errors.append(str(error))
    for name in ("frontend", "tools-pnpm"):
        scope = report["scopes"][name] = {"status": "error"}
        try:
            if name == "frontend":
                command = (
                    [str(ROOT / "scripts/pnpm.sh"), "audit", "--json"]
                    if PROJECT == "video"
                    else [
                        shutil.which("corepack") or "corepack",
                        f"pnpm@{versions()['pnpm']}",
                        "--dir",
                        "frontend",
                        "audit",
                        "--json",
                    ]
                )
                data, code = run_json(command)
                counts = data.get("metadata", {}).get("vulnerabilities")
                if (
                    not isinstance(counts, dict)
                    or set(counts) != {"info", "low", "moderate", "high", "critical"}
                    or any(not isinstance(v, int) or v < 0 for v in counts.values())
                    or (code and not any(counts.values()))
                ):
                    raise ValueError("Incomplete frontend audit")
                failed = any(counts.values())
                scope["finding_count"] = sum(counts.values())
            else:
                data = audit_tools()
                failed = any(data.values())
                scope["finding_count"] = sum(len(items) for items in data.values())
            report["frontend" if name == "frontend" else "tools"] = data
            scope.update(status="failed" if failed else "passed", result=data)
        except (
            OSError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ) as error:
            scope["error"] = str(error)
            errors.append(f"{name}: {error}")
    if not errors and all(s["status"] == "passed" for s in report["scopes"].values()):
        report["status"] = (
            "passed_with_documented_exceptions" if report["accepted"] else "passed"
        )
    else:
        report["error"] = (
            "; ".join(errors) or "Unaccepted dependency vulnerabilities found"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    markdown = summary(report)
    args.output.with_suffix(".md").write_text(markdown, encoding="utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(markdown)
    print(markdown)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    os.environ.setdefault("PIP_CACHE_DIR", str(ROOT / "cache/pip"))
    main()
