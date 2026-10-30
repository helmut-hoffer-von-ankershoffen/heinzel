"""The README's install, use and uninstall instructions work against the candidate core artifact.

Every fenced block right after a `<!-- docs-test -->` marker is run with bash in a sandbox, in order, except that the
block that uninstalls runs last. A local core-only profile exercises the installer without real plugins.
The unpublished candidate update selects its built wheel explicitly; literal published-index updating is a release gate.
Needs network (PyPI dependencies) and `uv`. `hz system changelog --newer` asks GitHub: CI passes its
read-only `GITHUB_TOKEN`; without a token a rate-limited answer skips the test with the reason instead of failing it.
"""

import contextlib
import hashlib
import json
import os
import re
import selectors
import shlex
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import psutil
import pytest

pytestmark = pytest.mark.e2e

REPO = Path(__file__).resolve().parents[2]
UNINSTALL = "uv tool uninstall heinzel"
TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "HZ_SYSTEM_CHANGELOG__GITHUB_TOKEN")
RATE_LIMITED = re.compile(r"HTTP Error (403|429): rate limit exceeded", re.IGNORECASE)
MARKER = re.compile(r"<!-- docs-test -->\s*\n```(?:bash|sh)\n(.*?)\n```", re.DOTALL)
MAX_OUTPUT = 65536
GUARDIAN_UNCERTAIN = "README process-group cleanup is uncertain: guardian exited unexpectedly."
GUARDIAN_INVALID = "README guardian returned invalid status."
GUARDIAN_START = "README guardian could not start its command."
_LIVE_GUARDIAN_STATES = frozenset(
    {
        psutil.STATUS_RUNNING,
        psutil.STATUS_SLEEPING,
        psutil.STATUS_DISK_SLEEP,
        psutil.STATUS_STOPPED,
        psutil.STATUS_TRACING_STOP,
        psutil.STATUS_IDLE,
        psutil.STATUS_LOCKED,
        psutil.STATUS_WAITING,
        psutil.STATUS_WAKING,
        psutil.STATUS_PARKED,
    }
)
PROCEDURE_BUDGET = 100  # Reserve cleanup time within the unchanged outer 120s test limit.


def safe_diagnostics(text):
    """Redact session URL tokens and the three known GitHub token variables from diagnostics."""
    text = re.sub(r"(#token=)[A-Za-z0-9_-]+", r"\1<redacted>", text)
    for name in TOKENS:
        if value := os.environ.get(name):
            text = text.replace(value, "<redacted>")
    return text[-12000:]


class CapturedOutput:
    """Drain original-group command pipes while retaining only bounded diagnostic tails."""

    def __init__(self, process):
        self.selector = selectors.DefaultSelector()
        self.buffers = {"stdout": b"", "stderr": b""}
        for name in self.buffers:
            stream = getattr(process, name)
            if stream is not None:
                self.selector.register(stream, selectors.EVENT_READ, name)

    def pump(self, timeout=0):
        # A noisy writer cannot monopolize a deadline or allocate an unbounded read.
        for turn in range(16):
            ready = self.selector.select(timeout if turn == 0 else 0)
            if not ready:
                break
            for key, _events in ready:
                chunk = os.read(key.fd, 8192)
                if chunk:
                    name = key.data
                    self.buffers[name] = (self.buffers[name] + chunk)[-MAX_OUTPUT:]
                else:
                    self.selector.unregister(key.fileobj)

    def text(self, name):
        return self.buffers[name].decode("utf-8", errors="replace")

    def close(self):
        self.selector.close()


def remaining(deadline):
    """All README phases share one budget rather than competing with the outer timeout."""
    budget = deadline - time.monotonic()
    if budget <= 0:
        pytest.fail("The README procedure exhausted its bounded execution budget")
    return budget


