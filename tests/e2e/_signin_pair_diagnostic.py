"""A bounded diagnostic of the original canonical macOS browser case ordering."""

import contextlib
import os
import re
import signal
import subprocess
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "UV_PYTHON", "PLAYWRIGHT_BROWSERS_PATH", "SYSTEMROOT")
CASES = (
    "tests/e2e/ai_access_install_ownership_test.py",
    "tests/e2e/web_ui_test.py::test_local_chatgpt_sign_in_fits_phone_viewports",
)
MARKER = re.compile(
    r"SIGN_IN_(?:BROWSER (?:chromium|webkit) (?:route-enter|fetch-start|fetch-return) \d{13}|"
    r"SERVER (?:handler-enter|handler-pending|handler-return|prepare-enter|prepare-refused|prepare-return) \d{13}|"
    r"STDERR_PENDING \d+|STACK (?:_chatgpt_signin[.]py|_chatgpt[.]py|rich/console[.]py|"
    r"logging/__init__[.]py|h11_impl[.]py):\d{1,6})"
)


def pair_environment(source, screenshots):
    """Recreate focused_coverage.run_lane's explicit child environment."""
    env = {key: source[key] for key in ENV_KEYS if key in source}
    env.update(UV_FROZEN="1", HZ_SCREENSHOTS=str(screenshots))
    return env


def pair_command(junit):
    """Retain the original canonical ordered cases and pytest selection."""
    return [
        "uv",
        "run",
        "pytest",
        "-m",
        "e2e",
        "-n",
        "0",
        "-q",
        "-p",
        "no:cacheprovider",
        "--session-timeout=900",
        f"--junitxml={junit}",
        *CASES,
    ]


def drain(process, markers):
    """Continuously drain the diagnostic pipe, retaining only bounded fixed markers."""
    assert process.stdout is not None
    pending = b""
    while chunk := process.stdout.read(4096):
        for byte in chunk:
            if byte == 10:
                line = pending.decode("utf-8", errors="replace").strip()
                if len(markers) < 100 and MARKER.fullmatch(line):
                    markers.append(line)
                pending = b""
            elif len(pending) < 1024:
                pending += bytes((byte,))


def run_pair(process, reader):
    """Wait within the original lane budget, always cleaning up owned children."""
    try:
        reader.start()
        return process.wait(timeout=1020)
    except subprocess.TimeoutExpired:
        return None
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        if reader.ident is not None:
            reader.join(timeout=10)
            if reader.is_alive():
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                reader.join(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
        if reader.is_alive():
            msg = "Diagnostic output reader did not stop"
            raise RuntimeError(msg)


@pytest.mark.timeout(1100)
def test_canonical_ordered_signin_diagnostic(tmp_path):
    """Run the original pair with bounded, sanitized failure markers; a pass is inconclusive."""
    env = pair_environment(os.environ, tmp_path / "screenshots")
    process = subprocess.Popen(
        pair_command(tmp_path / "pair.xml"),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    markers = []
    reader = threading.Thread(target=drain, args=(process, markers), daemon=True)
    status = run_pair(process, reader)
    # Fixed markers only: never expose tokens, URLs, credential values, or raw child stderr.
    evidence = "\n".join(markers) + "\n"
    (tmp_path / "sanitized-signin-markers.txt").write_text(evidence, encoding="utf-8")
    assert status == 0, f"Ordered sign-in diagnostic failed; sanitized markers:\n{evidence}"
