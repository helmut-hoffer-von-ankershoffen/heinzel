"""Owned server output drains without exposing known credentials."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e._mobile_server_output import MobileServerOutput, redact, sanitize_timeout, timeout_stage

pytestmark = pytest.mark.unit


def test_redacts_fragments_authorization_and_known_token():
    text = "url http://127.0.0.1:123/#token=fixture-secret Authorization: Bearer other-secret fixture-secret"
    text += ' {"authorization":"json-secret"}'
    safe = redact(text, "fixture-secret")
    assert "fixture-secret" not in safe
    assert "other-secret" not in safe
    assert "json-secret" not in safe


def test_flood_is_drained_and_tail_bounded_without_losing_startup():
    code = (
        "import sys; s=sys.stderr.buffer; "
        "s.write(b'x'*100000+b'\\n'); s.write(b'runs at http://127.0.0.1:1/#token=fixture-secret\\n'); "
        "s.flush(); s.write(b'y'*100000+b'\\n'); s.write(b'Authorization: Bearer other-secret\\n'); s.flush()"
    )
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    capture = MobileServerOutput(child)
    try:
        assert capture.wait_url() == "http://127.0.0.1:1/#token=fixture-secret"
        assert child.wait(timeout=5) == 0
        capture.close()
        text = capture.diagnostic("fixture-secret")
        assert len(text.encode()) <= 32768
        assert "fixture-secret" not in text
        assert "other-secret" not in text
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        if child.stderr is not None:
            child.stderr.close()


def test_crash_before_cleanup_is_observed_and_missing_token_hides_stderr():
    child = subprocess.Popen(
        [sys.executable, "-c", 'import sys; sys.stderr.write("private-value\\n");sys.exit(17)'], stderr=subprocess.PIPE
    )
    capture = MobileServerOutput(child)
    try:
        assert child.wait(timeout=5) == 17
        assert child.poll() == 17
        capture.close()
        assert "private-value" not in capture.diagnostic()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        if child.stderr is not None:
            child.stderr.close()


def test_timeout_retains_type_and_deadline_without_command_or_output():
    error = subprocess.TimeoutExpired(["url#token=secret"], 300, output=b"secret", stderr=b"secret")
    sanitize_timeout(error)
    assert error.timeout == 300
    assert "secret" not in str(error)
    assert "secret" not in repr(error)
    assert "secret" not in repr(error.args)
    assert error.output is None
    assert error.stderr is None
    assert error.__notes__ == ["Mobile driver last checkpoint: unavailable"]


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
    marker = secrets.token_hex(16)
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
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
    assert "<redacted>" in result.stdout


def test_timeout_stage_is_allowlisted_and_never_echoes_private_driver_data():
    secret = "fixture-private-token"
    captured = (
        f"Authorization: Bearer {secret}\nMOBILE_STAGE:viewport-wide-portrait-loaded\n"
        f"MOBILE_STAGE:viewport-wide-portrait-loaded {secret}\n"
    ).encode()
    assert timeout_stage(captured) == "viewport-wide-portrait-loaded"
    assert timeout_stage(f"MOBILE_STAGE:{secret}\n") == "unavailable"
    assert timeout_stage(None) == "unavailable"


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
    marker = secrets.token_hex(16)
    env = {
        "PATH": os.defpath,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
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
    assert "Mobile driver last checkpoint: viewport-wide-portrait-loaded" in result.stdout
