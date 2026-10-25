"""The CLI head: every module becomes a command group, every operation a command.

Commands and groups are sorted alphabetically at every level; built-in modules and plugins sit in separate help
panels. A module with exactly one operation of its own name and no submodules collapses into a plain command, so the
`changelog` submodule of `system` reads as `hz system changelog`; beside other operations, the one named like its
module runs when the group is called without a subcommand (`hz system expert`, next to `hz system expert on`).

Outside expert mode, help and shell completion list the everyday operations only, and say how many others there are
(ADR 0041); every command runs either way.
"""

import inspect
import logging
import platform
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.text import Text
from typer.core import TyperGroup

from heinzel import __version__
from heinzel.heads._exposure import collapses, owns_path
from heinzel.heads.cli import _playful, _update
from heinzel.heads.cli._branding import TAGLINE
from heinzel.heads.cli._branding import footer as brand_footer
from heinzel.heads.cli._chat import CHAT, chat_callback
from heinzel.heads.cli._chat import HELP as CHAT_HELP
from heinzel.heads.cli._command import command
from heinzel.heads.cli._join import HELP as JOIN_HELP
from heinzel.heads.cli._join import JoinCommand
from heinzel.heads.cli._progress import ConsoleProgress
from heinzel.heads.cli._render import TerminalSafe, message, terminal_safe
from heinzel.heads.cli._serve import UI_HELP, serve_group, ui_command
from heinzel.heads.cli._setup import HELP as SETUP_HELP
from heinzel.heads.cli._setup import SetupCommands
from heinzel.heads.cli._update import print_changelog_header, print_notice, start_check
from heinzel.kernel import HeinzelError, Kernel, ModuleRecord, OperationLet, Origin, Progress, boot

console = Console()
err_console = Console(stderr=True)

