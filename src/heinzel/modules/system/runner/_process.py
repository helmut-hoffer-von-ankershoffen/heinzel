"""Running an agent CLI with hard limits: its own process group, a timeout that kills it, capped output."""

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO, NoReturn, cast

MAX_OUTPUT = 8 * 1024 * 1024
"""Bytes kept of stdout and of stderr each; the rest is drained and dropped, so a runaway agent can't fill memory."""
GRACE = 2.0
"""Seconds a stopped agent gets between SIGTERM and SIGKILL."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """How an agent process ended and what it printed, cut at the limit."""

    returncode: int | None
    """None when it was stopped for exceeding its timeout."""
    stdout: str
    stderr: str
    truncated: bool = False
    """Whether stdout or stderr went over the limit and was cut."""

    @property
    def timed_out(self) -> bool:
        """Whether it was stopped for exceeding its timeout."""
        return self.returncode is None

    def stderr_tail(self, size: int = 500) -> str:
        """The end of stderr, for a detail line."""
        return self.stderr.strip()[-size:]


Execute = Callable[..., Outcome]
"""Runs a command like `run_bounded`; runners take one so tests never start an agent."""


class _Capture:
    """Drains a pipe in a thread, keeping at most `limit` bytes."""

    def __init__(self, stream: IO[bytes], limit: int, on_line: Callable[[str], None] | None = None) -> None:
        self.chunks: list[bytes] = []
        self.size = 0
        self._limit = limit
        self._on_line = on_line
        self._thread = threading.Thread(target=self._drain, args=(stream,), daemon=True)
        self._thread.start()

    def _drain(self, stream: IO[bytes]) -> None:
        with stream:
            # Line by line when someone listens, so they see each line as it is written; never more than 64 KiB a read.
            while chunk := (stream.readline(65536) if self._on_line else stream.read(65536)):
                if self.size < self._limit:
                    kept = chunk[: self._limit - self.size]
                    self.chunks.append(kept)
                    if self._on_line is not None:
                        self._listen(kept)
                self.size += len(chunk)

    def _listen(self, line: bytes) -> None:
        try:
            self._on_line(line.decode(errors="replace"))  # type: ignore[misc]  # set when called
        except Exception:  # noqa: BLE001 - a listener's failure must not stop draining the agent's output
            self._on_line = None

    def text(self, wait: float) -> str:
        self._thread.join(wait)
        return b"".join(self.chunks).decode(errors="replace")

    @property
    def truncated(self) -> bool:
        return self.size > self._limit


def _write(stream: IO[bytes], data: bytes) -> None:
    try:
        with stream:
            stream.write(data)
    except BrokenPipeError:  # the agent exited without reading all of its input; its exit code tells why
        pass


def _signal_group(pid: int, signum: signal.Signals) -> None:
    # The group may be gone already; macOS reports EPERM instead of ESRCH when only zombies are left in it.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signum)


def _timeout(command: list[str], timeout: float) -> NoReturn:
    """Signal that the wait must stop.

    Raises:
        subprocess.TimeoutExpired: Always, for the caller to handle.
    """
    raise subprocess.TimeoutExpired(command, timeout)


def run_bounded(  # noqa: PLR0913 - the process's whole contract
    command: list[str],
    *,
    input: str | None,  # noqa: A002 - as in subprocess.run
    cwd: Path | None,
    env: Mapping[str, str],
    timeout: float,
    limit: int = MAX_OUTPUT,
    on_line: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> Outcome:
    """Run `command` in a new session; past `timeout` its whole process group gets SIGTERM, then SIGKILL.

    The command line is a list, never a shell string. Whatever the agent left running in its group after it exits is
    killed too, so no tool process outlives the run. `on_line` is called with each line of stdout as it arrives, from
    another thread; a listener that raises is dropped.
    """
    if cancel is not None and cancel.is_set():
        return Outcome(returncode=None, stdout="", stderr="")
    process = subprocess.Popen(  # noqa: S603 - an argument list built by the runner, executable resolved to a path
        command,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=dict(env),
        start_new_session=True,
    )
    stdout = _Capture(cast("IO[bytes]", process.stdout), limit, on_line)  # both are pipes, see above
    stderr = _Capture(cast("IO[bytes]", process.stderr), limit)
    if input is not None and process.stdin is not None:
        threading.Thread(target=_write, args=(process.stdin, input.encode()), daemon=True).start()
    returncode: int | None
    try:
        if cancel is None:
            returncode = process.wait(timeout)
        else:
            deadline = time.monotonic() + timeout
            while True:
                if cancel.is_set() or time.monotonic() >= deadline:
                    _timeout(command, timeout)
                try:
                    returncode = process.wait(min(0.05, max(0.001, deadline - time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    continue
    except subprocess.TimeoutExpired:
        returncode = None
        _signal_group(process.pid, signal.SIGTERM)
        try:
            process.wait(GRACE)
        except subprocess.TimeoutExpired:
            _signal_group(process.pid, signal.SIGKILL)
            process.wait()
    except BaseException:  # Ctrl-C reaches only us (the agent has its own session): stop it, then go on stopping
        _signal_group(process.pid, signal.SIGKILL)
        process.wait()
        raise
    _signal_group(process.pid, signal.SIGKILL)  # leftovers of the agent's group
    return Outcome(
        returncode=returncode,
        stdout=stdout.text(GRACE),
        stderr=stderr.text(GRACE),
        truncated=stdout.truncated or stderr.truncated,
    )


def whole_number(value: object) -> int | None:
    """`value` if it is an int and not a bool (JSON `true` is no token count), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def amount(value: object) -> float | None:
    """`value` as a float if it is a number and not a bool, else None."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None
