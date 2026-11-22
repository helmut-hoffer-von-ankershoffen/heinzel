"""`hz system serve` and `hz ui`: the one file that composes the other heads (the heads contract allows it)."""

import json
import os
import sys
import webbrowser
from collections.abc import Callable
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypedDict

import typer
from pydantic import BaseModel
from rich.console import Console
from typer.core import TyperGroup

from heinzel.kernel import (
    KERNEL_SECTION,
    ConfigEditor,
    CurrentUser,
    HeinzelError,
    Kernel,
    PersistentWeb,
    Settings,
    parse_duration,
)

if TYPE_CHECKING:
    from rich.console import RenderableType

    from heinzel.modules.system.client import Devices, McpToken, McpTokens


def message(label: str, text: str) -> "RenderableType":
    """The CLI's error line; loaded on use, as the CLI package imports this file while it initialises."""
    from heinzel.heads.cli._render import message as line  # noqa: PLC0415 - the CLI package imports this file

    return line(label, text)


class Transport(StrEnum):
    """How the MCP head talks to its client."""

    STDIO = "stdio"
    HTTP = "streamable-http"


class ChatToolSet(StrEnum):
    """Which operations a chat's agent gets as tools."""

    EVERYDAY = "everyday"
    ALL = "all"
    PERSONAL_CHECK_IN = "personal-check-in"


