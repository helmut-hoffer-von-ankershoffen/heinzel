"""Run case 6 once with the opt-in published bridge trace on a hosted worker."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1150)]
REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "legacy_discovery_trace", REPO / "scripts/acceptance/legacy_discovery_trace.py"
)
assert spec
assert spec.loader
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)


def test_published_bridge_discovery_trace(tmp_path):
    if os.environ.get("FOCUSED_SELECTOR") != trace.SELECTOR:
        pytest.skip("explicit hosted diagnostic only")
    trace.require_hosted(os.environ, sys.platform)
    harness = REPO / "scripts/acceptance/run_acceptance.py"
    files = [harness, REPO / trace.SELECTOR, Path(trace.__file__)]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True, timeout=10).strip()
    assert head == os.environ["GITHUB_SHA"]
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *map(str, files)], cwd=REPO, check=True, timeout=10)

    run = subprocess.run(
        [
            sys.executable,
            "-I",
            str(harness),
            "--case",
            "6",
            "--expect-version",
            "0.9.50",
            "--expect-app-version",
            "0.9.50",
            "--accept-wheel-sha256",
            "3f7e5595065c2422d939c8f038b3f501d35106ddd5805dbb39c3c369c500443b",
            "--accept-profile-sha256",
            "5cafd51b8402fef3e63444bb14f2ae13d2397e0b1a00787085af2f5d3c4df62c",
            "--accept-install-sh-sha256",
            "450e25fc233b0667b0915a14be94399ef3ba981ff74f4944fa9bf006c102462f",
            "--diagnostic-legacy-discovery",
        ],
        cwd=tmp_path,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=1130,
    )
    result = json.loads((tmp_path / "reports/acceptance/result.json").read_text())
    assert result["mode"] == "diagnostic_legacy_discovery"
    assert result["public_path_proof"] is False
    assert run.returncode == 1
    assert result["verdict"] == "NOT_ACCEPTANCE_DIAGNOSTIC"
    case = result["cases"]["6"]
    case_root = Path(case["root"]).resolve(strict=True)
    assert case_root.is_relative_to(tmp_path.resolve())
    try:
        document = trace.read_sidecar(case_root)
    except (OSError, ValueError, KeyError, TypeError):
        document = {"kind": "instrumented-non-acceptance", "events": [], "unavailable": True}
    step = (case["first_failure"] or {}).get("step")
    document["case"] = {
        "status": case["status"] if case["status"] in {"PASS", "FAIL", "REFUSED", "SKIPPED"} else "unknown",
        "bridge_update_failed": step == "the published bridge updates to the target release",
    }
    out = REPO / "focused-artifacts"
    out.mkdir(exist_ok=True)
    assert not out.is_symlink()
    fd = os.open(out / trace.FILENAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(document, output)
    assert not document.get("unavailable"), "Case 6 did not produce a verified discovery trace"
    assert case["status"] == "PASS", "Case 6 failed its original assertions; inspect the diagnostic artifact"