_GUARDIAN = """import os, signal, struct, subprocess, sys
fd = int(sys.argv[1])
signal.signal(signal.SIGINT, lambda *_: None)
try:
    child = subprocess.Popen(sys.argv[2:])
except OSError:
    os.write(fd, b"E")
else:
    os.write(fd, b"R")
    status = child.wait()
    os.write(fd, b"D" + struct.pack("!i", status))
while True:
    signal.pause()
"""


class GuardedCommand:
    """Keep an owned live group leader until cleanup; command exit uses a private status pipe."""

    def __init__(self, command, *, env, cwd):
        self.command = command
        self.status_fd, writer = os.pipe()
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-I", "-c", _GUARDIAN, str(writer), *command],
                env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, pass_fds=(writer,),
            )  # fmt: skip
        except BaseException:
            os.close(self.status_fd)
            raise
        finally:
            os.close(writer)
        os.set_blocking(self.status_fd, False)
        try:
            self.guardian = psutil.Process(self.process.pid)
            self.guardian_started = self.guardian.create_time()
        except (psutil.Error, OSError):
            self.guardian = None
            self.guardian_started = None
        self.capture = CapturedOutput(self.process)
        self.buffer = bytearray()
        self.ready = False
        self.returncode = None

    def alive(self):
        """Observe but do not reap the process-group leader before its final signal."""
        if self.process.returncode is not None or self.guardian is None or self.guardian_started is None:
            return False
        try:
            return (
                self.guardian.is_running()
                and psutil.Process(self.process.pid).create_time() == self.guardian_started
                and self.guardian.status() in _LIVE_GUARDIAN_STATES
            )
        except (psutil.Error, OSError):
            return False

    def poll_command(self):
        """Read only the fixed ready/return-code messages, never command output or arguments."""
        try:
            chunk = os.read(self.status_fd, 7)
        except BlockingIOError:
            return
        if not chunk:
            raise RuntimeError(GUARDIAN_UNCERTAIN)
        self.buffer.extend(chunk)
        if len(self.buffer) > 6:
            raise RuntimeError(GUARDIAN_INVALID)
        if self.buffer and not self.ready:
            if self.buffer[0] != ord("R"):
                raise RuntimeError(GUARDIAN_START)
            del self.buffer[:1]
            self.ready = True
        if len(self.buffer) >= 5:
            if self.buffer[0] != ord("D") or self.returncode is not None:
                raise RuntimeError(GUARDIAN_INVALID)
            self.returncode = struct.unpack("!i", self.buffer[1:5])[0]
            del self.buffer[:5]

    def wait(self, timeout):
        """Wait for the real command, continuing to drain bounded output tails."""
        deadline = time.monotonic() + timeout
        while self.returncode is None:
            if not self.alive():
                raise RuntimeError(GUARDIAN_UNCERTAIN)
            self.poll_command()
            if self.returncode is not None:
                break
            budget = deadline - time.monotonic()
            if budget <= 0:
                raise subprocess.TimeoutExpired(self.command, timeout)
            self.capture.pump(min(0.05, budget))
        return self.returncode

    def stop(self):
        """Signal only the original group while its trusted guardian is alive; detached sessions are excluded."""
        try:
            if not self.alive():
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=1)
                raise RuntimeError(GUARDIAN_UNCERTAIN)
            status_error = None
            try:
                self.poll_command()
            except RuntimeError as error:
                status_error = error
            if status_error is None and self.ready and self.returncode is None:
                os.killpg(self.process.pid, signal.SIGINT)
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.wait(10)
            if not self.alive():
                with contextlib.suppress(subprocess.TimeoutExpired):
                    self.process.wait(timeout=1)
                raise RuntimeError(GUARDIAN_UNCERTAIN)
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait(timeout=5)
            self.capture.pump()
            if status_error is not None:
                raise status_error
        finally:
            self.capture.close()
            os.close(self.status_fd)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    stream.close()


