"""Every browser-test host installs both engines before running the mounted journeys."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("platform", ["Linux", "Darwin"])
def test_browser_setup_installs_both_engines_and_only_linux_system_packages(tmp_path, platform):
    commands = tmp_path / "bin"
    commands.mkdir()
    uname = commands / "uname"
    uname.write_text(f"#!/bin/sh\nprintf '%s\\n' {platform}\n")
    uname.chmod(0o700)
    output = tmp_path / "arguments.json"
    recorder = tmp_path / "record.py"
    recorder.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['BROWSER_SETUP_RECORD']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    environment = {
        **os.environ,
        "PATH": str(commands) + os.pathsep + os.environ["PATH"],
        "BROWSER_SETUP_RECORD": str(output),
    }
    subprocess.run(
        ["make", "--no-print-directory", "test-browser-deps", "WEB=" + shlex.join([sys.executable, str(recorder)])],
        cwd=REPO,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    expected = ["exec", "playwright-core", "install"]
    if platform == "Linux":
        expected.append("--with-deps")
    assert json.loads(output.read_text()) == [*expected, "chromium", "webkit"]


def test_every_e2e_matrix_host_installs_its_engines_before_tests():
    workflow = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["e2e"]["steps"]
    setup = next(step for step in steps if step.get("run") == "make test-browser-deps")
    run = next(step for step in steps if step.get("run") == "make test-e2e SUITE=${{ matrix.suite }}")
    # Only the browser suite needs engines; the tool-install and CLI suite skips the install.
    assert setup.get("if", "matrix.suite == 'web'") == "matrix.suite == 'web'"
    assert steps.index(setup) < steps.index(run)
    assert "make test-browser-deps" not in (REPO / "template/.github/workflows/ci.yml.jinja").read_text()
