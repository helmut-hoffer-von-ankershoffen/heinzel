"""A bounded wait for a recently stopped local UI to release its port."""

import errno
import socket
import time
from collections.abc import Callable

from heinzel.kernel import HeinzelError

WAIT_SECONDS = 30.0
RETRY_SECONDS = 1.0


def wait_for_port(  # noqa: PLR0913 - socket and time seams keep retries deterministic in tests
    open_socket: Callable[[], socket.socket],
    port: int,
    report: Callable[[str], None],
    *,
    clock: Callable[[], float] = time.monotonic,
    pause: Callable[[float], None] = time.sleep,
    timeout: float = WAIT_SECONDS,
) -> socket.socket:
    """Keep exclusive binding, retry a busy fixed port briefly, and explain the wait once.

    Raises:
        HeinzelError: If another process still holds the port after the wait.
        OSError: If binding fails for a reason other than an occupied fixed port.
        KeyboardInterrupt: If the person cancels the wait.
    """
    deadline = clock() + timeout
    announced = False
    while True:
        try:
            return open_socket()
        except OSError as error:
            if error.errno != errno.EADDRINUSE or port == 0:
                raise
            remaining = deadline - clock()
            if remaining <= 0:
                msg = (
                    f"Port {port} is still in use after {timeout:g} seconds. Another hz ui or application may be "
                    "running; stop it, or use hz ui --port 0 for a free port."
                )
                raise HeinzelError(msg) from None
            if not announced:
                report(
                    f"Port {port} is busy. If you just stopped hz ui, it can take about 30 seconds to become free. "
                    f"Waiting up to {timeout:g} seconds; press Ctrl-C to cancel."
                )
                announced = True
            pause(min(RETRY_SECONDS, remaining))
