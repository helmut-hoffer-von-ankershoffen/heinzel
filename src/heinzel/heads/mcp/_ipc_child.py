"""Ownership of a direct MCP startup child before vendor or provider threads exist."""

import os
import signal
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field

from heinzel.kernel import HeinzelError


def require_isolated_fork(refused: str) -> None:
    """Require a trusted isolated startup with exclusive direct-child reaping.

    Raises:
        HeinzelError: If this runtime cannot safely start an MCP child.
    """
    if (
        sys.platform not in {"darwin", "linux"}
        or not sys.flags.isolated
        or threading.current_thread() is not threading.main_thread()
        or threading.active_count() != 1
        or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL
    ):
        raise HeinzelError(refused)


def close_unrelated_descriptors(*kept: int) -> None:
    """In the direct child, retain only the descriptors explicitly issued for its job."""
    for entry in os.listdir("/dev/fd"):  # noqa: PTH208 - snapshot before closing inherited descriptors
        descriptor = int(entry)
        if descriptor not in kept:
            with suppress(OSError):  # Enumeration can include its own already-closed descriptor.
                os.close(descriptor)


@dataclass(slots=True)
class OwnedMcpChild:
    """Track the direct child until waitpid proves exit. Never act on a discovered PID."""

    pid: int = field(repr=False)
    _status: int | None = field(default=None, init=False, repr=False)

    def wait(self, seconds: float) -> int | None:
        """Return the retained wait status, or None while exit is unproven."""
        deadline = time.monotonic() + seconds
        while self._status is None:
            waited, status = os.waitpid(self.pid, os.WNOHANG)
            if waited == self.pid:
                self._status = status
            elif time.monotonic() >= deadline:
                break
            else:
                time.sleep(0.005)
        return self._status

    def retire(self, seconds: float, refused: str) -> None:
        """Bound cleanup after channel loss using only this still-unreaped direct child.

        Raises:
            HeinzelError: If the child cannot be reaped after bounded forced cleanup.
        """
        if self.wait(seconds) is None:
            with suppress(ProcessLookupError):
                os.kill(self.pid, signal.SIGKILL)
            if self.wait(seconds) is None:
                raise HeinzelError(refused)
