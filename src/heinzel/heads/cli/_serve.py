"""`hz system serve`: start the other heads from the CLI."""

import os
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import BaseModel
from rich.console import Console
from typer.core import TyperGroup

from heinzel.heads.cli._render import message
from heinzel.kernel import HeinzelError, Kernel, parse_duration

if TYPE_CHECKING:
    from heinzel.heads.web._devices import Devices


class Transport(StrEnum):
    """How the MCP head talks to its client."""

    STDIO = "stdio"
    HTTP = "streamable-http"


class ChatToolSet(StrEnum):
    """Which operations a chat's agent gets as tools."""

    EVERYDAY = "everyday"
    ALL = "all"


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
    def mcp(  # noqa: PLR0913, PLR0917 - one parameter per command-line option
        transport: Annotated[
            Transport, typer.Option(help="stdio for agents that start hz; streamable-http to share.")
        ] = Transport.STDIO,
        host: Annotated[str, typer.Option(help="Interface for streamable-http.")] = "127.0.0.1",
        port: Annotated[int, typer.Option(help="Port for streamable-http.")] = 8765,
        chat: Annotated[
            str | None,
            typer.Option(
                help="Serve the tools of this chat session: reads run, everything else waits for your confirmation "
                "in the chat. Started by `hz chat` for its agent, over stdio only.",
                metavar="SESSION",
            ),
        ] = None,
        project: Annotated[
            Path | None,
            typer.Option(
                help="Read the configuration as if started in this directory (its hz.toml), as the chat does."
            ),
        ] = None,
        chat_tools: Annotated[
            ChatToolSet,
            typer.Option(help="With --chat: the everyday operations as tools (cheaper messages), or all of them."),
        ] = ChatToolSet.EVERYDAY,
    ) -> None:
        """Serve every operation as an MCP tool, e.g. `claude mcp add hz -- hz system serve mcp`.

        Raises:
            typer.Exit: With 2 when --chat is given with another transport than stdio.
        """
        from heinzel.heads import mcp as head  # noqa: PLC0415 - heavy import, only when serving

        if chat is not None and transport is not Transport.STDIO:
            err_console.print(message("Can't serve:", "--chat serves one chat's agent over stdio only, never a port."))
            raise typer.Exit(2)
        if project is not None:
            os.chdir(project)  # before the head boots its kernel, so it finds the same project file
        head.serve(transport.value, host, port, chat=chat, chat_tools=chat_tools.value)

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


UI_HELP = "Open the web app in your browser, served on localhost; Ctrl-C stops it."
UI_STOPPED = "Heinzelmännchen stopped. Run `hz ui` to start it again."
"""What `hz ui` says when Ctrl-C stops it: its normal end, so it exits 0, unlike an interrupted `hz setup` (130)."""


def ui_command(kernel: Kernel, err_console: Console) -> Callable[..., None]:
    """`hz ui`: the web head on its fixed localhost port, with a token for this session and paired devices."""

    def ui(
        port: Annotated[
            int | None, typer.Option(help="Port on 127.0.0.1; [hz] ui_port by default, 0 picks a free one.")
        ] = None,
        browser: Annotated[
            bool,
            typer.Option(
                "--browser/--no-browser", help="Open the default browser at the app; --no-browser prints only its URL."
            ),
        ] = True,
        devices: Annotated[
            bool, typer.Option("--devices", help="List the browsers and installed apps paired with hz, then stop.")
        ] = False,
        revoke: Annotated[
            str | None, typer.Option(help="Unpair a device by its id from --devices, or `all`, then stop.")
        ] = None,
    ) -> None:
        """Open the web app in your browser, served on localhost; Ctrl-C stops it.

        It listens on one port (`[hz] ui_port`), so an installed app keeps its address and, once paired, reopens
        without a new link; `--devices` lists the paired ones and `--revoke` unpairs them.

        Ctrl-C stops it; that is its normal end, so it exits 0.

        Raises:
            typer.Exit: With 1 when the app isn't built, the port is taken or there is no such device.
        """
        from heinzel.heads import web  # noqa: PLC0415 - heavy import, only when serving

        if devices or revoke is not None:
            _devices(web.devices_for_user(), revoke, err_console)
            return

        def announce(url: str) -> None:
            # The URL carries the session's token: it is this terminal's to share, like the session itself.
            if not browser:
                typer.echo(url)
                return
            err_console.print(f"Heinzelmännchen runs at {url}", soft_wrap=True, highlight=False, markup=False)
            err_console.print("Press Ctrl-C to stop it.", highlight=False, markup=False)

        try:
            web.serve(
                kernel,
                port=kernel.settings.ui_port if port is None else port,
                browser=browser,
                announce=announce,
                waiting=lambda text: err_console.print(text, highlight=False, markup=False),
            )
        except KeyboardInterrupt:  # Ctrl-C before the server took it over: stopped all the same
            pass
        except HeinzelError as exc:
            err_console.print(message("Can't start the web app:", str(exc)))
            raise typer.Exit(1) from exc
        except OSError as exc:
            err_console.print(
                message("Can't listen:", f"{exc}; is `hz ui` running already? Or try another --port, 0 for a free one")
            )
            raise typer.Exit(1) from exc
        if err_console.is_terminal:
            err_console.print()  # past the ^C the terminal echoed
        err_console.print(UI_STOPPED, highlight=False, markup=False)

    return ui


def _devices(paired: "Devices", revoke: str | None, err_console: Console) -> None:
    """List the paired devices, or unpair one (or `all`).

    Raises:
        typer.Exit: With 1 when no device has that id.
    """
    if revoke is not None:
        gone = paired.revoke(revoke)
        if not gone:
            err_console.print(message("No such device:", f"{revoke}; `hz ui --devices` lists them"))
            raise typer.Exit(1)
        for device in gone:
            typer.echo(f"Unpaired {device.id} ({device.name}); it needs a new link from `hz ui` to open the app.")
        return
    listed = paired.paired()
    if not listed:
        typer.echo("No devices are paired; opening the link `hz ui` prints pairs the browser it opens in.")
        return
    for device in listed:
        typer.echo(
            f"{device.id}  {device.name}  paired {device.created:%Y-%m-%d}, last used {device.last_used:%Y-%m-%d}"
        )


def _duration(value: str) -> str:
    try:
        parse_duration(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return value
