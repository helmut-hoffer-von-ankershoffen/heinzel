"""How to start one found MCP server, read from its source file at the moment hz starts it (ADR 0146, 0162).

Nothing here is stored: the scan is repeated for the one id (`Scanner.want`), its raw entry is turned into a
`Launch`, and the `Launch` lives only as long as the connection it starts. Variables are expanded as Claude Code
expands them in `.mcp.json` (`${VAR}`, `${VAR:-default}`, `${CLAUDE_PLUGIN_ROOT}`; `${__dirname}` for a Claude
Desktop extension). A value kept in Claude Desktop's keychain (`${user_config.<key>}`) or held by another app's
sign-in is not something hz can use yet, and says so.
"""

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from heinzel import HeinzelError
from heinzel.kernel import subprocess_environment
from heinzel.modules.system.claude._models import FoundServer
from heinzel.modules.system.claude._scan import Captured

_VARIABLE = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")
NOT_USABLE = "not usable from hz yet"


class NotUsableError(HeinzelError):
    """The server can't be started or reached by hz; the message names why, never a value."""


@dataclass(frozen=True, slots=True)
class Launch:
    """Everything needed to start or reach one server; it holds secrets, so it is never logged or stored."""

    transport: Literal["stdio", "http"]
    command: list[str] = field(default_factory=list[str])
    env: dict[str, str] = field(default_factory=dict[str, str], repr=False)
    cwd: Path = Path()
    url: str = field(default="", repr=False)
    headers: dict[str, str] = field(default_factory=dict[str, str], repr=False)

    @property
    def digest(self) -> str:
        """A hash of everything, kept in memory to notice a changed entry; it reveals no value."""
        parts = [self.transport, self.command, sorted(self.env.items()), str(self.cwd), self.url, self.headers]
        return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def launch(home: Path, server: FoundServer, captured: Captured, environ: Mapping[str, str] | None = None) -> Launch:
    """The `Launch` of `server` from its raw entry.

    Raises:
        NotUsableError: If it is not a stdio or HTTP server, needs a value hz can't read, or its entry is malformed.
    """
    if server.status == "self":
        msg = "That is hz's own MCP server; it is never offered back to itself"
        raise NotUsableError(msg)
    if server.auth == "keychain":
        raise NotUsableError(_keychain(server))
    if not captured.approved:
        msg = f"{server.name} is {NOT_USABLE}: it is not approved in Claude Code for its project"
        raise NotUsableError(msg)
    expand = _Expander(server, outside_environment(os.environ if environ is None else environ), captured.path.parent)
    if server.transport == "stdio":
        return _stdio(home, server, captured, expand)
    if server.transport == "http":
        url = captured.entry.get("url")
        if not isinstance(url, str) or urlsplit(expand(url)).scheme not in {"http", "https"}:
            msg = f"{server.name} is {NOT_USABLE}: its entry has no http or https URL"
            raise NotUsableError(msg)
        headers = expand.mapping(captured.entry.get("headers"))
        return Launch(transport="http", url=expand(url), headers=headers, cwd=home)
    msg = f"{server.name} is {NOT_USABLE}: hz speaks stdio and streamable HTTP, not {server.transport}"
    raise NotUsableError(msg)


def outside_environment(environ: Mapping[str, str]) -> dict[str, str]:
    """The environment a found server sees and its entry may expand: never hz's own variables or credentials.

    `HZ_CREDENTIAL_*` holds hz's secrets and every other `HZ_*` variable is hz's configuration; a project's
    `.mcp.json` asking for `${HZ_CREDENTIAL_GITHUB_TOKEN}` gets "not set", as if hz did not run.
    """
    return {key: value for key, value in subprocess_environment(environ).items() if not key.startswith("HZ_")}


def _keychain(server: FoundServer) -> str:
    return f"{server.name} is {NOT_USABLE}: it needs a setting Claude Desktop keeps in its keychain"


@dataclass(frozen=True, slots=True)
class _Expander:
    """Claude Code's variable expansion, for one server's entry."""

    server: FoundServer
    environment: dict[str, str]
    directory: Path

    def __call__(self, text: str) -> str:
        """`text` with every `${VAR}` and `${VAR:-default}` replaced.

        Raises:
            NotUsableError: If a variable is not set and has no default, or names a keychain setting.
        """
        return _VARIABLE.sub(self._one, text)

    def _one(self, match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name.startswith("user_config."):
            raise NotUsableError(_keychain(self.server))
        # Only what Claude Code itself sets for this kind of entry: a plugin's root, an extension's directory.
        extra = (
            {"CLAUDE_PLUGIN_ROOT": str(self.directory)}
            if self.server.source == "code-plugin"
            else {"__dirname": str(self.directory)}
            if self.server.source == "desktop-extension"
            else {}
        )
        value = extra.get(name, self.environment.get(name))
        if value is None and default is None:
            msg = f"{self.server.name} is {NOT_USABLE}: the environment variable {name} is not set"
            raise NotUsableError(msg)
        return value if value is not None else default or ""

    def mapping(self, value: object) -> dict[str, str]:
        """A mapping of strings from the entry, each value expanded; anything else is left out."""
        items = cast("dict[str, Any]", value) if isinstance(value, dict) else {}
        return {str(k): self(v) for k, v in items.items() if isinstance(v, str)}


def _stdio(home: Path, server: FoundServer, captured: Captured, expand: _Expander) -> Launch:
    command = captured.entry.get("command")
    if not isinstance(command, str) or not command:
        msg = f"{server.name} is {NOT_USABLE}: its entry names no command"
        raise NotUsableError(msg)
    raw = captured.entry.get("args")
    arguments = [expand(a) for a in cast("list[object]", raw) if isinstance(a, str)] if isinstance(raw, list) else []
    return Launch(
        transport="stdio",
        command=[expand(command), *arguments],
        env={**expand.environment, **expand.mapping(captured.entry.get("env"))},
        cwd=_directory(home, server, captured),
    )


def _directory(home: Path, server: FoundServer, captured: Captured) -> Path:
    """Where a stdio server runs: its project, plugin or extension directory, else your home directory."""
    if server.source in {"code-project", "code-plugin", "desktop-extension"}:
        folder = captured.path.parent
    elif server.source == "code-local" and captured.origin:
        origin = captured.origin
        folder = home / origin[2:] if origin.startswith("~/") else Path(origin)
    else:
        folder = home
    return folder if folder.is_dir() else home
