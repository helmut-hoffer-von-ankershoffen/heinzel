"""`hz system serve`: start the other heads from the CLI."""

import os
import webbrowser
from collections.abc import Callable
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from pydantic import BaseModel
from rich.console import Console
from typer.core import TyperGroup

from heinzel.heads.cli._render import message
from heinzel.kernel import CurrentUser, HeinzelError, Kernel, PersistentWeb, parse_duration

if TYPE_CHECKING:
    from heinzel.heads._mcp_tokens import McpTokens
    from heinzel.heads.web._devices import Devices


class Transport(StrEnum):
    """How the MCP head talks to its client."""

    STDIO = "stdio"
    HTTP = "streamable-http"


class ChatToolSet(StrEnum):
    """Which operations a chat's agent gets as tools."""

    EVERYDAY = "everyday"
    ALL = "all"
    PERSONAL_CHECK_IN = "personal-check-in"


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
            Transport,
            typer.Option(help="stdio for agents that start hz; streamable-http to share, with a token per agent."),
        ] = Transport.STDIO,
        host: Annotated[
            str, typer.Option(help="Interface for streamable-http; loopback unless --expose.")
        ] = "127.0.0.1",
        port: Annotated[int, typer.Option(help="Port for streamable-http.")] = 8765,
        expose: Annotated[
            bool, typer.Option("--expose", help="Allow a --host other devices can reach; Tailscale Serve needs none.")
        ] = False,
        allowed_host: Annotated[
            list[str] | None,
            typer.Option(help="Also accept this Host name, e.g. the machine's Tailscale Serve name; repeatable."),
        ] = None,
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
            typer.Exit: With 2 when --chat is given with another transport than stdio, 1 when serving is refused.
        """
        from heinzel.heads import mcp as head  # noqa: PLC0415 - heavy import, only when serving

        if chat is not None and transport is not Transport.STDIO:
            err_console.print(message("Can't serve:", "--chat serves one chat's agent over stdio only, never a port."))
            raise typer.Exit(2)
        if project is not None:
            os.chdir(project)  # before the head boots its kernel, so it finds the same project file
        serving = partial(head.serve, transport.value, host, port, chat=chat, chat_tools=chat_tools.value)
        _refused_or_served(lambda: serving(expose=expose, allowed_hosts=tuple(allowed_host or ())), err_console)

    serve.add_typer(_mcp_token_group(group_cls, footer, err_console))

    @serve.command(epilog=footer)
    def api(
        host: Annotated[str, typer.Option(help="Interface to listen on.")] = "127.0.0.1",
        port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
        private: Annotated[
            bool, typer.Option("--private", help="Require a server-scoped bearer for private operations.")
        ] = False,
        credential_file: Annotated[
            Path | None,
            typer.Option(help="New owner-only file for the private API credential; never put the token in arguments."),
        ] = None,
    ) -> None:
        """Serve every operation as a REST endpoint; OpenAPI docs at /docs."""
        _serve_api(host, port, private, credential_file, err_console)

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

        with kernel.resolve(CurrentUser).local("daemon"):
            head.serve(kernel, parse_duration(interval), report)

    return serve


UI_HELP = "Serve the web workspace explicitly, or manage its optional service after login."
UI_STOPPED = "Heinzel stopped. Run `hz ui serve` to start it again."
"""Ctrl-C is a normal web-server stop (exit 0), unlike an interrupted setup (130)."""


def ui_command(kernel: Kernel, err_console: Console, *, open_existing: bool = False) -> Callable[..., None]:
    """`hz ui serve`: the web head on its fixed localhost port, with a token for this session and paired devices."""

    def ui(  # noqa: PLR0913, PLR0917 - one CLI option per supported foreground and service control
        ctx: typer.Context,
        port: Annotated[
            int | None, typer.Option(help="Port on 127.0.0.1; [hz] ui_port by default, 0 picks a free one.")
        ] = None,
        browser: Annotated[
            bool,
            typer.Option(
                "--browser/--no-browser", help="Open the default browser at the app; --no-browser prints only its URL."
            ),
        ] = True,
        supervised: Annotated[
            str | None, typer.Option(hidden=True, help="Owned service generation; never a credential.")
        ] = None,
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

        if ctx.invoked_subcommand is not None:
            return

        if devices or revoke is not None:
            _devices(web.devices_for_user(), revoke, err_console)
            return

        announce = _ui_announcement(err_console, browser=browser)
        if supervised is not None:
            from heinzel.heads.web._supervision import redirect_supervised_stderr  # noqa: PLC0415 - service only

            redirect_supervised_stderr(kernel.settings.log_dir)

        try:
            if (
                open_existing
                and supervised is None
                and port is None
                and web.RESUME not in os.environ
                and _open_persistent(kernel, browser=browser)
            ):
                return
            web.serve(
                kernel,
                port=kernel.settings.ui_port if port is None else port,
                browser=browser,
                announce=announce,
                supervised=supervised,
                waiting=lambda text: err_console.print(text, highlight=False, markup=False),
            )
        except KeyboardInterrupt:  # Ctrl-C before the server took it over: stopped all the same
            pass
        except HeinzelError as exc:
            err_console.print(message("Can't start the web app:", str(exc)))
            raise typer.Exit(1) from exc
        except OSError as exc:
            err_console.print(
                message(
                    "Can't listen:", f"{exc}; is `hz ui serve` running already? Or try another --port, 0 for a free one"
                )
            )
            raise typer.Exit(1) from exc
        if err_console.is_terminal:
            err_console.print()  # past the ^C the terminal echoed
        err_console.print(UI_STOPPED, highlight=False, markup=False)

    return ui


def _ui_announcement(err_console: Console, *, browser: bool) -> Callable[[str], None]:
    def announce(url: str) -> None:
        # The URL belongs only to this explicit terminal request, never to service-manager logs.
        if not browser:
            typer.echo(url)
            return
        err_console.print(f"Heinzel runs at {url}", soft_wrap=True, highlight=False, markup=False)
        err_console.print("Press Ctrl-C to stop it.", highlight=False, markup=False)

    return announce


def _open_persistent(kernel: Kernel, *, browser: bool) -> bool:
    if not kernel.provides(PersistentWeb):
        return False
    url = kernel.resolve(PersistentWeb).open_local()
    if url is None:
        return False
    if browser:
        webbrowser.open(url)
    else:
        typer.echo(url)
    return True


def _devices(paired: "Devices", revoke: str | None, err_console: Console) -> None:
    """List the paired devices, or unpair one (or `all`).

    Raises:
        typer.Exit: With 1 when no device has that id.
    """
    if revoke is not None:
        gone = paired.revoke(revoke)
        if not gone:
            err_console.print(message("No such device:", f"{revoke}; `hz ui serve --devices` lists them"))
            raise typer.Exit(1)
        for device in gone:
            typer.echo(f"Unpaired {device.id} ({device.name}); it needs a new link from `hz ui open` to open the app.")
        return
    listed = paired.paired()
    if not listed:
        typer.echo("No devices are paired; opening the link `hz ui open` prints pairs the browser it opens in.")
        return
    for device in listed:
        typer.echo(
            f"{device.id}  {device.name}  paired {device.created:%Y-%m-%d}, last used {device.last_used:%Y-%m-%d}"
        )


class TokenScope(StrEnum):
    """What an MCP token may do."""

    READ = "read"
    WRITE = "write"


def _refused_or_served(serving: Callable[[], None], err_console: Console) -> None:
    """Serve until stopped, or name why serving was refused (no token, a shared interface without --expose).

    Raises:
        typer.Exit: With 1 when serving is refused.
    """
    try:
        serving()
    except HeinzelError as exc:
        err_console.print(message("Can't serve:", str(exc)))
        raise typer.Exit(1) from None


def _mcp_token_group(group_cls: type[TyperGroup], footer: str | None, err_console: Console) -> typer.Typer:
    """`hz system serve mcp-token`: the agents' tokens for MCP over HTTP; never an MCP tool or REST endpoint."""
    group = typer.Typer(
        name="mcp-token",
        help="Make, list and revoke the tokens agents send to `hz system serve mcp --transport streamable-http`.",
        epilog=footer,
        cls=group_cls,
        no_args_is_help=True,
    )

    @group.command(epilog=footer)
    def create(
        actor: Annotated[str, typer.Option(help="Which agent it is for, e.g. codex-desktop; logged with each call.")],
        scope: Annotated[
            TokenScope, typer.Option(help="read: read-only tools only; write: every tool the head serves.")
        ] = TokenScope.READ,
        expires: Annotated[
            str, typer.Option(help="How long it works, e.g. 30d.", parser=_duration, metavar="DURATION")
        ] = "90d",
    ) -> None:
        """Make a token for one agent and print it once; only its SHA-256 is kept.

        Raises:
            typer.Exit: With 1 when the actor name is invalid or too many tokens exist.
        """
        try:
            record, token = _tokens().create(actor, scope.value, parse_duration(expires))
        except HeinzelError as exc:
            err_console.print(message("Can't make the token:", str(exc)))
            raise typer.Exit(1) from None
        err_console.print(
            message(
                f"Token {record.id} for {record.actor} ({record.scope}), until {record.expires:%Y-%m-%d}:",
                "shown this once; send it as `Authorization: Bearer <token>`.",
            )
        )
        typer.echo(token)

    @group.command(name="list", epilog=footer)
    def list_tokens() -> None:
        """List the tokens: id, actor, scope, expiry and last use; never the tokens themselves."""
        records = _tokens().tokens()
        if not records:
            typer.echo("No MCP tokens; `hz system serve mcp-token create --actor NAME` makes one.")
            return
        for record in records:
            used = "never" if record.last_used is None else f"{record.last_used:%Y-%m-%d}"
            typer.echo(
                f"{record.id}  {record.actor}  {record.scope}  expires {record.expires:%Y-%m-%d}, last used {used}"
            )

    @group.command(epilog=footer)
    def revoke(which: Annotated[str, typer.Argument(help="A token id from `list`, or `all`.")]) -> None:
        """Revoke a token (or `all`); the agent's next request is refused.

        Raises:
            typer.Exit: With 1 when no token has that id.
        """
        gone = _tokens().revoke(which)
        if not gone:
            err_console.print(message("No such token:", f"{which}; `hz system serve mcp-token list` lists them"))
            raise typer.Exit(1)
        for record in gone:
            typer.echo(f"Revoked {record.id} ({record.actor}).")

    return group


def _tokens() -> "McpTokens":
    from heinzel.heads._mcp_tokens import tokens_for_user  # noqa: PLC0415 - only when managing tokens

    return tokens_for_user()


def _duration(value: str) -> str:
    try:
        parse_duration(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return value


def _serve_api(host: str, port: int, private: bool, credential_file: Path | None, err_console: Console) -> None:
    from heinzel.heads import api as head  # noqa: PLC0415 - heavy import, only when serving

    if not private and credential_file is not None:
        err_console.print(message("Can't serve:", "--credential-file requires --private."))
        raise typer.Exit(2)
    if private:
        head.serve(host, port, private=True, credential_file=credential_file, announce=err_console.print)
    else:
        head.serve(host, port)
