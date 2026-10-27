"""Boot the installed shared web head only after the minimal broker established ownership."""

# ruff: noqa: INP001 - standalone sealed worker, intentionally outside the installed application imports

from __future__ import annotations

import os
import signal
import site
import sys
import threading
from pathlib import Path


def main() -> None:
    """Keep the broker pipe as a lease across every supported backend exec/restart."""
    closed = threading.Event()

    def parent_closed() -> None:
        os.read(sys.stdin.fileno(), 1)
        closed.set()
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=parent_closed, daemon=True).start()
    # The interpreter starts with -S, so even installed .pth hooks run only after the parent-death lease is armed.
    site.main()

    from heinzel.heads import web  # noqa: PLC0415 - parent-death watcher starts before any plugin imports
    from heinzel.kernel import boot  # noqa: PLC0415 - parent-death watcher starts before kernel bootstrap

    def restart(executable: str, _arguments: list[str], environment: dict[str, str]) -> None:
        if closed.is_set():
            return
        os.execve(  # noqa: S606 - current verified interpreter and this sealed worker, no shell
            executable, [executable, "-I", "-S", "-B", str(Path(__file__).resolve())], environment
        )

    web.serve(boot(), browser=False, announce=lambda _url: None, execute=restart)


if __name__ == "__main__":
    main()
