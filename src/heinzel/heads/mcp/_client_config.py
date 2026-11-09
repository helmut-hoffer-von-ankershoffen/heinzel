"""Reviewed manual MCP configuration for the standard user profile of each vendor.

The trusted host supplies the profile, immutable registration and bounded file snapshots from an
owner-verified path without following symlinks. It also obtains
the effective entry from the native client in the exact workspace under review, including any overrides
or policy blocks. These adapters neither execute a client nor write config. File parsing alone cannot prove
the effective entry or MCP readiness.
"""

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

import tomlkit
from pydantic import JsonValue

from heinzel.kernel import HeinzelError
from heinzel.kernel._approval import arguments_hash
from heinzel.kernel._logging import get_logger, step

log = get_logger("mcp")
_MAX_CONFIG_BYTES = 4 * 1024 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_REGISTRATION = re.compile(r"[0-9a-f]{32}")


class McpClient(StrEnum):
    """Native clients supported by the user-profile adapter."""

    CLAUDE = "claude"
    CODEX = "codex"
    CURSOR = "cursor"


_USER_CONFIG = {
    # https://code.claude.com/docs/en/mcp#user-scope
    McpClient.CLAUDE: Path(".claude.json"),
    # https://developers.openai.com/codex/mcp
    McpClient.CODEX: Path(".codex/config.toml"),
    # https://cursor.com/docs/mcp#configuration-locations
    McpClient.CURSOR: Path(".cursor/mcp.json"),
}


class ClientConfigAction(StrEnum):
    """The exact manual change presented for host review."""

    INSTALL = "install"
    UNINSTALL = "uninstall"


class NativeConfigStatus(StrEnum):
    """Whether the native client selects the named entry for the inspected workspace."""

    ABSENT = "absent"
    SELECTED = "selected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ClientConfigContext:
    """The trusted host's standard vendor profile and native workspace under inspection."""

    client: McpClient
    home: Path = field(repr=False)
    workspace: Path = field(repr=False)

    @property
    def path(self) -> Path:
        """The intended user config. Project configuration is a separate source."""
        return self.home / _USER_CONFIG[self.client]

    @property
    def table(self) -> str:
        """The client-specific container for stdio servers."""
        return "mcp_servers" if self.client is McpClient.CODEX else "mcpServers"


@dataclass(frozen=True, slots=True)
class ClientBridgeBinding:
    """The trusted inventory's pinned interpreter and per-registration bridge.

    For uninstall the host must recover the original binding from its own installation inventory.
    The name and paths are locators. Neither the binding nor a matching file conveys authority.
    """

    registration: str = field(repr=False)
    interpreter: Path = field(repr=False)
    bridge: Path = field(repr=False)

    @property
    def name(self) -> str:
        """A stable server name reserved for this registration."""
        return f"heinzel-{self.registration}"

    def entry(self, client: McpClient) -> dict[str, JsonValue]:
        """Render only the pinned, isolated stdio command, with no environment or credentials."""
        entry: dict[str, JsonValue] = {"command": str(self.interpreter), "args": ["-I", "-S", str(self.bridge)]}
        if client is McpClient.CLAUDE:
            entry["type"] = "stdio"
        return entry


@dataclass(frozen=True, slots=True)
class NativeClientConfig:
    """A trusted, fresh native observation for one named server and workspace.

    SELECTED requires the native client to identify the winning source and its entire normalized entry.
    Normalize inert vendor defaults only. Disabled, blocked, ambiguous or uninspectable is UNAVAILABLE.
    The host must resolve the actual profile and overrides before constructing this observation. Connection
    health is a separate check. A list of names or a file read alone cannot establish this observation.
    """

    context: ClientConfigContext
    name: str = field(repr=False)
    status: NativeConfigStatus
    source: Path | None = field(default=None, repr=False)
    entry: dict[str, object] | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ManualClientConfigPlan:
    """A manual-only preview and the exact baseline the local review must bind."""

    action: ClientConfigAction
    context: ClientConfigContext
    binding: ClientBridgeBinding = field(repr=False)
    before_digest: str = field(repr=False)

    @property
    def digest(self) -> str:
        """Bind the review to action, profile, workspace, registration, entry and original file bytes."""
        return arguments_hash(
            {
                "version": 1,
                "action": self.action.value,
                "client": self.context.client.value,
                "workspace": str(self.context.workspace),
                "path": str(self.context.path),
                "name": self.binding.name,
                "entry": self.binding.entry(self.context.client),
                "before_digest": self.before_digest,
            }
        )

    def install_snippet(self) -> str:
        """Render only the reviewed server for insertion into the named container.

        Add this member to an existing JSON or inline TOML container. The snippet represents
        the entry alone and should never replace a complete existing config.

        Raises:
            HeinzelError: If this plan is for removal.
        """
        if self.action is not ClientConfigAction.INSTALL:
            msg = "The manual MCP change is a removal."
            raise HeinzelError(msg)
        entry = self.binding.entry(self.context.client)
        if self.context.client is McpClient.CODEX:
            document = tomlkit.document()
            document.add(self.context.table, {self.binding.name: entry})
            return tomlkit.dumps(document)
        return json.dumps({self.context.table: {self.binding.name: entry}}, indent=2)

    def require_current(self, current: bytes | None, native: NativeClientConfig) -> None:
        """Recheck the reviewed baseline before handing a manual edit to the owner.

        Raises:
            HeinzelError: If the target file or selected native entry has changed.
        """
        if _snapshot_digest(current) != self.before_digest:
            msg = "The MCP config changed. Prepare a new manual plan."
            raise HeinzelError(msg)
        _require_native(self.context, self.binding, native, present=self.action is ClientConfigAction.UNINSTALL)

    def verify(self, before: bytes | None, after: bytes | None, native: NativeClientConfig) -> None:
        """Verify the one manual edit and effective native state, retaining unrelated config.

        Raises:
            HeinzelError: If the baseline, file edit or effective native selection differs from the plan.
        """
        with step(log, "mcp.client_config.verify", client=self.context.client.value, action=self.action.value):
            if _snapshot_digest(before) != self.before_digest:
                msg = "The original MCP config does not match the reviewed plan."
                raise HeinzelError(msg)
            old_entry, old_rest = _split(self.context, self.binding.name, before)
            new_entry, new_rest = _split(self.context, self.binding.name, after)
            expected = self.binding.entry(self.context.client)
            installed = self.action is ClientConfigAction.INSTALL
            if (
                after is None
                or not _same(old_entry, _ABSENT if installed else expected)
                or not _same(new_entry, expected if installed else _ABSENT)
                or not _same_settings(old_rest, new_rest)
            ):
                msg = "The manual edit differs from the reviewed MCP config change."
                raise HeinzelError(msg)
            _require_native(self.context, self.binding, native, present=installed)


