"""The real xdist lifecycle returns worker CPU timing and preserves failing exit status."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("session_timeout", [False, True])
def test_xdist_timing_is_emitted_for_a_failed_session_without_replacing_its_result(tmp_path, session_timeout):
    tests = tmp_path / "tests"
    tests.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / "conftest.py", tests / "conftest.py")
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    integration: integration\n")
    (tests / "test_probe.py").write_text(
        "import pytest\npytestmark = pytest.mark.integration\n"
        "def test_pass():\n    assert True\n"
        "def test_fail():\n    assert False\n"
    )
    command = [sys.executable, "-m", "pytest", "-m", "(integration) and not perf", "-n", "1", "-q", "-x"]
    if session_timeout:
        command.append("--session-timeout=0.000001")  # Expired before the first test; no sleep or timing assertion.
    result = subprocess.run(
        command,
        cwd=tmp_path,
        env={"HOME": str(tmp_path), "PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr  # xdist reports fail-fast as Interrupted.
    assert "INTEGRATION collected worker=gw0 tests=2 controller_seconds=" in result.stdout
    completed = 1 if session_timeout else 2
    assert re.search(
        rf"INTEGRATION worker=gw0 completed={completed} wall_seconds=[0-9.]+ cpu_seconds=[0-9.]+ "
        r"child_cpu_seconds=[0-9.]+ phase_seconds=[0-9.]+",
        result.stdout,
    ), result.stdout
    assert "INTEGRATION session exit=2 wall_seconds=" in result.stdout
    assert ("session-timeout: 1e-06 sec exceeded" if session_timeout else "1 failed, 1 passed") in result.stdout
