"""Bounded tracing for the isolated, headless sign-in fixture."""

import array
import contextlib
import faulthandler
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import termios
import time
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests.e2e._mobile_server_output import check_driver, redact, sanitize_timeout

_PHASES = r"(?:handler-enter|handler-pending|handler-return|prepare-enter|prepare-refused|prepare-return)"
_BROWSER_PHASES = r"(?:chromium|webkit) (?:route-enter|fetch-start|fetch-return)"
_STACK_FILES = ("_chatgpt_signin.py", "_chatgpt.py", "rich/console.py", "logging/__init__.py", "h11_impl.py")
_TRACE_LIMIT = 8192
_FIXTURE_ENVIRONMENT = {
    "BROWSER",
    "CI",
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PLAYWRIGHT_BROWSERS_PATH",
    "PLAYWRIGHT_DOWNLOAD_HOST",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONPATH",
    "TMPDIR",
    "UV_OFFLINE",
}


def browser_trace(stderr):
    """Keep only fixed engine and phase names and the fixture clock."""
    return [line for line in stderr.splitlines() if re.fullmatch(rf"SIGN_IN_BROWSER {_BROWSER_PHASES} \d{{13}}", line)]


def report(process, phases, stacks):
    """Sample the owned child on failure without draining its stderr or printing its output."""
    if process.poll() is None and process.stderr is not None:
        pending = array.array("i", [0])
        fcntl.ioctl(process.stderr.fileno(), termios.FIONREAD, pending)
        sys.stderr.write(f"SIGN_IN_STDERR_PENDING {pending[0]}\n")
        with contextlib.suppress(ProcessLookupError):
            process.send_signal(signal.SIGUSR1)
    with phases.open(encoding="utf-8") as trace:
        for line in trace.read(_TRACE_LIMIT).splitlines():
            if re.fullmatch(rf"SIGN_IN_SERVER {_PHASES} \d{{13}}", line):
                sys.stderr.write(line + "\n")
    # The process writes the signal sample asynchronously. Read at most one bounded trace.
    for _ in range(10):
        if stacks.stat().st_size or process.poll() is not None:
            break
        time.sleep(0.02)
    with stacks.open(encoding="utf-8", errors="replace") as stack:
        for line in stack.read(_TRACE_LIMIT).splitlines():
            match = re.match(r'\s*File "([^"]+)", line (\d{1,6}) in ', line)
            if match:
                path, number = match.groups()
                label = next((name for name in _STACK_FILES if path.endswith("/" + name)), None)
                if label:
                    sys.stderr.write(f"SIGN_IN_STACK {label}:{number}\n")


def _drive_browser(command, env, repo, token):
    try:
        driven = subprocess.run(command, cwd=repo, capture_output=True, env=env, text=True, check=False, timeout=180)
    except subprocess.TimeoutExpired as error:
        stderr = error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr
        for line in browser_trace(stderr or ""):
            sys.stderr.write(line + "\n")
        sanitize_timeout(error)
        raise error.with_traceback(None) from None
    for line in browser_trace(driven.stderr):
        sys.stderr.write(line + "\n")
    check_driver(driven.returncode, redact(driven.stderr, token))
    return json.loads(driven.stdout.strip().splitlines()[-1])


def drive_traced_signin(env, tmp_path, run, repo, verify_stream):
    """Keep the original undrained pipe and capture fixed markers from the owned child."""
    env = {key: value for key, value in env.items() if key.startswith("HZ_") or key in _FIXTURE_ENVIRONMENT}
    phases, stacks = tmp_path / "sign-in-phases.txt", tmp_path / "sign-in-stacks.txt"
    command = [sys.executable, "-m", "tests.e2e._signin_trace_server", str(phases), str(stacks)]
    server = subprocess.Popen(command, cwd=repo, env=env, stderr=subprocess.PIPE, text=True)
    try:
        assert server.stderr is not None
        line = next(line for line in server.stderr if "runs at" in line)
        url = line.strip().rsplit(" ", 1)[-1]
        assert url.startswith("http://127.0.0.1:")
        assert "#token=" in url
        token = parse_qs(urlsplit(url).fragment).get("token", [""])[0]
        verify_stream(url)
        shots = os.environ.get("HZ_SCREENSHOTS", str(tmp_path / "screenshots"))
        return _drive_browser([*run, "exec", "node", "e2e/chatgpt-sign-in.mjs", url, shots], env, repo, token)
    except BaseException:
        with contextlib.suppress(OSError):
            report(server, phases, stacks)
        raise
    finally:
        original = sys.exception()
        try:
            _stop_server(server)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            if original is None:
                raise
            original.add_note("Sign-in fixture server cleanup was uncertain.")
        finally:
            if server.stderr is not None:
                server.stderr.close()


def _stop_server(server):
    if server.poll() is None:
        server.terminate()
    try:
        server.wait(30)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(5)
        msg = "Sign-in fixture server required forced cleanup."
        raise RuntimeError(msg) from None


def main():
    """Run the ordinary CLI with test-only markers on a private file descriptor."""
    from heinzel import HeinzelError
    from heinzel.heads.cli import main as cli
    from heinzel.heads.web._chatgpt_signin import ChatGptSignIn
    from heinzel.modules.ai.access._chatgpt import ChatGptAccess

    phases, stacks = map(Path, sys.argv[1:])
    if os.environ.get("HZ_SYSTEM_CREDENTIAL__BACKEND") != "environment" or any(
        key.startswith("HZ_CREDENTIAL_") for key in os.environ
    ):
        msg = "Sign-in trace requires the isolated environment backend."
        raise RuntimeError(msg)
    with phases.open("x", encoding="utf-8") as phase, stacks.open("xb") as stack:
        faulthandler.register(signal.SIGUSR1, file=stack, all_threads=True)

        def mark(name):
            if phase.tell() < _TRACE_LIMIT:
                phase.write(f"SIGN_IN_SERVER {name} {time.time_ns() // 1_000_000}\n")
                phase.flush()

        start = ChatGptSignIn.start
        prepare = ChatGptAccess.prepare_web_login

        @wraps(start)
        async def trace_start(self, request):
            mark("handler-pending" if self._callback is not None else "handler-enter")
            result = await start(self, request)
            mark("handler-return")
            return result

        def trace_prepare(self, redirect_uri):
            mark("prepare-enter")
            try:
                result = prepare(self, redirect_uri)
            except HeinzelError:
                mark("prepare-refused")
                raise
            mark("prepare-return")
            return result

        ChatGptSignIn.start = trace_start
        ChatGptAccess.prepare_web_login = trace_prepare
        sys.argv[1:] = ["ui", "serve", "--port", "0"]
        cli()


if __name__ == "__main__":
    main()
