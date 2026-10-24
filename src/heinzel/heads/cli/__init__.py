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
from functools import partial
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.text import Text
from typer.core import TyperGroup

from heinzel import __version__
from heinzel.heads._exposure import collapses
from heinzel.heads.cli import _update
from heinzel.heads.cli._command import command
from heinzel.heads.cli._join import HELP as JOIN_HELP
from heinzel.heads.cli._join import JoinCommand
from heinzel.heads.cli._progress import ConsoleProgress
from heinzel.heads.cli._render import TerminalSafe, message, terminal_safe
from heinzel.heads.cli._serve import serve_group
from heinzel.heads.cli._setup import HELP as SETUP_HELP
from heinzel.heads.cli._setup import SetupCommands
from heinzel.heads.cli._update import print_changelog_header, print_notice, start_check
from heinzel.kernel import HeinzelError, Kernel, ModuleRecord, OperationLet, Origin, Progress, boot

console = Console()
err_console = Console(stderr=True)

LOGO = "🧝"
TAGLINE = "open-source helpers that keep product teams running"
FOOTER = f"{LOGO} [bold magenta]Heinzelmännchen[/bold magenta] [dim]hz {__version__} · {TAGLINE}[/dim]"
HELP = (
    f"{LOGO} [bold magenta]Heinzelmännchen[/bold magenta]: {TAGLINE}, working in the background like the "
    "helpers of Cologne.\n\nEvery operation accepts [bold]--json[/bold]. Start with [bold]hz setup[/bold], then "
    "[bold]hz system doctor[/bold]."
)
PLAIN_HELP = f"{LOGO} Heinzelmännchen: {TAGLINE}. Every operation accepts --json."
CORE_PANEL = "Core"
PLUGINS_PANEL = "Plugins"
EX_CONFIG = 78  # sysexits.h: configuration error
QUIET = {("system", "release"), ("system", "update"), ("system.changelog", "changelog"), ("system.doctor", "doctor")}
"""Operations that say themselves whether a newer release is available, so no update notice follows them."""
CHANGELOG = ("system.changelog", "changelog")
"""Its text output starts with a header when a newer release is available (ADR 0031)."""


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
        epilog=None if plain else start_screen_footer(kernel),
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
                help="Show the version and repository, Python, platform, installation and plugins, then exit.",
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
    mount = _Mount(kernel, progress, footer, plain=plain)
    mount(app, None)
    # Top level by the maintainer's decision, the one exception to ADR 0018, and the only way to run a step (ADR 0032).
    setup = SetupCommands(kernel, console, err_console).command()
    app.command(name="setup", help=SETUP_HELP, epilog=footer, rich_help_panel=CORE_PANEL)(setup)
    # The invite line, `uvx heinzel join <team repository>`: the second exception to ADR 0018, by the maintainer.
    join = JoinCommand(kernel, console, err_console).command()
    app.command(name="join", help=JOIN_HELP, epilog=footer, rich_help_panel=CORE_PANEL)(join)
    if system := mount.groups.get("system"):
        system.add_typer(serve_group(SortedGroup, footer, kernel, console, err_console), cls=SortedGroup)
    return app


def main() -> None:
    """Entry point of the `hz` executable."""
    try:
        kernel = boot()
    except (HeinzelError, ValidationError) as exc:
        err_console.print(message("Configuration error:", str(exc)))
        err_console.print("Check the files `hz system config show` lists and your HZ_* environment variables.")
        sys.exit(EX_CONFIG)
    check = start_check(kernel)  # in the background, at most once an hour (ADR 0031)
    try:
        build(kernel)()
    finally:
        if check is not None:
            check.wait()


@dataclass
class _Mount:
    """Adds modules' operations and submodules to command groups, alphabetically, remembering each group."""

    kernel: Kernel
    progress: ConsoleProgress
    footer: str | None
    plain: bool = False
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
                    parent.command(name=entry.name, help=entry.summary, epilog=footer)(
                        self._command(entry, (qualname, entry.name))
                    )
                case ModuleRecord():
                    self._module(parent, qualname, entry)

    def _module(self, parent: typer.Typer, qualname: str | None, record: ModuleRecord) -> None:
        operations = _operations(self.kernel, record)
        panel = (CORE_PANEL if record.origin.builtin else PLUGINS_PANEL) if qualname is None else None
        plugin = None if self.plain or qualname is not None else plugin_line(record.origin)
        # A setup step never changes the module's command path: it runs as `hz setup <module>` (ADR 0032).
        if collapses(self.kernel, record) and operations:
            summary = operations[0].summary
            parent.command(
                name=record.module.name,
                help=summary,
                short_help=f"{summary}\n{plugin}" if plugin else None,
                epilog=self.footer,
                rich_help_panel=panel,
            )(self._command(operations[0], (record.qualname, operations[0].name)))
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
            short_help = f"{record.module.summary}\n{plugin}" if plugin else None
            parent.add_typer(group, cls=SortedGroup, rich_help_panel=panel, short_help=short_help)

    def _command(self, operation: OperationLet, key: tuple[str | None, str]) -> Callable[..., None]:
        # The update notice follows every text output on stderr (ADR 0031); JSON never gets it.
        epilogue = None if key in QUIET else partial(print_notice, self.kernel, err_console)
        prologue = partial(print_changelog_header, self.kernel, console) if key == CHANGELOG else None
        return command(
            self.kernel, operation, console, err_console, self.progress, prologue=prologue, epilogue=epilogue
        )


def _operations(kernel: Kernel, record: ModuleRecord) -> list[OperationLet]:
    return [
        let
        for owner, let in kernel.lets(OperationLet)
        if owner.qualname == record.qualname and kernel.available(let.func)
    ]


def plugin_line(origin: Origin) -> str | None:
    """A plugin's distribution and version, and its repository below, as escaped markup; None for built-ins."""
    if origin.builtin:
        return None
    text = f"{origin.distribution} {origin.version}" + (f"\n{origin.url}" if origin.url else "")
    return f"[dim]{escape(terminal_safe(text))}[/dim]"


def start_screen_footer(kernel: Kernel) -> str:
    """The footer of `hz` and `hz --help`, with the update notice when stdout is a terminal and there is one."""
    text = _update.notice(kernel) if _update.is_terminal(console) else None
    return f"{FOOTER}\n\n[yellow]{text}[/yellow]" if text else FOOTER


def _print_version(kernel: Kernel, value: bool) -> None:
    if not value:
        return
    origins = {(r.origin.distribution, r.origin.version, r.origin.url, r.origin.builtin) for r in kernel.modules}
    core = next((url for _, _, url, builtin in origins if builtin and url), None)
    lines = [f"hz {__version__}"]
    if core:
        lines.append(f"repository {core}")
    lines += [
        f"python {platform.python_version()} ({sys.executable})",
        f"platform {platform.platform()}",
        f"installation {'uv tool' if (Path(sys.prefix) / 'uv-receipt.toml').is_file() else 'environment'} {sys.prefix}",
    ]
    lines += [
        f"plugin {distribution} {version}" + (f" {url}" if url else "")
        for distribution, version, url, builtin in sorted(origins, key=lambda origin: origin[0])
        if not builtin
    ]
    for line in lines:
        console.print(TerminalSafe(Text(line)), highlight=False, soft_wrap=True)
    print_notice(kernel, err_console)
    raise typer.Exit