class ProjectMcpClient(StrEnum):
    """Clients supporting ordinary project MCP configuration."""

    CLAUDE = "claude"
    CODEX = "codex"
    CURSOR = "cursor"


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
            bool,
            typer.Option(
                "--expose",
                help="Allow a --host other devices reach, with --allowed-host; Tailscale Serve needs neither.",
            ),
        ] = False,
        allowed_host: Annotated[
            list[str] | None,
            typer.Option(
                help="Also accept this Host: a Tailscale Serve name (port 443 implied), or name-or-ip:PORT or "
                "name-or-ip:* with --expose; repeatable."
            ),
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

        \f

        Raises:
            typer.Exit: With 2 when --chat is given with another transport than stdio, 1 when serving is refused.
        """  # noqa: D301 - Typer ends the help at a real form feed
        from heinzel.heads import mcp as head  # noqa: PLC0415 - heavy import, only when serving

        if chat is not None and transport is not Transport.STDIO:
            err_console.print(message("Can't serve:", "--chat serves one chat's agent over stdio only, never a port."))
            raise typer.Exit(2)
        if project is not None:
            os.chdir(project)  # before the head boots its kernel, so it finds the same project file
        serving = partial(head.serve, transport.value, host, port, chat=chat, chat_tools=chat_tools.value)
        _refused_or_served(lambda: serving(expose=expose, allowed_hosts=tuple(allowed_host or ())), err_console)

    _add_mcp_switch(serve, footer, kernel, err_console)
    _add_mcp_check(serve, footer, err_console)
    _add_mcp_project_install(serve, footer, err_console)

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

        \f

        Raises:
            typer.Exit: With 1 when the app isn't built, the port is taken or there is no such device.
        """  # noqa: D301 - Typer ends the help at a real form feed
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
            _retire_strays_in_background(kernel)

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


_BRIDGE_TOKEN_LIMIT = 256


def attach_command(kernel: Kernel, err_console: Console) -> Callable[..., None]:
    """`hz ui attach`: what the desktop apps run at launch instead of starting a server of their own."""

    def attach(
        json_output: JsonOption = False,
        bridge_token_stdin: Annotated[
            bool,
            typer.Option(
                "--bridge-token-stdin",
                help="Read the desktop app's browser-bridge token from standard input and register it.",
            ),
        ] = False,
    ) -> None:
        """Print a session link to this computer's one web service, installing or starting it first if needed.

        It never serves itself: no second server, no fallback. It also stops web servers older desktop apps left
        behind. On failure it prints one reason and exits non-zero.

        \f

        Raises:
            typer.Exit: With 1 when the service can't be installed, started or reached.
        """  # noqa: D301 - Typer ends the help at a real form feed
        bridge: str | None = None
        if bridge_token_stdin:
            bridge = sys.stdin.readline(_BRIDGE_TOKEN_LIMIT + 2).rstrip("\r\n") or None
        if not kernel.provides(PersistentWeb):
            err_console.print(message("Can't attach:", "the ui module is not loaded."))
            raise typer.Exit(1)
        web = kernel.resolve(PersistentWeb)
        if web.attach is None:
            err_console.print(message("Can't attach:", "this installation has no owned web service."))
            raise typer.Exit(1)
        _report_strays(*web.retire_strays(), lambda text: err_console.print(text, highlight=False, markup=False))
        try:
            url = web.attach(kernel.settings.ui_port, bridge)
        except (HeinzelError, OSError) as exc:
            err_console.print(message("Can't attach:", str(exc)))
            raise typer.Exit(1) from None
        if json_output:
            typer.echo(json.dumps({"url": url}))
        else:
            typer.echo(url)

    return attach


def _retire_strays_in_background(kernel: Kernel) -> None:
    """At supervised start: stop the servers older desktop apps left (one backend per machine); never delays serving."""
    if not kernel.provides(PersistentWeb):
        return
    import logging  # noqa: PLC0415 - only the supervised service
    import threading  # noqa: PLC0415 - only the supervised service

    def retire() -> None:
        try:
            stopped, survivors = kernel.resolve(PersistentWeb).retire_strays()
        except Exception:  # noqa: BLE001 - a failed cleanup must never stop the service
            logging.getLogger(__name__).warning("Leftover web servers could not be inspected.")
            return
        _report_strays(stopped, survivors, logging.getLogger(__name__).warning)

    threading.Thread(target=retire, name="hz-retire-strays", daemon=True).start()


def _report_strays(stopped: list[int], survivors: list[int], say: Callable[[str], object]) -> None:
    if stopped:
        say(f"Stopped {len(stopped)} leftover web server{'s' if len(stopped) != 1 else ''} an older app started.")
    if survivors:
        pids = ", ".join(str(pid) for pid in survivors)
        say(f"A leftover web server did not stop (pid {pids}); `hz system doctor` shows it until it is gone.")


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


_MAX_MCP_TOKEN_LENGTH = 4096


def _add_mcp_project_install(serve: typer.Typer, footer: str | None, err_console: Console) -> None:
    """Register explicit creation of an ordinary client project configuration."""

    @serve.command(name="mcp-install-project", epilog=footer)
    def mcp_install_project(
        client: Annotated[ProjectMcpClient, typer.Option(help="Client whose project configuration to create.")],
        project: Annotated[
            Path, typer.Option(help="Existing project directory other than home; no global configuration is changed.")
        ],
        expected_device: Annotated[int | None, typer.Option(hidden=True)] = None,
        expected_inode: Annotated[int | None, typer.Option(hidden=True)] = None,
    ) -> None:
        """Create a new project MCP config; existing configs are never replaced.

        The client may require project trust or approval. Connection readiness is not verified.

        \f

        Raises:
            typer.Exit: If the project config already exists or its directory is unavailable.
        """  # noqa: D301 - Typer ends the help at a real form feed
        from heinzel.heads.mcp._standard_project import StandardClient, install_project  # noqa: PLC0415

        if (expected_device is None) != (expected_inode is None):
            err_console.print(message("Can't install:", "The selected project identity is incomplete."))
            raise typer.Exit(2)
        identity = (
            (expected_device, expected_inode) if expected_device is not None and expected_inode is not None else None
        )
        try:
            path = install_project(StandardClient(client.value), project, expected_identity=identity)
        except HeinzelError as exc:
            err_console.print(message("Can't install:", str(exc)))
            raise typer.Exit(1) from None
        typer.echo(json.dumps({"configuration": "created", "path": str(path), "mcp_readiness": "unverified"}))


def _add_mcp_check(serve: typer.Typer, footer: str | None, err_console: Console) -> None:
    """Register a bounded authenticated observation of the ordinary loopback MCP head."""

    @serve.command(name="mcp-check", epilog=footer)
    def mcp_check(
        url: Annotated[str, typer.Option(help="Loopback HTTP MCP endpoint, including /mcp.")],
        token_stdin: Annotated[
            bool,
            typer.Option(
                "--token-stdin",
                help="Read the bearer from standard input; the network timeout starts after input arrives.",
            ),
        ] = False,
    ) -> None:
        """Authenticate and initialize a loopback MCP endpoint; this does not prove worker ownership.

        Input waits for a line or EOF; the network check then has a ten-second limit.

        \f

        Raises:
            typer.Exit: If the token, endpoint or MCP initialization is unavailable.
        """  # noqa: D301 - Typer ends the help at a real form feed
        if not token_stdin or sys.stdin.isatty():
            err_console.print(message("Can't check:", "Provide the token on standard input with --token-stdin."))
            raise typer.Exit(2)
        token = sys.stdin.readline(_MAX_MCP_TOKEN_LENGTH + 1).rstrip("\r\n")
        if len(token) > _MAX_MCP_TOKEN_LENGTH:
            err_console.print(message("Can't check:", "The token is invalid."))
            raise typer.Exit(1)
        from heinzel.heads.mcp._http_check import check_http  # noqa: PLC0415 - imported only for this command

        try:
            count = check_http(url, token)
        except HeinzelError:
            err_console.print(message("Can't check:", "The MCP endpoint could not be authenticated and initialized."))
            raise typer.Exit(1) from None
        typer.echo(json.dumps({"mcp_readiness": "initialized", "tools": count, "worker_ownership": "unverified"}))


class McpState(TypedDict):
    """What `mcp-start`, `mcp-stop` and `mcp-status` report."""

    enabled: bool
    url: str
    shared_urls: list[str]
    serving: bool


def _mcp_state(kernel: Kernel) -> McpState:
    """The `[hz] mcp_http` switch as the files hold it now, where it is served, and whether the web app answers."""
    settings = kernel.config.reread().settings(Settings, KERNEL_SECTION)
    url = f"http://127.0.0.1:{settings.ui_port}/mcp"
    shared = sorted(f"{origin}/mcp" for origin in settings.ui_allowed_origins)
    return {
        "enabled": settings.mcp_http,
        "url": url,
        "shared_urls": shared,
        "serving": _mcp_serving(settings.ui_port) if settings.mcp_http else False,
    }


def _mcp_serving(port: int) -> bool:
    """Whether the web app answers `/mcp` (401 without a token) on its port; False when it isn't running or it's off."""
    import http.client  # noqa: PLC0415 - only for this check

    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/mcp")
        return connection.getresponse().status in {http.client.UNAUTHORIZED, http.client.BAD_REQUEST}
    except OSError:
        return False
    finally:
        connection.close()


def _print_mcp_state(state: McpState, *, json_output: bool) -> None:
    """The switch, as JSON or as lines saying what to do next."""
    if json_output:
        typer.echo(json.dumps(state))
        return
    if not state["enabled"]:
        typer.echo("MCP for other agents is off. `hz system serve mcp-start` turns it on (or Settings, MCP).")
        return
    if state["serving"]:
        typer.echo(f"MCP for other agents is on at {state['url']}")
    else:
        typer.echo(f"MCP for other agents is on, and `hz ui serve` serves it at {state['url']} once it runs.")
    for url in state["shared_urls"]:
        typer.echo(f"Shared: {url}")
    typer.echo("Every agent needs a token: `hz system serve mcp-token create --actor NAME`.")


def _add_mcp_switch(serve: typer.Typer, footer: str | None, kernel: Kernel, err_console: Console) -> None:
    """Register `mcp-start`, `mcp-stop` and `mcp-status`: the one setting that serves MCP at `/mcp` on the web app."""

    def change(*, on: bool, json_output: bool) -> None:
        try:
            kernel.resolve(ConfigEditor).write("hz.mcp_http", on)
        except HeinzelError as exc:
            err_console.print(message("Can't change it:", str(exc)))
            raise typer.Exit(1) from None
        state = _mcp_state(kernel)
        if state["enabled"] is not on:
            err_console.print(message("Not in effect:", "the environment (HZ_MCP_HTTP) overrides the configuration."))
            raise typer.Exit(1)
        _print_mcp_state(state, json_output=json_output)

    @serve.command(name="mcp-start", epilog=footer)
    def mcp_start(json_output: JsonOption = False) -> None:
        """Serve MCP to other agents at /mcp on the web app's address; the running web app follows at once.

        It sets `[hz] mcp_http`, the switch Settings, MCP shows too. Nothing else has to run beyond `hz ui serve`.
        """
        change(on=True, json_output=json_output)

    @serve.command(name="mcp-stop", epilog=footer)
    def mcp_stop(json_output: JsonOption = False) -> None:
        """Stop serving MCP at /mcp; open agent sessions end, the tokens stay."""
        change(on=False, json_output=json_output)

    @serve.command(name="mcp-status", epilog=footer)
    def mcp_status(json_output: JsonOption = False) -> None:
        """Say whether MCP is on, where it is served and whether the web app answers it."""
        _print_mcp_state(_mcp_state(kernel), json_output=json_output)


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
        json_output: JsonOption = False,
    ) -> None:
        """Make a token for one agent and print it once; only its SHA-256 is kept.

        \f

        Raises:
            typer.Exit: With 1 when the actor name is invalid or too many tokens exist.
        """  # noqa: D301 - Typer ends the help at a real form feed
        try:
            record, token = _tokens().create(actor, scope.value, parse_duration(expires))
        except HeinzelError as exc:
            err_console.print(message("Can't make the token:", str(exc)))
            raise typer.Exit(1) from None
        if json_output:
            typer.echo(json.dumps({**_public(record), "token": token}))
            return
        err_console.print(
            message(
                f"Token {record.id} for {record.actor} ({record.scope}), until {record.expires:%Y-%m-%d}:",
                "shown this once; send it as `Authorization: Bearer <token>`.",
            )
        )
        typer.echo(token)

    @group.command(name="list", epilog=footer)
    def list_tokens(json_output: JsonOption = False) -> None:
        """List the tokens: id, actor, scope, expiry and last use; never the tokens themselves."""
        _print_tokens(_tokens().tokens(), json_output=json_output)

    @group.command(epilog=footer)
    def revoke(
        which: Annotated[str, typer.Argument(help="A token id from `list`, or `all`.")],
        json_output: JsonOption = False,
    ) -> None:
        """Revoke a token (or `all`); the agent's next request is refused.

        \f

        Raises:
            typer.Exit: With 1 when no token has that id.
        """  # noqa: D301 - Typer ends the help at a real form feed
        gone = _tokens().revoke(which)
        if not gone:
            err_console.print(message("No such token:", f"{which}; `hz system serve mcp-token list` lists them"))
            raise typer.Exit(1)
        _print_revoked(gone, json_output=json_output)

    return group


