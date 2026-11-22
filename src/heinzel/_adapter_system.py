"""The real adapters of the `Clock` and `ProcessRunner` ports: the system clock and `subprocess` (`heinzel.ports`)."""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from heinzel.ports import RunningProcess

__all__ = ["SYSTEM_CLOCK", "SYSTEM_RUNNER", "SubprocessRunner", "SystemClock"]


class SystemClock:
    """Time as the operating system tells it."""

    def now(self) -> datetime:
        """The current time in UTC."""
        return datetime.now(UTC)

    def monotonic(self) -> float:
        """Seconds on the monotonic clock."""
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        """Block this thread for `seconds`."""
        time.sleep(seconds)

    def deadline(self, seconds: float) -> float:
        """The monotonic reading `seconds` from now."""
        return time.monotonic() + seconds


class SubprocessRunner:
    """Programs started with `subprocess`: no shell, no input, text output."""

    def run(
        self, command: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run `command` to its end; a non-zero exit is the caller's to read.

        Raises:
            OSError: If the program can't be started.
            subprocess.TimeoutExpired: If it runs longer than `timeout` seconds.
        """
        return subprocess.run(  # noqa: S603 - the caller's own fixed argument vector, never a shell
            list(command),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=None if env is None else dict(env),
        )

    def spawn(
        self, command: Sequence[str], *, env: Mapping[str, str] | None = None, capture: bool = False
    ) -> RunningProcess:
        """Start `command` and return the child; with `capture` its standard output is a text pipe.

        Raises:
            OSError: If the program can't be started.
        """
        return subprocess.Popen(  # noqa: S603 - the caller's own fixed argument vector, never a shell
            list(command),
            env=None if env is None else dict(env),
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            text=True,
        )

    def exec(self, command: Sequence[str], *, env: Mapping[str, str] | None = None) -> None:
        """Replace this process by `command`; it only returns if the replacement fails.

        Raises:
            OSError: If the program can't be executed.
        """
        os.execve(command[0], list(command), dict(os.environ if env is None else env))  # noqa: S606


SYSTEM_CLOCK = SystemClock()
SYSTEM_RUNNER = SubprocessRunner()
