"""A bounded diagnostic of the original canonical macOS browser case ordering."""

import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_KEYS = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "UV_PYTHON", "PLAYWRIGHT_BROWSERS_PATH", "SYSTEMROOT")
CASES = (
    "tests/e2e/ai_access_install_ownership_test.py",
    "tests/e2e/web_ui_test.py::test_local_chatgpt_sign_in_fits_phone_viewports",
)
MARKER = re.compile(
    r"SIGN_IN_(?:BROWSER (?:chromium|webkit) (?:route-enter|fetch-start|fetch-return) \d{13}|"
    r"SERVER (?:handler-enter|handler-pending|handler-return|prepare-enter|prepare-refused|prepare-return) \d{13}|"
    r"STDERR_PENDING \d+|CLEANUP_UNCERTAIN|STACK (?:_chatgpt_signin[.]py|_chatgpt[.]py|rich/console[.]py|"
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


def run_command(process, reader, timeout=1080):
    """Wait within the original lane budget, always cleaning up owned children."""
    try:
        reader.start()
        return process.wait(timeout=timeout)
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
        if reader.is_alive():
            msg = "Diagnostic output reader did not stop"
            raise RuntimeError(msg)
        if process.stdout is not None:
            process.stdout.close()


def execute(command, env):
    """Run one canonical command and retain only bounded, sanitized markers."""
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    markers = []
    reader = threading.Thread(target=drain, args=(process, markers), daemon=True)
    try:
        return run_command(process, reader), markers
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None, [*markers[:99], "SIGN_IN_CLEANUP_UNCERTAIN"]


def summarize_junit(path):
    """Keep only fixed case names and outcomes; delete raw private test output."""
    expected = (
        "test_client_install_survives_dialog_close_and_reopen",
        "test_local_chatgpt_sign_in_fits_phone_viewports",
    )
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return {"valid": False, "cases": []}
        root = ET.fromstring(path.read_bytes())  # noqa: S314 - owned local pytest output, bounded above
        cases = [
            (case.get("name"), any(case.find(tag) is not None for tag in ("failure", "error", "skipped")))
            for case in root.iter("testcase")
        ]
        return {
            "valid": tuple(name for name, _ in cases) == expected and not any(bad for _, bad in cases),
            "cases": [name for name, _ in cases if name in expected],
        }
    except (OSError, ET.ParseError):
        return {"valid": False, "cases": []}
    finally:
        path.unlink(missing_ok=True)


def main():
    """Run the original pair directly; a passing diagnostic remains inconclusive."""
    if len(sys.argv) != 2:
        msg = "Supply one new private evidence directory"
        raise SystemExit(msg)
    output = Path(sys.argv[1]).resolve()
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    env = pair_environment(os.environ, output / "screenshots")
    prep, _ = execute(["make", "e2e-prepare"], env)
    if prep != 0:
        msg = "Diagnostic preparation failed or timed out"
        raise SystemExit(msg)
    status, markers = execute(pair_command(output / "pair.xml"), env)
    summary = summarize_junit(output / "pair.xml")
    # Screenshots may contain fixture content; the diagnostic retains only fixed, bounded evidence.
    if (output / "screenshots").exists():
        shutil.rmtree(output / "screenshots")
    evidence = "\n".join(markers) + "\n"
    (output / "sanitized-signin-markers.txt").write_text(evidence, encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if status != 0 or not summary["valid"]:
        sys.stderr.write(evidence)
        msg = "Ordered sign-in diagnostic failed or timed out"
        raise SystemExit(msg)
    sys.stdout.write("Ordered sign-in diagnostic passed; cause remains unresolved\n")


if __name__ == "__main__":
    main()