JsonOption = Annotated[bool, typer.Option("--json", help="Print JSON.")]


def _public(record: "McpToken") -> dict[str, object]:
    """A token's record as JSON without its digest."""
    return record.model_dump(mode="json", exclude={"sha256"})


def _print_tokens(records: "list[McpToken]", *, json_output: bool) -> None:
    """`mcp-token list`: one line per token, or a JSON list; never a token or its digest."""
    if json_output:
        typer.echo(json.dumps([_public(record) for record in records]))
        return
    if not records:
        typer.echo("No MCP tokens; `hz system serve mcp-token create --actor NAME` makes one.")
        return
    for record in records:
        used = "never" if record.last_used is None else f"{record.last_used:%Y-%m-%d}"
        typer.echo(f"{record.id}  {record.actor}  {record.scope}  expires {record.expires:%Y-%m-%d}, last used {used}")


def _print_revoked(gone: "list[McpToken]", *, json_output: bool) -> None:
    """`mcp-token revoke`: what was revoked, as lines or JSON."""
    if json_output:
        typer.echo(json.dumps({"revoked": [_public(record) for record in gone]}))
        return
    for record in gone:
        typer.echo(f"Revoked {record.id} ({record.actor}).")


def _tokens() -> "McpTokens":
    from heinzel.modules.system.client import tokens_for_user  # noqa: PLC0415 - only when managing tokens

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