def run_documented_command(command, *, env, cwd, deadline):
    """Capture bounded command output and clean up the owned original process group."""
    budget = remaining(deadline)
    started = time.monotonic()
    process = GuardedCommand(command, env=env, cwd=cwd)
    timed_out = False
    try:
        process.wait(remaining(deadline))
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        process.stop()
    output, problem = process.capture.text("stdout"), process.capture.text("stderr")
    if timed_out:
        heading = safe_diagnostics(f"README command exceeded its shared budget:\n{shlex.join(command)}")
        diagnostic = safe_diagnostics(f"{output}\n{problem}")
        elapsed = time.monotonic() - started
        pytest.fail(f"{heading}\nCommand began with {budget:.1f}s remaining and ran for {elapsed:.1f}s.\n{diagnostic}")
    return subprocess.CompletedProcess(command, process.returncode, output, problem)


def documented_blocks(path: Path) -> list[str]:
    """The shell blocks of a document that are marked for testing, the one that uninstalls last."""
    return sorted(MARKER.findall(path.read_text(encoding="utf-8")), key=lambda block: UNINSTALL in block)


def test_the_readme_has_tested_blocks():
    blocks = documented_blocks(REPO / "README.md")
    assert len(blocks) == 3
    assert blocks[0].strip() == (REPO / "docs/_data/install-command.txt").read_text().strip()
    assert "git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel" not in blocks[0]
    assert "hz system info" in blocks[1]
    assert "hz system plugin add" not in blocks[1]
    assert UNINSTALL in blocks[-1]


def launch_documented_app(command, env, tmp_path, deadline, *, bootstrap_wheels):
    """Run the actual foreground quick start, observe its built app, then stop only its owned session."""
    remaining(deadline)
    # Select the candidate only for uvx's bootstrap resolution. An inherited UV_FIND_LINKS would also become
    # persistent resolver policy in the managed tool, unlike an ordinary user's standard installation.
    program, *arguments = shlex.split(command)
    selected = [program, "--find-links", str(bootstrap_wheels), *arguments]
    log = tmp_path / "launch.log"
    process = GuardedCommand(selected, env=env, cwd=tmp_path)
    try:
        while time.monotonic() < deadline:
            process.poll_command()
            process.capture.pump(0.1)
            text = process.capture.text("stdout") + process.capture.text("stderr")
            match = re.search(r"http://127\.0\.0\.1:\d+/", text)
            if match:
                try:
                    with urlopen(match[0], timeout=2) as response:  # noqa: S310 - fixed loopback origin from own app
                        assert response.status == 200
                        assert b"<html" in response.read(1024 * 1024)
                    break
                except OSError:
                    pass
            assert process.alive(), "README guardian exited unexpectedly; process-group cleanup is uncertain."
            assert process.returncode is None, safe_diagnostics(text)
        else:
            diagnostic = safe_diagnostics(process.capture.text("stdout") + process.capture.text("stderr"))
            pytest.fail(f"Documented launcher did not serve its app:\n{diagnostic}")
    finally:
        process.stop()
        log.write_text(safe_diagnostics(process.capture.text("stdout") + process.capture.text("stderr")))
    assert process.returncode in {0, 130, -signal.SIGINT}, safe_diagnostics(
        process.capture.text("stdout") + process.capture.text("stderr")
    )


def candidate_update(block, wheel):
    """Select the unpublished wheel for exactly the README update command, without resolver-policy overrides."""
    pattern = r"(?m)^hz system update(?=\s*(?:#|$))"
    selected, count = re.subn(pattern, lambda _match: f"hz system update --source {shlex.quote(str(wheel))}", block)
    assert count <= 1, "The README update instruction must identify one command."
    return selected


@contextlib.contextmanager
def readme_phase(label, deadline):
    """Retain fixed phase timings on a failing hosted run without changing its budgets."""
    started = time.monotonic()
    try:
        yield
    finally:
        now = time.monotonic()
        sys.stderr.write(f"README phase={label} elapsed={now - started:.1f}s remaining={deadline - now:.1f}s\n")


