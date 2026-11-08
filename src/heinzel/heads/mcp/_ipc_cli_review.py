"""Trusted CLI-side delivery of the broker's one-use host-review descriptor."""

import hashlib
import os
import socket
import stat
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from heinzel.heads.mcp._ipc_approval import HostMcpConnectionApprovals
from heinzel.heads.mcp._ipc_host_review import host_review_channel
from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

_REVIEWER = Path(__file__).absolute().with_name("_ipc_terminal_reviewer.py")
_REFUSED = "The pinned local MCP reviewer is unavailable"
log = get_logger("mcp")


@dataclass(frozen=True, slots=True)
class CliReviewRuntime:
    """Physical interpreter and digests selected by the trusted installation authority."""

    interpreter: Path
    interpreter_sha256: str
    reviewer_sha256: str


def _verify_file(path: Path, digest: str) -> None:
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
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise HeinzelError(_REFUSED)


@contextmanager
def cli_host_reviewer(channel: socket.socket, runtime: CliReviewRuntime) -> Generator[subprocess.Popen[bytes]]:
    """Pass the exact private descriptor to the pinned reviewer under the caller's controlling terminal.

    The supervisor selects the runtime under its installation ownership fence. Only the descriptor number
    crosses argv. The child has a clean environment, isolated imports and no inherited standard streams.

    Raises:
        HeinzelError: If the pinned runtime cannot be verified or launched.

    Yields:
        The owned reviewer process. The parent relinquishes its copy of the reviewer descriptor before yield.
    """
    with step(log, "mcp.ipc.launch_reviewer", operation="ipc", step="launch_reviewer"):
        try:
            _verify_file(runtime.interpreter, runtime.interpreter_sha256)
            _verify_file(_REVIEWER, runtime.reviewer_sha256)
            descriptor = channel.fileno()
            command = [os.fspath(runtime.interpreter), "-I", "-S", "-B", os.fspath(_REVIEWER), str(descriptor)]
            process = subprocess.Popen(  # noqa: S603 - only the pinned interpreter/script and one inherited descriptor
                command,
                cwd=_REVIEWER.parent,
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                pass_fds=(descriptor,),
            )
        except (OSError, ValueError):
            raise HeinzelError(_REFUSED) from None
    try:
        channel.close()
        yield process
    finally:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def review_private_mcp_from_terminal(
    authority: HostMcpConnectionApprovals, instance_id: str, runtime: CliReviewRuntime
) -> None:
    """Run one exact broker review from a trusted, explicitly invoked local CLI path."""
    with host_review_channel(authority, instance_id) as (reviewer, serve), cli_host_reviewer(reviewer, runtime):
        serve()
