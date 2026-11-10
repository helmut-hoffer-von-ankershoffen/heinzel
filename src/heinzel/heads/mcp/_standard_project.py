"""Explicit, creation-only standard MCP configuration for a selected project."""

import json
import os
import sys
from contextlib import suppress
from enum import StrEnum
from pathlib import Path

import tomlkit

from heinzel.kernel import HeinzelError
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp")


class StandardClient(StrEnum):
    """Clients with a documented project configuration."""

    CLAUDE = "claude"
    CODEX = "codex"
    CURSOR = "cursor"


def _identity(stat: os.stat_result) -> tuple[int, int]:
    return stat.st_dev, stat.st_ino


def _reject_global_project(client: StandardClient, project: os.stat_result) -> Path | None:
    # Compare identities so symlinked parents cannot disguise a global target.
    if _identity(project) == _identity(Path.home().stat()):
        msg = "Choose a project directory other than your home."
        raise HeinzelError(msg)
    if client is not StandardClient.CODEX:
        return None
    codex_home = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser().resolve()
    if codex_home.name == ".codex":
        # Refuse before mkdir even when the configured global directory is absent.
        try:
            parent = codex_home.parent.stat()
        except FileNotFoundError:
            pass  # A missing parent cannot be the already-open project.
        else:
            if _identity(project) == _identity(parent):
                msg = "The selected project would create the global Codex configuration."
                raise HeinzelError(msg)
    return codex_home


def _reject_global_codex_dir(child: int, codex_home: Path | None) -> None:
    if codex_home is None:
        return
    try:
        global_dir = codex_home.stat()
    except FileNotFoundError:
        return
    if _identity(os.fstat(child)) == _identity(global_dir):
        msg = "The selected project would create the global Codex configuration."
        raise HeinzelError(msg)


def install_project(client: StandardClient, project: Path, *, expected_identity: tuple[int, int] | None = None) -> Path:
    """Create a new project config; never replace an existing file or follow its final links.

    Raises:
        HeinzelError: If the selected project, configuration directory, or new file is unavailable.
    """
    with step(log, "mcp.standard_project.install", client=client.value):
        folder = {StandardClient.CLAUDE: None, StandardClient.CODEX: ".codex", StandardClient.CURSOR: ".cursor"}[client]
        name = {
            StandardClient.CLAUDE: ".mcp.json",
            StandardClient.CODEX: "config.toml",
            StandardClient.CURSOR: "mcp.json",
        }[client]
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        entry = {"command": sys.executable, "args": ["-m", "heinzel", "system", "serve", "mcp"]}
        if client is StandardClient.CODEX:
            document = tomlkit.document()
            document.add("mcp_servers", {"heinzel": entry})
            contents = tomlkit.dumps(document).encode()
        else:
            contents = (json.dumps({"mcpServers": {"heinzel": entry}}, indent=2) + "\n").encode()
        directory = None
        child = None
        try:
            directory = os.open(project, flags)
            stat = os.fstat(directory)
            codex_home = _reject_global_project(client, stat)
            if expected_identity is not None and _identity(stat) != expected_identity:
                msg = "The selected project changed. Choose it again."
                raise HeinzelError(msg)
            if folder:
                # The subsequent no-follow open validates an existing entry.
                with suppress(FileExistsError):
                    os.mkdir(folder, 0o700, dir_fd=directory)
            child = os.open(folder, flags, dir_fd=directory) if folder else os.dup(directory)
            _reject_global_codex_dir(child, codex_home)
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=child)
            with os.fdopen(descriptor, "wb") as output:
                output.write(contents)
        except (OSError, RuntimeError):
            msg = "The project config already exists or its directory is unavailable."
            raise HeinzelError(msg) from None
        finally:
            if child is not None:
                os.close(child)
            if directory is not None:
                os.close(directory)
        return project / folder / name if folder else project / name
