"""What connected clients present: device tokens, MCP bearer tokens, the private state directory and the widget fence.

The public entry of the `client` context (`contexts.toml`). The heads import these names from here and never a private
file; the modules here import no web toolkit, so the CLI stays fast.
"""

from heinzel.modules.system.client._devices import Devices
from heinzel.modules.system.client._mcp_tokens import (
    READ,
    SCOPES,
    WRITE,
    McpToken,
    McpTokens,
    require_reachable_only_as_asked,
    tokens_for_user,
)
from heinzel.modules.system.client._state_directory import open_updater_file, private_directory
from heinzel.modules.system.client._widget_fence import FenceConflictError
from heinzel.modules.system.client._widget_fence import Request as FenceRequest
from heinzel.modules.system.client._workspace_version import WorkspaceVersion
from heinzel.modules.system.client._workspace_version import observe as observe_workspace_version

__all__ = [
    "READ",
    "SCOPES",
    "WRITE",
    "Devices",
    "FenceConflictError",
    "FenceRequest",
    "McpToken",
    "McpTokens",
    "WorkspaceVersion",
    "observe_workspace_version",
    "open_updater_file",
    "private_directory",
    "require_reachable_only_as_asked",
    "tokens_for_user",
]
