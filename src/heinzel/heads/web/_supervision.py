"""The head's side of web supervision: the terminal and the browser (the service lives in lifecycle)."""

import sys
import webbrowser
from collections.abc import Callable
from pathlib import Path

from heinzel.modules.system.lifecycle._supervision import (
    SUPERVISED_LOG,
    SupervisedWeb,
    redirect_supervised_log,
    restart_when_settled,
)
from heinzel.modules.system.lifecycle._supervision import WebPublication as _WebPublication

__all__ = ["SUPERVISED_LOG", "SupervisedWeb", "WebPublication", "redirect_supervised_stderr", "restart_when_settled"]


def redirect_supervised_stderr(log_dir: Path) -> Path | None:
    """Send this process's stderr to the supervised server's owner-only log; None, leaving it as it was, if unsafe."""
    return redirect_supervised_log(log_dir, sys.stderr.flush)


class WebPublication(_WebPublication):
    """The publication that opens the person's browser."""

    def publish(
        self, announce: Callable[[str], None], *, browser: bool, opener: Callable[[str], object] | None = None
    ) -> None:
        """Publish after readiness, opening the default browser when asked."""
        super().publish(announce, browser=browser, opener=opener or webbrowser.open)
