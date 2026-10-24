"""The CLI head: every module becomes a command group, every operation a command.

Commands and groups are sorted alphabetically at every level; built-in modules and plugins sit in separate help
panels. A module with exactly one operation of its own name and no submodules collapses into a plain command, so the
`changelog` submodule of `system` reads as `hz system changelog`.
"""

import logging
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler
from typer.core import TyperGroup

from heinzel import __version__
from heinzel.heads._exposure import collapses
from heinzel.heads.cli._command import command
from heinzel.heads.cli._progress import ConsoleProgress
from heinzel.heads.cli._serve import serve_group
from heinzel.kernel import HeinzelError, Kernel, ModuleRecord, OperationLet, Progress, boot

console = Console()
err_console = Console(stderr=True)

REPOSITORY = "https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
LOGO = "🧝"
TAGLINE = "open-source helpers that keep product teams running"
FOOTER = f"{LOGO} [bold magenta]Heinzelmännchen[/bold magenta] [dim]hz {__version__} · {TAGLINE} · {REPOSITORY}[/dim]"
HELP = (
    f"{LOGO} [bold magenta]Heinzelmännchen[/bold magenta]: {TAGLINE}, working in the background like the "
    "helpers of Cologne.\n\nEvery operation accepts [bold]--json[/bold]. Start with [bold]hz system info[/bold] or "
    "[bold]hz system doctor[/bold]."
)
PLAIN_HELP = f"{LOGO} Heinzelmännchen: {TAGLINE}. Every operation accepts --json."
CORE_PANEL = "Core"
PLUGINS_PANEL = "Plugins"
EX_CONFIG = 78  # sysexits.h: configuration error


class SortedGroup(TyperGroup):
    """A command group that lists commands and subgroups together, alphabetically."""

    def list_commands(self, ctx: object) -> list[str]:
        """Command names in alphabetical order."""
        del ctx
        return sorted(self.commands)


def build(kernel: Kernel, *, plain: bool = False) -> typer.Typer:
    """Build the `hz` command tree from the modules registered with the kernel.

    Args:
        kernel: The booted kernel.
        plain: Help without Rich markup and footer, for the generated reference docs.
    """
    footer = None if plain else FOOTER
    app = typer.Typer(
        name="hz",
        help=PLAIN_HELP if plain else HELP,
        epilog=None if plain else footer,
        cls=SortedGroup,
        no_args_is_help=True,
        pretty_exceptions_show_locals=False,
        rich_markup_mode="rich",
        context_settings={"help_option_names": ["-h", "--help"]},
    )

    def print_version(value: bool) -> None:
        _print_version(kernel, value)

    @app.callback()
    def main(
        version: Annotated[  # noqa: ARG001 - handled by the eager callback
            bool,
            typer.Option(
                "--version",
                "-V",
                help="Show the version, Python, platform, installation and plugins, then exit.",
                callback=print_version,
                is_eager=True,
            ),
        ] = False,
    ) -> None:
        logging.basicConfig(
            level=kernel.settings.log_level,
            format="%(message)s",
            handlers=[RichHandler(console=err_console, show_path=False)],
        )

    progress = ConsoleProgress(err_console)
    kernel.bind(Progress, progress)
    mount = _Mount(kernel, progress, footer)
    mount(app, None)
    if system := mount.groups.get("system"):
        system.add_typer(serve_group(SortedGroup, footer), cls=SortedGroup)
    return app


def main() -> None:
    """Entry point of the `hz` executable."""
    try:
        kernel = boot()
    except (HeinzelError, ValidationError) as exc:
        err_console.print(f"[red]Configuration error:[/red] {exc}")
        err_console.print("Check the files `hz system config show` lists and your HZ_* environment variables.")
        sys.exit(EX_CONFIG)
    build(kernel)()


@dataclass
class _Mount:
    """Adds modules' operations and submodules to command groups, alphabetically, remembering each group."""

    kernel: Kernel
    progress: ConsoleProgress
    footer: str | None
    groups: dict[str, typer.Typer] = field(default_factory=dict[str, typer.Typer])

    def __call__(self, parent: typer.Typer, qualname: str | None) -> None:
        kernel, footer = self.kernel, self.footer
        entries: list[tuple[str, OperationLet | ModuleRecord]] = []
        if qualname is not None:
            entries += [(operation.name, operation) for operation in _operations(kernel, kernel.module(qualname))]
        entries += [(record.module.name, record) for record in kernel.children(qualname)]
        for _, entry in sorted(entries, key=lambda pair: pair[0]):
            match entry:
                case OperationLet():
                    parent.command(name=entry.name, help=entry.summary, epilog=footer)(self._command(entry))
                case ModuleRecord():
                    self._module(parent, qualname, entry)

    def _module(self, parent: typer.Typer, qualname: str | None, record: ModuleRecord) -> None:
        operations = _operations(self.kernel, record)
        panel = (CORE_PANEL if record.origin.builtin else PLUGINS_PANEL) if qualname is None else None
        if collapses(self.kernel, record) and operations:
            parent.command(
                name=record.module.name, help=operations[0].summary, epilog=self.footer, rich_help_panel=panel
            )(self._command(operations[0]))
        elif operations or self.kernel.children(record.qualname):
            group = typer.Typer(
                name=record.module.name,
                help=record.module.summary,
                epilog=self.footer,
                cls=SortedGroup,
                no_args_is_help=True,
            )
            self.groups[record.qualname] = group
            self(group, record.qualname)
            parent.add_typer(group, cls=SortedGroup, rich_help_panel=panel)

    def _command(self, operation: OperationLet) -> Callable[..., None]:
        return command(self.kernel, operation, console, err_console, self.progress)


def _operations(kernel: Kernel, record: ModuleRecord) -> list[OperationLet]:
    return [
        let
        for owner, let in kernel.lets(OperationLet)
        if owner.qualname == record.qualname and kernel.available(let.func)
    ]


def _print_version(kernel: Kernel, value: bool) -> None:
    if not value:
        return
    console.print(f"hz {__version__}", highlight=False)
    console.print(f"python {platform.python_version()} ({sys.executable})", highlight=False, soft_wrap=True)
    console.print(f"platform {platform.platform()}", highlight=False)
    kind = "uv tool" if (Path(sys.prefix) / "uv-receipt.toml").is_file() else "environment"
    console.print(f"installation {kind} {sys.prefix}", highlight=False, soft_wrap=True)
    plugins = sorted({(r.origin.distribution, r.origin.version) for r in kernel.modules if not r.origin.builtin})
    for distribution, plugin_version in plugins:
        console.print(f"plugin {distribution} {plugin_version}", highlight=False)
    raise typer.Exit
