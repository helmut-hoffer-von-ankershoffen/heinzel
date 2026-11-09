"""Bounded, descriptor-based inspection for the trusted MCP client configuration host."""

import hashlib
import hmac
import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

from heinzel.heads.mcp._client_config import (
    _MAX_CONFIG_BYTES,
    ClientBridgeBinding,
    ClientConfigContext,
    McpClient,
    _load_json,
)
from heinzel.kernel import HeinzelError

_HASH = re.compile(r"[0-9a-f]{64}")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_BAD_FILE = "The local MCP config or pinned bridge file is unavailable or unsafe."
_MAX_INTERPRETER_BYTES = 128 * 1024 * 1024
_MAX_BRIDGE_BYTES = 256 * 1024
_WORKSPACE_FILES = {
    McpClient.CLAUDE: Path(".mcp.json"),
    McpClient.CODEX: Path(".codex/config.toml"),
    McpClient.CURSOR: Path(".cursor/mcp.json"),
}
_SHADOWED = "Use a workspace without project MCP configuration to review this user-profile change."


@dataclass(frozen=True, slots=True)
class ClientFileSnapshot:
    """The bytes and inode state observed through an owned, no-follow descriptor."""

    data: bytes = field(repr=False)
    identity: tuple[int, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class PinnedClientBridge:
    """The installation inventory's exact bridge binding and SHA-256 pins.

    Pin values must come from trusted installation inventory, never from project or client config.
    These checks attest files at observation time. Connection admission still verifies the bridge.
    """

    binding: ClientBridgeBinding = field(repr=False)
    interpreter_sha256: str = field(repr=False)
    bridge_sha256: str = field(repr=False)

    def verify(self) -> None:
        """Check current executable and bridge source bytes against the installation's pins.

        Raises:
            HeinzelError: If a file is unsafe, unavailable or differs from the trusted pin.
        """
        files = (
            (self.binding.interpreter, self.interpreter_sha256, _MAX_INTERPRETER_BYTES, True),
            (self.binding.bridge, self.bridge_sha256, _MAX_BRIDGE_BYTES, False),
        )
        for path, expected, maximum, executable in files:
            if not _HASH.fullmatch(expected):
                raise HeinzelError(_BAD_FILE)
            actual = _inspect_file(path, maximum, owned=False, executable=executable, digest=True)
            if actual is None or not hmac.compare_digest(actual.data.hex(), expected):
                raise HeinzelError(_BAD_FILE)


def read_client_config(context: ClientConfigContext) -> ClientFileSnapshot | None:
    """Read the standard profile without following links or creating directories."""
    return _inspect_file(context.path, _MAX_CONFIG_BYTES, owned=True, executable=False, digest=False)


def require_plain_workspace(context: ClientConfigContext, name: str, current: ClientFileSnapshot | None) -> None:
    """Require physical paths and refuse project declarations for the standard user profile.

    Raises:
        HeinzelError: If the local account or workspace cannot be proved or a project layer exists.
    """
    _require_directory(context.home, owner=os.getuid())
    _require_directory(context.workspace, owner=None)
    marker = _WORKSPACE_FILES[context.client]
    for directory in (context.workspace, *context.workspace.parents):
        candidate = directory / marker
        if candidate != context.path and _entry_exists(candidate):
            raise HeinzelError(_SHADOWED)
    if context.client is McpClient.CLAUDE and current is not None:
        _require_no_claude_local_entry(current.data, name)


def _entry_exists(path: Path) -> bool:
    try:
        with ExitStack() as stack:
            parent = _open_directory(path.parent, stack)
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        raise HeinzelError(_BAD_FILE) from None
    return True


def _require_no_claude_local_entry(data: bytes, name: str) -> None:
    try:
        document = _load_json(data.decode("utf-8"))
    except (ArithmeticError, ValueError, RecursionError):
        raise HeinzelError(_BAD_FILE) from None
    if not isinstance(document, dict):
        raise HeinzelError(_BAD_FILE)
    projects = document.get("projects", {})
    if not isinstance(projects, dict):
        raise HeinzelError(_BAD_FILE)
    for project in projects.values():
        if not isinstance(project, dict):
            raise HeinzelError(_BAD_FILE)
        servers = project.get("mcpServers", {})
        if not isinstance(servers, dict) or name in servers:
            raise HeinzelError(_SHADOWED)


def _require_directory(path: Path, *, owner: int | None) -> None:
    try:
        with ExitStack() as stack:
            descriptor = _open_directory(path, stack)
            if owner is not None and os.fstat(descriptor).st_uid != owner:
                raise HeinzelError(_BAD_FILE)
    except (OSError, ValueError):
        raise HeinzelError(_BAD_FILE) from None


def _open_directory(path: Path, stack: ExitStack) -> int:
    if path.anchor != "/" or ".." in path.parts:
        raise HeinzelError(_BAD_FILE)
    directory = os.open("/", _DIRECTORY_FLAGS)
    stack.callback(os.close, directory)
    for part in path.parts[1:]:
        directory = os.open(part, _DIRECTORY_FLAGS, dir_fd=directory)
        stack.callback(os.close, directory)
        _safe_directory(directory)
    return directory


def _safe_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
    if info.st_uid not in {0, os.getuid()} or (info.st_mode & 0o022 and not sticky_root):
        raise HeinzelError(_BAD_FILE)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size, info.st_ctime_ns, info.st_mtime_ns


def _read(descriptor: int, maximum: int, *, owned: bool, executable: bool, digest: bool) -> ClientFileSnapshot:
    info = os.fstat(descriptor)
    owners = {os.getuid()} if owned else {0, os.getuid()}
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid not in owners
        or info.st_mode & 0o022
        or info.st_size > maximum
        or (owned and info.st_nlink != 1)
        or (executable and not info.st_mode & stat.S_IXUSR)
    ):
        raise HeinzelError(_BAD_FILE)
    identity = _identity(info)
    checksum = hashlib.sha256()
    data = bytearray()
    remaining = info.st_size
    while remaining:
        chunk = os.read(descriptor, min(65536, remaining))
        if not chunk:
            raise HeinzelError(_BAD_FILE)
        if digest:
            checksum.update(chunk)
        else:
            data.extend(chunk)
        remaining -= len(chunk)
    if _identity(os.fstat(descriptor)) != identity:
        raise HeinzelError(_BAD_FILE)
    return ClientFileSnapshot(checksum.digest() if digest else bytes(data), identity)


def _inspect_file(
    path: Path, maximum: int, *, owned: bool, executable: bool, digest: bool
) -> ClientFileSnapshot | None:
    if not path.name:
        raise HeinzelError(_BAD_FILE)
    try:
        with ExitStack() as stack:
            parent = _open_directory(path.parent, stack)
            descriptor = os.open(path.name, _FILE_FLAGS, dir_fd=parent)
            stack.callback(os.close, descriptor)
            return _read(descriptor, maximum, owned=owned, executable=executable, digest=digest)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise HeinzelError(_BAD_FILE) from None