def test_readme_instructions(tmp_path, fixture_managed_python):
    deadline = time.monotonic() + PROCEDURE_BUDGET
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    artifacts = tmp_path / "artifacts"
    with readme_phase("build", deadline):
        built = run_documented_command(
            ["uv", "build", "--wheel", "--out-dir", str(artifacts)], env=None, cwd=REPO, deadline=deadline
        )
    assert built.returncode == 0, safe_diagnostics(built.stdout + built.stderr)
    [wheel] = artifacts.glob("heinzel-*.whl")
    from heinzel import __version__

    profile = tmp_path / "standard.json"
    profile.write_text(
        json.dumps(
            {
                "format": 2,
                "core_version": __version__,
                "sdk": 1,
                "artifacts": [
                    {
                        "name": "heinzel",
                        "version": __version__,
                        "filename": wheel.name,
                        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                        "url": str(wheel),
                    }
                ],
            }
        )
    )
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith(("HZ_", "UV_"))},
        "UV_NO_CONFIG": "1",
        # Other E2E installs must not change shared cache inode metadata during this tool's inspection.
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "HZ_APP_PROFILE": str(profile),
        "HZ_SYSTEM_CREDENTIAL__BACKEND": "environment",
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HZ_CONFIG_HOME": str(tmp_path / "config"),
        "HZ_DATA_DIR": str(tmp_path / "data"),
        "HZ_CACHE_DIR": str(tmp_path / "cache"),
        "HZ_STATE_DIR": str(tmp_path / "state"),
        "HZ_LOG_DIR": str(tmp_path / "log"),
        "HZ_SYSTEM_DAEMON__UNIT_DIR": str(tmp_path / "units"),
        "HZ_SYSTEM_AGENT__SKILL_DIRS": f'["{tmp_path / "skills"}"]',
        "HZ_UI_PORT": str(port),
        "BROWSER": "true",  # Exercise the bare command without opening a human's browser.
    }
    env.pop("VIRTUAL_ENV", None)
    env.pop("UV_OVERRIDE", None)
    python_directory, python = fixture_managed_python
    env["UV_PYTHON_INSTALL_DIR"] = str(python_directory)
    env["UV_PYTHON"] = str(python)
    # A stale checkout is a common developer environment; all packaged heads must still use this wheel.
    shadow = tmp_path / "stale-source/heinzel"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text('raise RuntimeError("A stale checkout shadowed the selected SDK")\n')
    env["PYTHONPATH"] = str(shadow.parent)

    for block in documented_blocks(REPO / "README.md"):
        if block.strip() == (REPO / "docs/_data/install-command.txt").read_text().strip():
            with readme_phase("install-and-launch", deadline):
                launch_documented_app(block, env, tmp_path, deadline, bootstrap_wheels=artifacts)
            assert (tmp_path / "data/standard-app.json").is_file()
            assert (tmp_path / "tools/heinzel/bin/python").resolve(strict=True) == python
            continue
        with readme_phase("uninstall" if UNINSTALL in block else "documented-cli", deadline):
            # Bash expands SECONDS for each traced command, identifying a slow phase without extra operations.
            traced = "PS4='+${SECONDS}s ';\n" + candidate_update(block, wheel)
            result = run_documented_command(
                ["bash", "-exuo", "pipefail", "-c", traced], env=env, cwd=tmp_path, deadline=deadline
            )
        tokenless = not any(os.environ.get(name) for name in TOKENS)
        if result.returncode and tokenless and (limited := RATE_LIMITED.search(result.stderr)):
            pytest.skip(f"GitHub rate-limited the unauthenticated releases API ({limited[0]}); set GH_TOKEN")
        assert result.returncode == 0, safe_diagnostics(
            f"README block failed:\n{block}\n---\n{result.stdout}\n{result.stderr}"
        )

    assert not (tmp_path / "bin" / "hz").exists()