HELP = (
    "Every operation accepts [bold]--json[/bold]. Start with [bold]hz setup[/bold], then [bold]hz system doctor[/bold]."
)
PLAIN_HELP = (
    f"Heinzelmännchen: {TAGLINE}. Every operation accepts --json. Commands marked (expert) run like any other "
    "but are listed in help and completion only in expert mode: hz system expert on."
)
CORE_PANEL = "Core"
PLUGINS_PANEL = "Plugins"
EXPERT_MARK = "(expert)"
"""Marks commands for experts in the generated reference, which lists every command (ADR 0041)."""
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
        plain: Help without Rich markup and footer, for the generated reference docs: every command listed, the ones
            for experts marked.
    """
    footer = None if plain else brand_footer(console.width)
    app = typer.Typer(
        name="hz",
        help=PLAIN_HELP if plain else HELP,
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

    progress = ConsoleProgress(err_console, playful=_playful.allowed(kernel.settings, err_console))
    kernel.bind(Progress, progress)
    mount = _Mount(kernel, progress, footer, plain=plain, expert=kernel.settings.expert)
    count = mount(app, None) + Count(commands=3)  # setup, join and ui: everyday, for everyone
    # Top level by the maintainer's decision, the one exception to ADR 0018, and the only way to run a step (ADR 0032).
    setup = SetupCommands(kernel, console, err_console).command()
    app.command(name="setup", help=SETUP_HELP, epilog=footer, rich_help_panel=CORE_PANEL)(setup)
    # The invite line, `uvx heinzel join <team repository>`: the second exception to ADR 0018, by the maintainer.
    join = JoinCommand(kernel, console, err_console).command()
    app.command(name="join", help=JOIN_HELP, epilog=footer, rich_help_panel=CORE_PANEL)(join)
    # Top level too, by the maintainer's decision: the web head is how people who don't live in a terminal start
    # (ADR 0039, "a web head: a React app served by the API head").
    app.command(name="ui", help=UI_HELP, epilog=footer, rich_help_panel=CORE_PANEL)(ui_command(kernel, err_console))
    # `hz chat` at the top level as well, by the maintainer's decision: the chat is what a person talks to, and
    # without a subcommand it is the interactive chat (ADR 0045).
    if chat := mount.groups.get(CHAT):
        chat.callback(invoke_without_command=True, no_args_is_help=False, help=CHAT_HELP)(
            chat_callback(kernel, console, err_console, progress)
        )
        # Only at the top level: one path for one group, so help, docs and messages all say `hz chat`.
        if runner := mount.groups.get(CHAT.rpartition(".")[0]):
            runner.registered_groups = [g for g in runner.registered_groups if g.typer_instance is not chat]
        app.add_typer(chat, name="chat", cls=SortedGroup, rich_help_panel=CORE_PANEL)
    if system := mount.groups.get("system"):
        # Starting servers is for experts: agents and scripts are configured once, by someone who knows `hz`.
        serve = serve_group(SortedGroup, footer, kernel, console, err_console)
        for info in serve.registered_commands:
            info.help = mount.help(info.help or inspect.getdoc(info.callback) or "", expert=True)
        serve.info.help = mount.help(serve.info.help or "", expert=True)
        system.add_typer(serve, cls=SortedGroup, hidden=mount.hides(expert=True))
        servers = Count(len(serve.registered_commands), len(serve.registered_commands))
        count += servers
        mount.hint(system, mount.counts["system"] + servers)
    if not plain:  # the generated reference documents the commands to use, not the old ones
        mount.aliases(app)
        hidden = 0 if mount.expert else count.expert
        app.info.epilog = start_screen_footer(kernel, hidden)
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
    if not sys.argv[1:]:
        _banner(kernel)  # the bare `hz`: the start screen follows
    try:
        build(kernel)()
    finally:
        if check is not None:
            check.wait()


@dataclass(frozen=True, slots=True)
class Count:
    """How many commands a part of the tree holds, and how many of them are for experts only."""

    commands: int = 0
    expert: int = 0

    def __add__(self, other: "Count") -> "Count":
        """Both counts together."""
        return Count(self.commands + other.commands, self.expert + other.expert)

    @property
    def all_expert(self) -> bool:
        """Whether every command in it is for experts, so the part as a whole is too."""
        return self.commands > 0 and self.commands == self.expert


@dataclass
class _Mount:
    """Adds modules' operations and submodules to command groups, alphabetically, remembering each group.

    Outside expert mode, expert operations, and groups holding nothing else, are hidden: left out of help and shell
    completion, but they run when typed (ADR 0041). Inside a hidden group nothing is hidden, so its own help lists
    its commands. `plain` (the generated reference) lists everything and marks what is for experts.
    """

    kernel: Kernel
    progress: ConsoleProgress
    footer: str | None
    plain: bool = False
    expert: bool = False
    groups: dict[str, typer.Typer] = field(default_factory=dict[str, typer.Typer])
    counts: dict[str, Count] = field(default_factory=dict[str, Count])

    def __call__(self, parent: typer.Typer, qualname: str | None) -> Count:
        """Mount the operations and submodules of `qualname` (the top level for None) on `parent`; count them."""
        kernel, footer = self.kernel, self.footer
        entries: list[tuple[str, OperationLet | ModuleRecord]] = []
        if qualname is not None:
            record = kernel.module(qualname)
            # The operation named like its module is the group's own command, not one of its subcommands.
            entries += [(op.name, op) for op in _operations(kernel, record) if not owns_path(kernel, record, op)]
        entries += [(record.module.name, record) for record in kernel.children(qualname)]
        count = Count()
        for _, entry in sorted(entries, key=lambda pair: pair[0]):
            match entry:
                case OperationLet():
                    parent.command(
                        name=entry.name, help=self.help(entry.summary, expert=entry.audience == "expert"),
                        epilog=footer, hidden=self.hides(expert=entry.audience == "expert"),
                    )(self._command(entry, (qualname, entry.name)))  # fmt: skip
                    count += _count(entry)
                case ModuleRecord():
                    count += self._module(parent, qualname, entry)
        return count

    def hides(self, *, expert: bool) -> bool:
        """Whether a command or group for experts (when `expert`) is hidden in this tree."""
        return expert and not (self.expert or self.plain)

    def hint(self, group: typer.Typer, count: Count) -> None:
        """End a visible group's help with how many of its commands are hidden, like the start screen's."""
        if self.footer is not None and not count.all_expert and self.hides(expert=count.expert > 0):
            group.info.epilog = f"{self.footer}\n\n{expert_hint(count.expert)}"

    def help(self, text: str, *, expert: bool) -> str:
        """Help text, marked as for experts in the generated reference."""
        return f"{EXPERT_MARK} {text}" if expert and self.plain else text

    def _module(self, parent: typer.Typer, qualname: str | None, record: ModuleRecord) -> Count:
        operations = _operations(self.kernel, record)
        panel = (CORE_PANEL if record.origin.builtin else PLUGINS_PANEL) if qualname is None else None
        plugin = None if self.plain or qualname is not None else plugin_line(record.origin)
        # A setup step never changes the module's command path: it runs as `hz setup <module>` (ADR 0032).
        if collapses(self.kernel, record) and operations:
            operation = operations[0]
            expert = operation.audience == "expert"
            summary = self.help(operation.summary, expert=expert)
            parent.command(
                name=record.module.name,
                help=summary,
                short_help=f"{summary}\n{plugin}" if plugin else None,
                epilog=self.footer,
                rich_help_panel=panel,
                hidden=self.hides(expert=expert),
            )(self._command(operation, (record.qualname, operation.name)))
            return _count(operation)
        if not operations and not self.kernel.children(record.qualname):
            return Count()
        own = next((op for op in operations if owns_path(self.kernel, record, op)), None)
        group = typer.Typer(
            name=record.module.name,
            epilog=self.footer,
            cls=SortedGroup,
            no_args_is_help=own is None,
            invoke_without_command=own is not None,
        )
        self.groups[record.qualname] = group
        count = self(group, record.qualname)
        if own is not None:
            group.callback()(self._own(own, (record.qualname, own.name)))
            count += _count(own)
        if count.all_expert:
            # The group itself is what's hidden; whoever asks for its help or completes in it sees what it holds.
            for command_info in group.registered_commands:
                command_info.hidden = False
            for group_info in group.registered_groups:
                group_info.hidden = False
        self.counts[record.qualname] = count
        self.hint(group, count)
        summary = self.help(record.module.summary, expert=count.all_expert)
        short_help = f"{summary}\n{plugin}" if plugin else None
        parent.add_typer(
            group,
            cls=SortedGroup,
            help=summary,
            rich_help_panel=panel,
            short_help=short_help,
            hidden=self.hides(expert=count.all_expert),
        )
        return count

    def aliases(self, app: typer.Typer) -> None:
        """Mount old command paths of renamed operations as hidden commands that name the new one (ADR 0051).

        A path a module now holds keeps its module's meaning: an alias never shadows a command that exists.
        """
        groups: dict[str, typer.Typer] = {}
        for record, operation in self.kernel.lets(OperationLet):
            if not operation.aliases or not self.kernel.available(operation.func):
                continue
            path = record.path if owns_path(self.kernel, record, operation) else (*record.path, operation.name)
            new = " ".join(("hz", *path))
            for alias in operation.aliases:
                *words, name = alias.split()
                if words and words[0] in {r.module.name for r in self.kernel.children(None)}:
                    continue
                parent = app
                for depth, word in enumerate(words):
                    key = " ".join(words[: depth + 1])
                    if key not in groups:
                        groups[key] = typer.Typer(name=word, cls=SortedGroup, no_args_is_help=True)
                        parent.add_typer(groups[key], name=word, cls=SortedGroup, hidden=True)
                    parent = groups[key]
                parent.command(name=name, help=f"Deprecated: {new} runs it now.", epilog=self.footer)(
                    self._deprecated(operation, (record.qualname, operation.name), f"hz {alias}", new)
                )

    def _deprecated(
        self, operation: OperationLet, key: tuple[str | None, str], old: str, new: str
    ) -> Callable[..., None]:
        """The command of an old path: one line on stderr naming the new command, then the operation."""
        run = self._command(operation, key)

        def deprecated(**kwargs: Any) -> None:  # noqa: ANN401 - forwarded to the operation
            err_console.print(
                f"{old} is deprecated and goes in the next minor release; use {new}.",
                style="yellow",
                highlight=False,
                soft_wrap=True,
            )
            run(**kwargs)

        deprecated.__signature__ = inspect.signature(run)  # type: ignore[attr-defined]  # Typer reads it
        deprecated.__doc__ = run.__doc__
        return deprecated

    def _own(self, operation: OperationLet, key: tuple[str | None, str]) -> Callable[..., None]:
        """The callback of a group that runs its module's own operation when called without a subcommand."""
        run = self._command(operation, key)

        def own(ctx: typer.Context, **kwargs: Any) -> None:  # noqa: ANN401 - forwarded to the operation
            if ctx.invoked_subcommand is None:
                run(**kwargs)

        context = inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=typer.Context)
        own.__signature__ = inspect.Signature([context, *inspect.signature(run).parameters.values()])  # type: ignore[attr-defined]  # Typer reads it
        own.__doc__ = run.__doc__
        return own

    def _command(self, operation: OperationLet, key: tuple[str | None, str]) -> Callable[..., None]:
        # The update notice follows every text output on stderr (ADR 0031); JSON never gets it.
        epilogue = None if key in QUIET else partial(print_notice, self.kernel, err_console)
        prologue = partial(print_changelog_header, self.kernel, console) if key == CHANGELOG else None
        return command(
            self.kernel,
            operation,
            console,
            err_console,
            self.progress,
            prologue=prologue,
            epilogue=epilogue,
            module=key[0] or "",
        )


