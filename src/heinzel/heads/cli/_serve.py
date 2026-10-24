"""`hz system serve`: start the other heads from the CLI."""

from collections.abc import Callable
from enum import StrEnum
from typing import Annotated

import typer
from pydantic import BaseModel
from rich.console import Console
from typer.core import TyperGroup

from heinzel.heads.cli._render import message
from heinzel.kernel import HeinzelError, Kernel, parse_duration


class Transport(StrEnum):
    """How the MCP head talks to its client."""

    STDIO = "stdio"
    HTTP = "streamable-http"


def serve_group(
    group_cls: type[TyperGroup], footer: str | None, kernel: Kernel, out_console: Console, err_console: Console
) -> typer.Typer:
    """The `serve` command group; the heads are imported only when started, so `hz` itself stays fast."""
    serve = typer.Typer(
        name="serve",
        help="Serve every operation to coding agents (MCP) or over HTTP (REST), or run the daemon in the foreground.",
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

    @serve.command(epilog=footer)
    def daemon(
        interval: Annotated[
            str, typer.Option(help="Time between ticks, e.g. 30s, 5m or 1h.", parser=_duration, metavar="DURATION")
        ] = "5m",
    ) -> None:
        """Run the daemon's tick in the foreground every interval, printing each result as a JSON line."""
        from heinzel.heads import daemon as head  # noqa: PLC0415 - only when serving

        def report(result: object) -> None:
            if isinstance(result, HeinzelError):
                err_console.print(message("Tick failed:", str(result)))
            elif isinstance(result, Exception):
                err_console.print(message("Tick failed with a bug:", f"{type(result).__name__}: {result}"))
            elif isinstance(result, BaseModel):
                out_console.print(result.model_dump_json(), soft_wrap=True, highlight=False, markup=False)

        head.serve(kernel, parse_duration(interval), report)

    return serve


UI_HELP = "Open the web app in your browser, served on localhost for this session only; Ctrl-C stops it."


def ui_command(kernel: Kernel, err_console: Console) -> Callable[..., None]:
    """`hz ui`: the web head on a free localhost port, with a token for this session only."""

    def ui(
        port: Annotated[int, typer.Option(help="Port on 127.0.0.1; 0 picks a free one.")] = 0,
        browser: Annotated[
            bool,
            typer.Option(
                "--browser/--no-browser", help="Open the default browser at the app; --no-browser prints only its URL."
            ),
        ] = True,
    ) -> None:
        """Open the web app in your browser, served on localhost for this session only; Ctrl-C stops it.

        Raises:
            typer.Exit: With 1 when the app isn't built or the port is taken.
        """
        from heinzel.heads import web  # noqa: PLC0415 - heavy import, only when serving

        def announce(url: str) -> None:
            # The URL carries the session's token: it is this terminal's to share, like the session itself.
            if not browser:
                typer.echo(url)
                return
            err_console.print(f"Heinzelmännchen runs at {url}", soft_wrap=True, highlight=False, markup=False)
            err_console.print("Press Ctrl-C to stop it.", highlight=False, markup=False)

        try:
            web.serve(kernel, port=port, browser=browser, announce=announce)
        except HeinzelError as exc:
            err_console.print(message("Can't start the web app:", str(exc)))
            raise typer.Exit(1) from exc
        except OSError as exc:
            err_console.print(message("Can't listen:", f"{exc}; try another --port, or 0 for a free one"))
            raise typer.Exit(1) from exc

    return ui


def _duration(value: str) -> str:
    try:
        parse_duration(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return value
