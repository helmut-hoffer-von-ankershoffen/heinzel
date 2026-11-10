"""Normalize bounded MCP inspection results from trusted local vendor integrations.

The host supplies output from the exact client/profile/workspace and proves the source separately when
the client does not report it. These functions don't launch clients. Claude's get may run a configured
server, so never use it where launch isolation or trusted selection cannot be established. Codex get
does not report its winning file, so the host must pair it with the authenticated config/read result
for the same profile and workspace. Cursor list alone has neither the exact argv nor the winning file.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from heinzel.heads.mcp._client_config import (
    ClientBridgeBinding,
    ClientConfigContext,
    McpClient,
    NativeClientConfig,
    NativeConfigStatus,
    load_config_json,
)
from heinzel.heads.mcp._client_config_codex import codex_config_provenance

MAX_NATIVE_CONFIG_OUTPUT = 128 * 1024
_CODEX_NULL = {
    "disabled_reason",
    "enabled_tools",
    "disabled_tools",
    "startup_timeout_sec",
    "tool_timeout_sec",
}
_CLAUDE_SCOPE = "User config (available in all your projects)"
_CLAUDE_STATUS = {"✔ Connected", "✘ Failed to connect"}


@dataclass(frozen=True, slots=True)
class NativeInspection:
    """One bounded host-captured probe and the independently proved winning source, if selected.

    Do not construct this from user or model inputs. The native host owns the actual invocation, its
    isolation, the account/profile, workspace, and applicable configuration-layer inspection.
    """

    returncode: int
    stdout: str = field(repr=False)
    stderr: str = field(repr=False)
    context: ClientConfigContext = field(kw_only=True, repr=False)
    source: Path | None = field(default=None, repr=False)
    codex_config_read: str | None = field(default=None, kw_only=True, repr=False)


def observe_client_config(
    context: ClientConfigContext, binding: ClientBridgeBinding, inspection: NativeInspection
) -> NativeClientConfig:
    """Cross-check the native entry and scope, keeping unsupported or ambiguous evidence unavailable."""
    unavailable = NativeClientConfig(context, binding.name, NativeConfigStatus.UNAVAILABLE)
    if (
        context != inspection.context
        or context.client is McpClient.CURSOR
        or len(inspection.stdout) + len(inspection.stderr) > MAX_NATIVE_CONFIG_OUTPUT
    ):
        return unavailable
    if context.client is McpClient.CODEX:
        provenance = codex_config_provenance(context, binding, inspection.codex_config_read)
        if provenance is None or provenance.selected != (inspection.returncode == 0):
            return unavailable
    if inspection.returncode:
        if inspection.source is None and _native_absent(context.client, binding.name, inspection):
            return NativeClientConfig(context, binding.name, NativeConfigStatus.ABSENT)
        return unavailable
    if context.client is McpClient.CLAUDE:
        match = inspection.source == context.path and _claude_matches(binding, inspection.stdout)
    else:
        match = inspection.source in {None, context.path} and _codex_matches(binding, inspection.stdout)
    if not match:
        return unavailable
    return NativeClientConfig(
        context, binding.name, NativeConfigStatus.SELECTED, context.path, dict(binding.entry(context.client))
    )


def _native_absent(client: McpClient, name: str, inspection: NativeInspection) -> bool:
    if inspection.returncode != 1:
        return False
    if client is McpClient.CLAUDE:
        expected = f'No MCP server named "{name}". Run `claude mcp add` to add one.'
        return [line for line in (inspection.stdout + inspection.stderr).splitlines() if line] == [expected]
    diagnostics = [
        line
        for line in inspection.stderr.splitlines()
        if line and not line.startswith("WARNING: proceeding, even though we could not create PATH aliases:")
    ]
    return not inspection.stdout.strip() and diagnostics == [f"Error: No MCP server named '{name}' found."]


def _claude_matches(binding: ClientBridgeBinding, output: str) -> bool:
    lines = output.strip().splitlines()
    if not lines or lines[0] != f"{binding.name}:":
        return False
    fields: dict[str, str] = {}
    remove = f"To remove this server, run: claude mcp remove {binding.name} -s user"
    for line in lines[1:]:
        if not line or line == remove:
            continue
        key, separator, value = line.removeprefix("  ").partition(": ")
        if not separator or key in fields:
            return False
        fields[key] = value
    return (
        not fields.keys() - {"Scope", "Status", "Issue", "Type", "Command", "Args"}
        and fields.get("Scope") == _CLAUDE_SCOPE
        and fields.get("Status") in _CLAUDE_STATUS
        and fields.get("Type") == "stdio"
        and fields.get("Command") == str(binding.interpreter)
        # The UI flattens argv. The host checks the complete structured file in the same inspection window.
        and fields.get("Args") == f"-I -S {binding.bridge}"
    )


def _codex_matches(binding: ClientBridgeBinding, output: str) -> bool:
    try:
        payload = load_config_json(output)
    except (ArithmeticError, ValueError, RecursionError):
        return False
    if not isinstance(payload, dict):
        return False
    body = cast("dict[str, object]", payload)
    if (
        body.get("name") != binding.name
        or body.get("enabled") is not True
        or body.keys() - ({"name", "enabled", "transport"} | _CODEX_NULL)
        or any(body.get(key) is not None for key in _CODEX_NULL)
    ):
        return False
    return body.get("transport") == {
        "type": "stdio",
        "command": str(binding.interpreter),
        "args": ["-I", "-S", str(binding.bridge)],
        "env": None,
        "env_vars": [],
        "cwd": None,
    }