def _count(operation: OperationLet) -> Count:
    return Count(1, int(operation.audience == "expert"))


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


def start_screen_footer(kernel: Kernel, hidden: int = 0) -> str:
    """The footer of `hz` and `hz --help`, after the expert-mode hint and any update notice (ADR 0041).

    The update notice comes before that line when stdout is a terminal and there is one.
    """
    text = _update.notice(kernel) if _update.is_terminal(console) else None
    parts = [expert_hint(hidden)] if hidden else []
    if text:
        parts.append(f"[yellow]{text}[/yellow]")
    parts.append(brand_footer(console.width))
    return "\n\n".join(parts)


def expert_hint(hidden: int) -> str:
    """The line that ends the start screen outside expert mode: how many commands are hidden, and how to list them."""
    commands = "command for experts is" if hidden == 1 else "commands for experts are"
    return f"[dim]{hidden} more {commands} hidden; [bold]hz system expert on[/bold] lists them.[/dim]"


def _banner(kernel: Kernel) -> None:
    """The Heinzelmännchen beside its name and tagline, where a person watches a colour terminal."""
    if "--json" in sys.argv or "json" in sys.argv:
        return  # the flag is parsed later; a script asking for JSON gets no art
    if _playful.allowed(kernel.settings, console):
        lines = ["Heinzelmännchen", TAGLINE]
        console.print(_playful.banner(lines), end="")


def _print_version(kernel: Kernel, value: bool) -> None:
    if not value:
        return
    _banner(kernel)
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
