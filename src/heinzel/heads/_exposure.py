"""The exposure rule, which heads import from here; it lives in the kernel (`heinzel.kernel._exposure`, ADR 0058)."""

from heinzel.kernel._exposure import (
    MCP_HTTP,
    MCP_STDIO,
    Exposed,
    collapses,
    exposed,
    input_schema,
    owns_path,
    public_parameters,
    restricts,
)

__all__ = [
    "MCP_HTTP",
    "MCP_STDIO",
    "Exposed",
    "collapses",
    "exposed",
    "input_schema",
    "owns_path",
    "public_parameters",
    "restricts",
]
