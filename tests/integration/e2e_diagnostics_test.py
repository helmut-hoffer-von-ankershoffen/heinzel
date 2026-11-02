"""Exercise the actual pytest/xdist controller before an owned teardown stalls."""

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from tests.e2e.docs_test import GuardedCommand

pytestmark = pytest.mark.integration


def test_controller_emits_sanitized_failure_before_teardown(tmp_path):
    root = Path(__file__).resolve().parents[2]
    tests = tmp_path / "tests"
    e2e = tests / "e2e"
    e2e.mkdir(parents=True)
    for source, target in ((root / "tests" / "conftest.py", tests / "conftest.py"),):
        shutil.copyfile(source, target)
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers =\n    e2e: end to end\n")
    (e2e / "test_probe.py").write_text(
        "import time\nimport pytest\npytestmark = pytest.mark.e2e\n"
        "@pytest.fixture\ndef stalled():\n    yield\n    time.sleep(30)\n"
        "@pytest.mark.parametrize('value', ['PARAMETER_SECRET'])\n"
        "def test_probe(stalled, value):\n"
        "    raise ValueError('ERROR_SECRET http://127.0.0.1:1/#token=TOKEN_SECRET\\n"
        "locator.waitFor: Timeout 30000ms exceeded.\\n"
        "    at file:///runner/work/heinzel/heinzel/web/e2e/layers.mjs:168:13')\n"
    )
    env = {"HOME": str(tmp_path), "PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}
    command = [
        str(Path(sys.executable).parent / "pytest"),
        "-m",
        "e2e",
        "-n",
        "1",
        "-x",
        "-q",
        "tests/e2e/test_probe.py",
    ]
    guard = GuardedCommand(command, env=env, cwd=tmp_path)
    try:
        deadline = time.monotonic() + 10
        output = ""
        while time.monotonic() < deadline:
            guard.capture.pump(0.05)
            guard.poll_command()
            output = guard.capture.text("stdout") + guard.capture.text("stderr")
            if "E2E driver frame web/e2e/layers.mjs:168" in output:
                break
        assert guard.returncode is None
        assert "E2E start tests/e2e/test_probe.py::test_probe" in output
        assert "E2E failure tests/e2e/test_probe.py::test_probe phase=call" in output
        assert "E2E frame tests/e2e/test_probe.py:10" in output
        assert "PARAMETER_SECRET" not in output
        assert "ERROR_SECRET" not in output
        assert "TOKEN_SECRET" not in output
        assert "E2E driver locator.waitFor: Timeout 30000ms exceeded" in output
        assert "E2E driver frame web/e2e/layers.mjs:168" in output
    finally:
        guard.stop()
