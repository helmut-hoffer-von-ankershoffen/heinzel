"""The one place that starts a process: the GitHub CLI `gh`, which owns authentication.

The adapter never sees a token: `gh` keeps the login (`gh auth login`), sends it, and refreshes it. `gh` is started
with an argument list, never a shell, under a timeout that kills it, with its output capped, and in an environment
without Heinzelmännchen's credentials or `gh`'s debug switches, which would print request headers. Tests replace
this service through the kernel (`kernel.bind(Gh, fake)`) or put a fake `gh` first on `PATH`.
"""

import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import IO, cast

from heinzel import HeinzelError
from heinzel.kernel import subprocess_environment

MAX_BYTES = 10_000_000
"""Largest answer read from `gh`; a page of 100 pull requests, which embed their repositories, is a few megabytes."""
MAX_ERROR_BYTES = 65_536
"""Bytes of `gh`'s standard error kept; enough for any message it prints."""
AUTH_TIMEOUT = 10.0
"""Seconds `gh auth token` gets; it reads `gh`'s local configuration or the keychain, never the network."""
POLL = 0.05
"""Seconds between checks of a running `gh` for its exit, its timeout and its output cap."""
DRAIN_WAIT = 5.0
"""Seconds to wait for `gh`'s output after it ended."""
INSTALL = "https://cli.github.com"
DROPPED = frozenset(
    {"GH_DEBUG", "DEBUG", "GODEBUG", "GH_FORCE_TTY", "CLICOLOR_FORCE", "GH_REPO", "GH_BROWSER", "BROWSER"}
)
"""Variables `gh` must not see: debug output (`GODEBUG=http2debug=2` too) prints request headers with the token; the
others make its output unpredictable."""


def login_fix(host: str) -> str:
    """The command that logs `gh` in to `host`."""
    return f"gh auth login --hostname {host}"


@dataclass(frozen=True, slots=True)
class GhResult:
    """How a `gh` call ended and what it printed, at most `MAX_BYTES` + 1 of stdout and `MAX_ERROR_BYTES` of stderr."""

    returncode: int | None
    """None when it was stopped for exceeding its timeout."""
    stdout: bytes
    stderr: str
    truncated: bool = False
    """Whether stdout went over `MAX_BYTES`; `gh` was stopped then."""


def environment(host: str) -> dict[str, str]:
    """`gh`'s environment: the current one without credentials or `DROPPED`, pinned to `host`, never interactive."""
    env = {key: value for key, value in subprocess_environment().items() if key not in DROPPED}
    env.update(
        GH_HOST=host,
        GH_PROMPT_DISABLED="1",
        GH_NO_UPDATE_NOTIFIER="1",
        GH_NO_EXTENSION_UPDATE_NOTIFIER="1",
        GH_SPINNER_DISABLED="1",
        GH_PAGER="",
        NO_COLOR="1",
    )
    return env


class _Capture:
    """Drains a pipe in a thread, keeping at most `limit` + 1 bytes; sets `overflowed` once it has seen more."""

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        self.chunks: list[bytes] = []
        self.size = 0
        self.overflowed = threading.Event()
        self._limit = limit
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream: IO[bytes]) -> None:
        with stream:
            while chunk := stream.read(65536):
                if self.size <= self._limit:
                    self.chunks.append(chunk[: self._limit + 1 - self.size])
                self.size += len(chunk)
                if self.size > self._limit:
                    self.overflowed.set()

    def data(self) -> bytes:
        # A child `gh` started (a credential helper) may hold the pipe open after `gh` is gone: don't wait for it.
        self._thread.join(DRAIN_WAIT)
        return b"".join(self.chunks)


class Gh:
    """Runs `gh` commands for the adapter and answers whether `gh` is installed and logged in, without the network."""

    def executable(self) -> str | None:
        """The path of `gh` on `PATH`, or None if it isn't installed."""
        return shutil.which("gh")

    def _require(self) -> str:
        if (path := self.executable()) is None:
            msg = f"The GitHub CLI `gh` is not installed; install it from {INSTALL}, then run `gh auth login`."
            raise HeinzelError(msg)
        return path

    def run(self, args: list[str], *, host: str, timeout: float) -> GhResult:
        """Run `gh` with `args` for `host`; past `timeout` seconds, or past `MAX_BYTES` of output, it is killed.

        Raises:
            HeinzelError: If `gh` is not installed or can't be started.
        """
        command = [self._require(), *args]
        try:
            process = subprocess.Popen(  # noqa: S603 - an argument list, gh resolved with shutil.which, no shell
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment(host),
            )
        except OSError as exc:
            msg = f"Could not start the GitHub CLI `gh` ({type(exc).__name__}); reinstall it from {INSTALL}."
            raise HeinzelError(msg) from None
        stdout = _Capture(cast("IO[bytes]", process.stdout), MAX_BYTES)  # both are pipes, see above
        stderr = _Capture(cast("IO[bytes]", process.stderr), MAX_ERROR_BYTES)
        returncode: int | None = None
        deadline = time.monotonic() + timeout
        try:
            # Kill from this thread only, after a short wait: never while another thread may be reaping `gh`.
            while not stdout.overflowed.is_set() and (left := deadline - time.monotonic()) > 0:
                try:
                    returncode = process.wait(min(left, POLL))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:  # timed out, too much output, or interrupted
                process.kill()
                process.wait()
        out = stdout.data()
        return GhResult(
            returncode=returncode,
            stdout=out,
            stderr=stderr.data()[:MAX_ERROR_BYTES].decode(errors="replace"),
            truncated=stdout.overflowed.is_set(),
        )

    def logged_in(self, host: str) -> bool:
        """Whether `gh` has a login for `host`: `gh auth token`, whose output, the token, goes to /dev/null unread.

        `gh auth status` would check the token with GitHub; doctor never contacts it.

        Raises:
            HeinzelError: If `gh` is not installed.
        """
        command = [self._require(), "auth", "token", "--hostname", host]
        try:
            done = subprocess.run(  # noqa: S603 - an argument list, gh resolved with shutil.which, no shell
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,  # the token: never read into this process
                stderr=subprocess.DEVNULL,
                env=environment(host),
                timeout=AUTH_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0
