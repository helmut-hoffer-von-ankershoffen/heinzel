"""The old path of `heinzel.heads._compose`, kept so no importable path disappears in an in-place update."""

from heinzel.heads._compose import (
    UI_HELP,
    UI_STOPPED,
    ChatToolSet,
    JsonOption,
    McpState,
    ProjectMcpClient,
    TokenScope,
    Transport,
    attach_command,
    message,
    serve_group,
    ui_command,
)

__all__ = [
    "UI_HELP",
    "UI_STOPPED",
    "ChatToolSet",
    "JsonOption",
    "McpState",
    "ProjectMcpClient",
    "TokenScope",
    "Transport",
    "attach_command",
    "message",
    "serve_group",
    "ui_command",
]
