"""The `runtime` health check: one owned web backend per user, no leftover servers, the served version installed."""

import subprocess
from collections.abc import Callable
from pathlib import Path

from heinzel import Check, HeinzelError, Status
from heinzel.modules.system.lifecycle._service import WebStatus
from heinzel.modules.system.lifecycle._strays import Stray, strays


def runtime_checks(
    status: Callable[[], WebStatus],
    prefix: Path | None,
    installed: str,
    *,
    find: Callable[[Path], list[Stray]] = strays,
) -> list[Check]:
    """The findings: the owned service serves the installed version, and no app-spawned server is left.

    `prefix` is the owned uv tool, or None in a dev checkout (whose own servers are its developer's business).
    """
    return [_service(status(), installed), *_leftovers(prefix, find)]


def _service(observed: WebStatus, installed: str) -> Check:
    name = "runtime: one backend"
    if not observed.installed:
        return Check(
            name=name,
            status=Status.UP,
            detail="no web service installed; the desktop apps install it on first launch (`hz ui attach`)",
        )
    if not observed.ready:
        return Check(
            name=name,
            status=Status.DEGRADED,
            detail=observed.problem or "the owned web service is installed but not ready",
            fix="Inspect it: `hz ui status`; `hz ui attach` starts it",
        )
    if observed.version != installed:
        return Check(
            name=name,
            status=Status.DEGRADED,
            detail=f"the web service serves {observed.version}, but {installed} is installed",
            fix="Restart it into the installed version: `hz ui restart`",
        )
    return Check(name=name, status=Status.UP, detail=f"pid {observed.pid} on port {observed.port} serves {installed}")


def _leftovers(prefix: Path | None, find: Callable[[Path], list[Stray]]) -> list[Check]:
    name = "runtime: no leftover servers"
    if prefix is None:
        return [Check(name=name, status=Status.UP, detail="not an installed tool; dev servers are not checked")]
    try:
        found = find(prefix)
    except (OSError, subprocess.SubprocessError, HeinzelError) as exc:
        return [Check(name=name, status=Status.DEGRADED, detail=f"the process list could not be read ({exc})")]
    orphans = [stray.pid for stray in found if stray.orphaned]
    owned = [stray.pid for stray in found if not stray.orphaned]
    checks: list[Check] = []
    if orphans:
        pids = " ".join(str(pid) for pid in orphans)
        checks.append(
            Check(
                name=name,
                status=Status.DOWN,
                detail=f"a web server an older app left still runs (pid {pids}), with its own auto-updater",
                fix=f"Open the Heinzel app once (it stops them), or stop them yourself: `kill -INT {pids}`",
            )
        )
    if owned:
        pids = " ".join(str(pid) for pid in owned)
        checks.append(
            Check(
                name=name,
                status=Status.DEGRADED,
                detail=f"an older app still runs its own web server (pid {pids})",
                fix="Quit the Heinzel app and open it again: the new app attaches to the one service",
            )
        )
    return checks or [Check(name=name, status=Status.UP, detail="no app-spawned web server runs")]
