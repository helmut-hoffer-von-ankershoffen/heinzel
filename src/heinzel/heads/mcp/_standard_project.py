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


def install_project(client: StandardClient, project: Path) -> Path:
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
            if folder:
                # The subsequent no-follow open validates an existing entry.
                with suppress(FileExistsError):
                    os.mkdir(folder, 0o700, dir_fd=directory)
            child = os.open(folder, flags, dir_fd=directory) if folder else os.dup(directory)
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=child)
            with os.fdopen(descriptor, "wb") as output:
                output.write(contents)
        except OSError:
            msg = "The project config already exists or its directory is unavailable."
            raise HeinzelError(msg) from None
        finally:
            if child is not None:
                os.close(child)
            if directory is not None:
                os.close(directory)
        return project / folder / name if folder else project / name
