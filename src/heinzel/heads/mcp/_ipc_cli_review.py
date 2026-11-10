"""Terminal review inside an already-trusted local CLI runtime."""

import hashlib
import os
import socket
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, cast

from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals
from heinzel.heads.mcp._ipc_host_review import exact_host_review_channel, host_review_channel
from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from pydantic import JsonValue

_REVIEWER = Path(__file__).absolute().with_name("_ipc_terminal_reviewer.py")
# Part of the trusted installed SDK. A source change must deliberately update this pin.
_REVIEWER_SHA256 = "bbbec0a81676085c9591439adaa5b3cef86e6540089fe2fd80ce4b59468dd50c"
_MAX_SOURCE = 64 * 1024
_REFUSED = "The pinned local MCP reviewer is unavailable"
log = get_logger("mcp")


def _verified_source(path: Path, digest: str) -> bytes:
    owners = {0, os.getuid()}
    if not path.is_absolute() or ".." in path.parts:
        raise HeinzelError(_REFUSED)
    for parent in path.parents:
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in owners or (info.st_mode & 0o022 and not sticky_root):
            raise HeinzelError(_REFUSED)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in owners or info.st_mode & 0o022:
            raise HeinzelError(_REFUSED)
        content = source.read(_MAX_SOURCE + 1)
    if len(content) > _MAX_SOURCE or hashlib.sha256(content).hexdigest() != digest:
        raise HeinzelError(_REFUSED)
    return content


def _load_installed_reviewer() -> "Callable[[socket.socket, float | None], bool]":
    with step(log, "mcp.ipc.load_reviewer", operation="ipc", step="load_reviewer"):
        if not sys.flags.isolated:
            raise HeinzelError(_REFUSED)
        try:
            source = _verified_source(_REVIEWER, _REVIEWER_SHA256)
            namespace: dict[str, object] = {"__name__": "_owned_mcp_terminal_reviewer"}
            code = compile(source, "<owned MCP terminal reviewer>", "exec")
            exec(code, namespace)  # noqa: S102 - these exact immutable bytes match the trusted installed digest
            return cast("Callable[[socket.socket, float | None], bool]", namespace["review"])
        except (OSError, KeyError, SyntaxError, ValueError):
            raise HeinzelError(_REFUSED) from None


def run_cli_host_reviewer(channel: socket.socket) -> bool:
    """Use the exact checked source without passing the private descriptor through an executable path.

    The supervisor must already own the isolated CLI process and supply its private control channel.
    Python isolation and a foreground terminal do not independently confer host authority.

    Raises:
        HeinzelError: If the current runtime is unisolated or the pinned reviewer cannot be loaded.
    """
    with channel:
        return _load_installed_reviewer()(channel, None)


def review_private_mcp_from_terminal(authority: HostMcpConnectionApprovals, instance_id: str) -> None:
    """Run one broker review in the trusted foreground CLI process."""
    _review_exact(host_review_channel(authority, instance_id))


def review_mcp_lifecycle_from_terminal(exact: "dict[str, JsonValue]", *, deadline: float | None = None) -> None:
    """Approve one action on the host terminal before the bound lifecycle effect."""
    _review_exact(exact_host_review_channel(exact, lambda: None, deadline=deadline), deadline=deadline)


def require_local_mcp_terminal() -> None:
    """Require the isolated installed command's own foreground controlling terminal.

    Raises:
        HeinzelError: If the command has no verified local terminal for explicit review.
    """
    refused = "MCP lifecycle requires the trusted installed host and a local controlling terminal."
    if not sys.flags.isolated or os.getuid() != os.geteuid():
        raise HeinzelError(refused)
    try:
        descriptor = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            local = os.isatty(descriptor) and os.tcgetpgrp(descriptor) == os.getpgrp()
        finally:
            os.close(descriptor)
    except OSError:
        raise HeinzelError(refused) from None
    if not local:
        raise HeinzelError(refused)


def _review_exact(
    channel: "AbstractContextManager[tuple[socket.socket, Callable[[], None]]]", *, deadline: float | None = None
) -> None:
    review = _load_installed_reviewer()
    with channel as (reviewer, serve), ThreadPoolExecutor(max_workers=1) as executor:
        broker_result = executor.submit(serve)
        with reviewer:
            review(reviewer, deadline)
        try:
            broker_result.result(timeout=None if deadline is None else max(0, deadline - time.monotonic()))
        except TimeoutError:
            raise HeinzelError(_REFUSED) from None
