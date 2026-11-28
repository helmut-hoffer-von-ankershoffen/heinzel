"""Register UI commands and preserve the serving exports used by older launchers."""

from typing import Annotated

import typer
from rich.console import Console

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
from heinzel.kernel import Kernel

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


def register_ui_commands(ui: typer.Typer, kernel: Kernel, error: Console) -> None:
    """Bind current commands and keep the published legacy flag vectors working."""

    @ui.callback(invoke_without_command=True, no_args_is_help=False, help=UI_HELP)
    def group(
        ctx: typer.Context,
        port: Annotated[int | None, typer.Option(hidden=True)] = None,
        browser: Annotated[bool, typer.Option("--browser/--no-browser", hidden=True)] = True,
        supervised: Annotated[str | None, typer.Option(hidden=True)] = None,
    ) -> None:
        # Published service launchers retain this exact legacy vector until explicitly reinstalled.
        if supervised is not None:
            if ctx.invoked_subcommand is not None or port is None or browser:
                problem = "Use `hz ui serve` for foreground serving."
                raise typer.BadParameter(problem)
            error.print("The legacy foreground flags are deprecated; use `hz ui serve`.")
            ui_command(kernel, error)(ctx, port=port, browser=False, supervised=supervised)
        elif port is not None or getattr(ctx.get_parameter_source("browser"), "name", None) == "COMMANDLINE":
            # Published updaters trial and exec the older foreground vector; socket handoff still validates in web.
            if ctx.invoked_subcommand is not None:
                problem = "Use `hz ui serve` for foreground serving."
                raise typer.BadParameter(problem)
            error.print("The legacy foreground flags are deprecated; use `hz ui serve`.")
            ui_command(kernel, error)(ctx, port=port, browser=browser)
        elif ctx.invoked_subcommand is None:
            typer.echo(ctx.get_help())

    ui.command("serve", help="Run the web server in the foreground; Ctrl-C stops it.")(ui_command(kernel, error))
    ui.command("open", help="Open the owned web service, or run in the foreground when none is installed.")(
        ui_command(kernel, error, open_existing=True)
    )
    ui.command("attach", help="Start the owned web service if needed and print its session link; never serves.")(
        attach_command(kernel, error)
    )
