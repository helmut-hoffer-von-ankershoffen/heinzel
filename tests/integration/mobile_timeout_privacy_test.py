"""Nested pytest failures never render a synthetic mobile session bearer."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _assert_nested_failure_hides_secret(test, expected):
    marker = secrets.token_hex(16)
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "HZ_FIXTURE_SECRET": marker,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--noconftest", str(test)],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
    assert expected in result.stdout


def test_pytest_failure_does_not_render_completed_process_secret(tmp_path):
    test = tmp_path / "test_nested.py"
    test.write_text(
        "import os, subprocess\n"
        "from tests.e2e._mobile_server_output import check_driver, redact\n"
        "def test_fails():\n"
        " token = os.environ['HZ_FIXTURE_SECRET']\n"
        " driven = subprocess.CompletedProcess(['http://127.0.0.1/#token='+token], 1, "
        "stderr='Authorization: Bearer '+token)\n"
        " check_driver(driven.returncode, redact(driven.stderr, token))\n"
    )
    _assert_nested_failure_hides_secret(test, "<redacted>")


def test_real_timeout_reports_only_fixed_checkpoint_in_pytest_traceback(tmp_path):
    test = tmp_path / "test_timeout_trace.py"
    test.write_text(
        "import os, selectors, subprocess, sys\n"
        "from tests.e2e._mobile_server_output import sanitize_timeout\n"
        "def test_timeout():\n"
        " token=os.environ['HZ_FIXTURE_SECRET']\n"
        ' child = ("import sys,time; "\n'
        "          \"print('MOBILE_STAGE:viewport-wide-portrait-loaded', file=sys.stderr, flush=True); \"\n"
        '          "print(sys.argv[1], file=sys.stderr, flush=True); "\n'
        '          "print(\\"READY\\", flush=True); time.sleep(5)")\n'
        " real_popen = subprocess.Popen\n"
        " class ReadyPopen(real_popen):\n"
        "  def __init__(self, *args, **kwargs):\n"
        "   super().__init__(*args, **kwargs)\n"
        "   try:\n"
        "    with selectors.DefaultSelector() as sel:\n"
        "     sel.register(self.stdout, selectors.EVENT_READ)\n"
        "     assert sel.select(5) and self.stdout.readline() == b'READY\\n'\n"
        "   except BaseException:\n"
        "    self.kill(); self.communicate(timeout=5); raise\n"
        " subprocess.Popen = ReadyPopen\n"
        " try:\n"
        "  subprocess.run([sys.executable, '-c', child, token], capture_output=True, timeout=0.4)\n"
        " except subprocess.TimeoutExpired as error:\n"
        "  sanitize_timeout(error)\n"
        "  raise error.with_traceback(None) from None\n"
    )
    _assert_nested_failure_hides_secret(test, "Mobile driver last checkpoint: viewport-wide-portrait-loaded")
