"""Terminal review inside an already-trusted local CLI runtime."""

import hashlib
import os
import socket
import stat
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals
from heinzel.heads.mcp._ipc_host_review import host_review_channel
from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

if TYPE_CHECKING:
    from collections.abc import Callable

_REVIEWER = Path(__file__).absolute().with_name("_ipc_terminal_reviewer.py")
_MAX_SOURCE = 64 * 1024
_REFUSED = "The pinned local MCP reviewer is unavailable"
log = get_logger("mcp")


@dataclass(frozen=True, slots=True)
class CliReviewRuntime:
    """Reviewer digest supplied by the installation authority, never derived from the candidate at review time."""

    reviewer_sha256: str


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


def run_cli_host_reviewer(channel: socket.socket, runtime: CliReviewRuntime) -> bool:
    """Use the exact checked source without passing the private descriptor through an executable path.

    The supervisor must already own the isolated CLI process and supply its private control channel and
    installed digest. Python isolation and a foreground terminal do not independently confer host authority.

    Raises:
        HeinzelError: If the current runtime is unisolated or the pinned reviewer cannot be loaded.
    """
    with channel:
        with step(log, "mcp.ipc.load_reviewer", operation="ipc", step="load_reviewer"):
            if not sys.flags.isolated:
                raise HeinzelError(_REFUSED)
            try:
                source = _verified_source(_REVIEWER, runtime.reviewer_sha256)
                namespace: dict[str, object] = {"__name__": "_owned_mcp_terminal_reviewer"}
                code = compile(source, "<owned MCP terminal reviewer>", "exec")
                exec(code, namespace)  # noqa: S102 - these exact immutable bytes match the trusted installed digest
                review = cast("Callable[[socket.socket], bool]", namespace["review"])
            except (OSError, KeyError, SyntaxError, ValueError):
                raise HeinzelError(_REFUSED) from None
        return review(channel)


def review_private_mcp_from_terminal(
    authority: HostMcpConnectionApprovals, instance_id: str, runtime: CliReviewRuntime
) -> None:
    """Run one broker review in the trusted foreground CLI process."""
    with (
        host_review_channel(authority, instance_id) as (reviewer, serve),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        broker_result = executor.submit(serve)
        run_cli_host_reviewer(reviewer, runtime)
        broker_result.result()
