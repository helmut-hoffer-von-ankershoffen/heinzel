"""`hz system serve`: start the other heads from the CLI."""

from enum import StrEnum
from typing import Annotated

import typer
from typer.core import TyperGroup


class Transport(StrEnum):
    """How the MCP head talks to its client."""

    STDIO = "stdio"
    HTTP = "streamable-http"


def serve_group(group_cls: type[TyperGroup], footer: str | None) -> typer.Typer:
    """The `serve` command group; the heads are imported only when started, so `hz` itself stays fast."""
    serve = typer.Typer(
        name="serve",
        help="Serve every operation to coding agents (MCP) or over HTTP (REST).",
        epilog=footer,
        cls=group_cls,
        no_args_is_help=True,
    )

    @serve.command(epilog=footer)
    def mcp(
        transport: Annotated[
            Transport, typer.Option(help="stdio for agents that start hz; streamable-http to share.")
        ] = Transport.STDIO,
        host: Annotated[str, typer.Option(help="Interface for streamable-http.")] = "127.0.0.1",
        port: Annotated[int, typer.Option(help="Port for streamable-http.")] = 8765,
    ) -> None:
        """Serve every operation as an MCP tool, e.g. `claude mcp add hz -- hz system serve mcp`."""
        from heinzel.heads import mcp as head  # noqa: PLC0415 - heavy import, only when serving

        head.serve(transport.value, host, port)

    @serve.command(epilog=footer)
    def api(
        host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
        port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
    ) -> None:
        """Serve every operation as a REST endpoint; OpenAPI docs at /docs."""
        from heinzel.heads import api as head  # noqa: PLC0415 - heavy import, only when serving

        head.serve(host, port)

    return serve
