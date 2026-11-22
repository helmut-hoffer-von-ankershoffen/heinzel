"""Automatic updates on the web head: the routes of the update plan and the middleware that marks work (ADR 0116).

The plan, its schedule and its state file live in the update context (`heinzel.modules.system.updater`). The head
serves them, hands them the client context's guarded state folder and refreshes the desktop app, which is a head's.
"""

import logging
import os
import platform
import threading
import weakref
from collections.abc import AsyncGenerator, AsyncIterable, Awaitable, Callable
from pathlib import Path
from subprocess import SubprocessError
from typing import Any, ClassVar

from fastapi import FastAPI, HTTPException, Response

from heinzel import HeinzelError
from heinzel.heads._stream import WORK
from heinzel.kernel import Installation
from heinzel.modules.system.client import open_updater_file, private_directory
from heinzel.modules.system.updater.api import Activity, AutoUpdateStatus, Schedule, StateFiles

__all__ = [
    "AUTO_UPDATE_PATH",
    "STATE_FILES",
    "Activity",
    "AutoUpdater",
    "add_auto_update",
    "as_work",
    "counts_as_work",
    "held_until_sent",
]

log = logging.getLogger(__name__)
STATE_FILES = StateFiles(private_directory, open_updater_file)
"""The client context's checks on the state folder, which the plan's file and locks go through."""


class AutoUpdater(Schedule):
    """The update plan of one `hz ui serve` server, on the client context's guarded state folder."""

    files: ClassVar[StateFiles] = STATE_FILES

    def _refresh_desktop(self) -> None:
        """Bring an already installed desktop shell up to this running stable Core."""
        if platform.system() != "Darwin":
            return
        receipt = Path.home() / ".config/heinzel/desktop-installation.json"
        if not os.path.lexists(receipt):
            return
        from heinzel.heads._desktop_installation import install_desktop  # noqa: PLC0415 - managed Mac app only

        try:
            entries = self._kernel.resolve(Installation).tool_entrypoints()
            executable = next((entry for entry in entries if entry.name == "hz"), None)
            if executable is not None:
                status = install_desktop(self._version, executable, update_only=True)
                if status in {"updated", "deferred", "installed-pending"}:
                    log.info("desktop automatic update: %s", status)
        except (HeinzelError, OSError, SubprocessError) as exc:
            log.warning("desktop automatic update could not finish: %s", exc)


AUTO_UPDATE_PATH = "/auto-update"


def add_auto_update(app: FastAPI, updater: AutoUpdater | None) -> None:
    """`GET /auto-update`, `POST /auto-update/postpone` and `POST /auto-update/dismiss` behind the web head's token."""

    def require() -> AutoUpdater:
        if updater is None:
            raise HTTPException(status_code=409, detail="This server doesn't run automatic updates.")
        return updater

    refused: dict[int | str, dict[str, Any]] = {409: {"description": "This server doesn't run automatic updates."}}

    @app.get(AUTO_UPDATE_PATH, tags=["web"], responses=refused)
    def status(response: Response) -> AutoUpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        return require().status()

    @app.post(f"{AUTO_UPDATE_PATH}/postpone", tags=["web"], responses=refused)
    def postpone_update(response: Response) -> AutoUpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        return require().postpone()

    @app.post(f"{AUTO_UPDATE_PATH}/check", tags=["web"], responses=refused)
    def check_now(response: Response) -> AutoUpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        return require().recheck()

    @app.post(f"{AUTO_UPDATE_PATH}/dismiss", tags=["web"], responses=refused)
    def dismiss_message(response: Response) -> AutoUpdateStatus:
        response.headers["Cache-Control"] = "no-store"
        return require().dismiss()


def held_until_sent(response: Response, leave: Callable[[], None]) -> Response:
    """`response`, calling `leave` once its body has been sent, the browser left, or the body was dropped unsent.

    A middleware's `call_next` returns when the headers go out; a streamed chat turn runs while its body streams, so
    the work it holds off an automatic update for ends only then.
    """
    body: AsyncIterable[Any] | None = getattr(response, "body_iterator", None)
    if body is None:
        leave()
        return response
    lock, left = threading.Lock(), [False]

    def done() -> None:
        with lock:
            if left[0]:
                return
            left[0] = True
        leave()

    async def held() -> AsyncGenerator[Any]:
        try:
            async for chunk in body:
                yield chunk
        finally:
            done()

    wrapped = held()
    weakref.finalize(wrapped, done)  # never iterated (the browser left before the first byte): still let go
    setattr(response, "body_iterator", wrapped)  # noqa: B010 - only streamed responses have it
    return response


async def as_work[R](activity: Activity, call_next: Callable[[R], Awaitable[Response]], request: R) -> Response:
    """Run a request that changes state as work: from its start until its response body has ended.

    A streamed chat turn runs while its body streams, and its operation's thread can outlive the stream when the
    browser leaves; `WORK` lets the stream hold the activity for that thread as well.
    """
    url = getattr(request, "url", None)
    what = f"{getattr(request, 'method', 'POST')} {getattr(url, 'path', '')}".strip()
    activity.enter(what)
    token = WORK.set((lambda: activity.enter(what), lambda: activity.leave(what)))
    try:
        response = await call_next(request)
    except BaseException:
        activity.leave(what)
        raise
    finally:
        WORK.reset(token)
    return held_until_sent(response, lambda: activity.leave(what))


def counts_as_work(method: str, path: str) -> bool:
    """Whether a request to the web head's API changes state and so holds off an update while it runs."""
    if method in {"GET", "HEAD", "OPTIONS"}:
        return False
    return not path.startswith(f"/api{AUTO_UPDATE_PATH}")
