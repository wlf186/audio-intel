"""Release gates must never accept incomplete parallel checks."""
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_parallel_gate_rejects_failed_cancelled_skipped_or_missing_jobs(platform):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / f"{platform}.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "needs" not in jobs["backend"] and "needs" not in jobs["browser"]
    gate = jobs[platform]
    assert set(gate["needs"]) == {"backend", "browser"}
    assert gate["if"] == "always()"
    assert not gate.get("continue-on-error", False)
    script = gate["steps"][0]["with"]["script"]
    # Execute the actual workflow predicate, including missing/unexpected states.
    node = shutil.which("node")
    assert node, "Node is required by the documented API/frontend setup"
    cases = []
    states = ("success", "failure", "cancelled", "skipped", "pending", None)
    for backend, browser in itertools.product(states, repeat=2):
        results = {name: {"result": result} for name, result in zip(("backend", "browser"), (backend, browser)) if result is not None}
        cases.append({"results": results, "accepted": backend == browser == "success"})
    harness = """
const vm = require('node:vm')
const cases = JSON.parse(process.env.GATE_CASES)
for (const item of cases) {
  const failures = []
  vm.runInNewContext(process.env.GATE_SCRIPT, {
    process: {env: {CHECK_RESULTS: JSON.stringify(item.results)}},
    core: {setFailed: message => failures.push(message)},
  })
  if ((failures.length === 0) !== item.accepted) throw new Error(JSON.stringify(item))
}
"""
    subprocess.run([node, "-e", harness], env={**os.environ, "GATE_CASES": json.dumps(cases), "GATE_SCRIPT": script}, check=True, timeout=10)


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_full_checks_remain_on_main_and_tags_with_distinct_reports(platform):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / f"{platform}.yml").read_text(encoding="utf-8"))
    assert workflow["on"]["push"] == {"branches": ["main", "ci/**"], "tags": ["v*"]}
    assert "pull_request" in workflow["on"]
    artifacts = []
    for kind in ("backend", "browser"):
        job = workflow["jobs"][kind]
        assert job["env"]["AUDIO_INTEL_LOAD_ENV"] == "0"
        check = next(step for step in job["steps"] if step.get("name") == ("Backend checks" if kind == "backend" else "Browser checks"))
        assert "if" not in check
        assert not check.get("continue-on-error", False)
        assert not job.get("continue-on-error", False)
        assert "--grep" not in check["run"] and "--shard" not in check["run"]
        artifacts.extend(step["with"]["name"] for step in job["steps"] if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert len(artifacts) == len(set(artifacts)) == 4


def test_native_powershell_preserves_stability_tag_argument(tmp_path):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("The native Windows job verifies PowerShell argument parsing")
    workflow = yaml.safe_load((ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8"))
    command = next(step["run"] for step in workflow["jobs"]["browser"]["steps"] if step.get("name") == "Repeat timing regressions")
    # A bare @timing is splatted by PowerShell and disappears before pnpm runs.
    # Capture arguments after the real shell parser, without invoking Playwright.
    script = tmp_path / "capture-arguments.ps1"
    script.write_text("""
function corepack.cmd {
    ConvertTo-Json -InputObject @($args) -Compress
    $global:LASTEXITCODE = 0
}
""" + command, encoding="utf-8")
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)], check=True, capture_output=True, text=True, timeout=10)
    arguments = json.loads(result.stdout)
    assert arguments[arguments.index("--grep") + 1] == "@timing"
    assert "--repeat-each=5" in arguments
    assert "--output=../logs/playwright-stability" in arguments