def plan_manual_client_config(
    action: ClientConfigAction,
    context: ClientConfigContext,
    binding: ClientBridgeBinding,
    before: bytes | None,
    native: NativeClientConfig,
) -> ManualClientConfigPlan:
    """Prepare one exact install or uninstall preview without reading or mutating the filesystem.

    Raises:
        HeinzelError: If the target is invalid, already occupied or lacks the exact managed entry for removal.
    """
    with step(log, "mcp.client_config.plan", client=context.client.value, action=action.value):
        _validate_binding(context, binding)
        entry, _ = _split(context, binding.name, before)
        remove = action is ClientConfigAction.UNINSTALL
        if not _same(entry, binding.entry(context.client) if remove else _ABSENT):
            msg = "The MCP entry already exists or does not match the recorded bridge."
            raise HeinzelError(msg)
        _require_native(context, binding, native, present=remove)
        return ManualClientConfigPlan(action, context, binding, _snapshot_digest(before))


_ABSENT = object()


def _validate_binding(context: ClientConfigContext, binding: ClientBridgeBinding) -> None:
    for path in (context.home, context.workspace, binding.interpreter, binding.bridge):
        value = str(path)
        if not path.is_absolute() or ".." in path.parts or _CONTROL.search(value) or "${" in value:
            msg = "The MCP configuration or bridge path is invalid."
            raise HeinzelError(msg)
    if not _REGISTRATION.fullmatch(binding.registration):
        msg = "The MCP registration locator is invalid."
        raise HeinzelError(msg)


def _require_native(
    context: ClientConfigContext, binding: ClientBridgeBinding, native: NativeClientConfig, *, present: bool
) -> None:
    if native.context == context and native.name == binding.name:
        if present:
            if (
                native.status is NativeConfigStatus.SELECTED
                and native.source == context.path
                and _same(native.entry, binding.entry(context.client))
            ):
                return
        elif native.status is NativeConfigStatus.ABSENT and native.source is None and native.entry is None:
            return
    msg = "The native MCP config is unavailable or selects a different entry."
    raise HeinzelError(msg)


def _snapshot_digest(raw: bytes | None) -> str:
    if raw is None:
        return hashlib.sha256(b"\x00").hexdigest()
    if len(raw) > _MAX_CONFIG_BYTES:
        msg = "The MCP config is too large to inspect."
        raise HeinzelError(msg)
    digest = hashlib.sha256(b"\x01")
    digest.update(raw)
    return digest.hexdigest()


def _split(context: ClientConfigContext, name: str, raw: bytes | None) -> tuple[object, dict[str, object]]:
    if raw is None:
        return _ABSENT, {}
    if len(raw) > _MAX_CONFIG_BYTES:
        msg = "The MCP config is too large to inspect."
        raise HeinzelError(msg)
    invalid = "The MCP config is invalid or has an ambiguous server table."
    try:
        data = raw.decode("utf-8")
        parsed = tomllib.loads(data) if context.client is McpClient.CODEX else _load_json(data)
    except (ArithmeticError, ValueError, RecursionError):
        raise HeinzelError(invalid) from None
    if not isinstance(parsed, dict):
        raise HeinzelError(invalid)
    document = cast("dict[str, object]", parsed)
    servers = document.get(context.table, _ABSENT)
    if servers is _ABSENT:
        return _ABSENT, document
    if not isinstance(servers, dict):
        raise HeinzelError(invalid)
    entry = servers.pop(name, _ABSENT)
    if not servers:
        del document[context.table]
    return entry, document


def _load_json(data: str) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        mapping: dict[str, object] = {}
        for key, value in pairs:
            if key in mapping:
                raise ValueError
            mapping[key] = value
        return mapping

    def invalid_constant(_value: str) -> object:
        raise ValueError

    return cast(
        "object", json.loads(data, object_pairs_hook=unique, parse_float=Decimal, parse_constant=invalid_constant)
    )


def _same_settings(left: dict[str, object], right: dict[str, object]) -> bool:
    try:
        return _same(left, right)
    except RecursionError:
        return False


def _same(left: object, right: object) -> bool:
    """Compare parsed config while keeping distinct scalar types and NaN values distinct from other types."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        left_table, right_table = cast("dict[str, object]", left), cast("dict[str, object]", right)
        return left_table.keys() == right_table.keys() and all(
            _same(value, right_table[key]) for key, value in left_table.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        left_items, right_items = cast("list[object]", left), cast("list[object]", right)
        return len(left_items) == len(right_items) and all(
            _same(first, second) for first, second in zip(left_items, right_items, strict=True)
        )
    if isinstance(left, float):
        return repr(left) == repr(right)
    return left == right
