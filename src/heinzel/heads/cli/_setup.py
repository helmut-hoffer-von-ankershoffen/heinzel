"""`hz setup` and `hz <module> setup`: modules' interactive setup steps, wizard style (ADR 0032).

Only the CLI runs setup steps: MCP and REST clients are not people at a keyboard. Steps run in a fixed order, built-in
modules first, then the others alphabetically. Optional steps can be skipped, required ones can't; a module that is
already configured is offered a change instead. Ctrl-C stops and keeps what finished steps wrote.
"""

import getpass
import shlex
import sys
import warnings
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from heinzel.heads.cli._render import TerminalSafe, message, terminal_safe
from heinzel.kernel import (
    Config,
    CredentialLet,
    Credentials,
    HeinzelError,
    Kernel,
    ModuleRecord,
    OperationLet,
    Prompter,
    ScriptExhausted,
    SettingsLet,
    SetupLet,
    toml_value,
)

DOCTOR = ("system.doctor", "doctor")
EX_INTERRUPTED = 130  # what shells report for a process stopped by Ctrl-C (128 + SIGINT)


def stdin_is_terminal() -> bool:
    """Whether a person can answer on standard input; the seam tests fake."""
    return sys.stdin.isatty()


class ConsolePrompter(Prompter):
    """Asks on the terminal: questions and output on stdout, answers from stdin; secrets aren't echoed."""

    interactive = True

    def __init__(self, console: Console) -> None:
        """Ask on `console`."""
        self.console = console

    def read(self, prompt: str, *, secret: bool = False) -> str:
        """One line from the person; end of input stops the run like Ctrl-C.

        Raises:
            KeyboardInterrupt: At the end of input.
            HeinzelError: If the terminal can't hide a secret as it is typed.
        """
        try:
            # getpass reads the terminal device, not stdin, so piped answers are read as plain lines.
            if secret and sys.stdin.isatty():
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)  # it would echo the secret instead
                    return getpass.getpass(terminal_safe(prompt))
            return self.console.input(Text(terminal_safe(prompt)))
        except EOFError:
            raise KeyboardInterrupt from None
        except getpass.GetPassWarning as exc:
            msg = "The terminal can't hide what you type, so the secret wasn't read; use `hz system credential set`."
            raise HeinzelError(msg) from exc

    def say(self, text: str) -> None:
        """Print a line as written, never as markup."""
        self.console.print(Text(terminal_safe(text)), highlight=False)


class Outcome(StrEnum):
    """What became of a setup step."""

    DONE = "done"
    KEPT = "kept"
    SKIPPED = "skipped"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    NOT_REACHED = "not reached"


@dataclass(frozen=True, kw_only=True, slots=True)
class StepResult:
    """One step of a run and what became of it."""

    module: str
    required: bool
    outcome: Outcome
    detail: str = ""


def steps(kernel: Kernel) -> list[tuple[ModuleRecord, SetupLet]]:
    """Every setup step that can run, in order: built-in modules first, then the others by qualified name."""
    found = [(record, let) for record, let in kernel.lets(SetupLet) if kernel.available(let.func)]
    return sorted(found, key=lambda pair: (not pair[0].origin.builtin, pair[0].path))


def step_of(kernel: Kernel, record: ModuleRecord) -> SetupLet | None:
    """The setup step of a module, if it has one that can run."""
    return next((let for owner, let in steps(kernel) if owner.qualname == record.qualname), None)


def is_configured(kernel: Kernel, record: ModuleRecord) -> bool:
    """Whether one of the module's settings is set in a file or the environment, or one of its credentials stored."""
    for owner, let in kernel.lets(SettingsLet):
        if owner.qualname == record.qualname and kernel.resolve(let.model).model_fields_set:
            return True
    owned = [let for owner, let in kernel.lets(CredentialLet) if owner.qualname == record.qualname]
    if not owned:
        return False
    try:
        credentials = kernel.resolve(Credentials)
        return any(credentials.has(let) for let in owned)
    except HeinzelError:  # no credential service or an unreadable keychain: nothing counts as stored
        return False


def equivalent_commands(kernel: Kernel, record: ModuleRecord) -> list[str]:
    """The commands that configure what the module's step would, for running without a terminal."""
    commands = [
        f"hz system config set {record.qualname}.{key} {shlex.quote(toml_value(value))}"
        for owner, let in kernel.lets(SettingsLet)
        if owner.qualname == record.qualname
        for key, value in kernel.resolve(let.model).model_dump(mode="json").items()
        if value is not None
    ]
    try:
        declared = kernel.resolve(Credentials).declared
    except HeinzelError:
        declared = []
    commands += [d.fix for d in declared if d.module == record.qualname]
    return commands


class Runner:
    """Runs setup steps with a prompter and collects what became of each."""

    def __init__(self, kernel: Kernel, prompter: Prompter) -> None:
        """Run steps of `kernel`, asking through `prompter`."""
        self.kernel = kernel
        self.prompter = prompter
        kernel.bind(Prompter, prompter)

    def run(self, plan: list[tuple[ModuleRecord, SetupLet]], *, offer_skip: bool) -> list[StepResult]:
        """Run each step in turn; after Ctrl-C the rest are not reached and earlier steps keep what they wrote."""
        results: list[StepResult] = []
        for index, (record, let) in enumerate(plan):
            try:
                results.append(self._step(record, let, offer_skip=offer_skip))
            except KeyboardInterrupt:
                self.prompter.say("")
                results.append(StepResult(module=record.qualname, required=let.required, outcome=Outcome.INTERRUPTED))
                results += [
                    StepResult(module=r.qualname, required=s.required, outcome=Outcome.NOT_REACHED)
                    for r, s in plan[index + 1 :]
                ]
                break
        return results

    def _step(self, record: ModuleRecord, let: SetupLet, *, offer_skip: bool) -> StepResult:
        kind = "required" if let.required else "optional"
        self.prompter.say("")
        self.prompter.say(f"{record.qualname} ({kind}): {let.summary}")
        try:
            outcome = self._ask_and_run(record, let, offer_skip=offer_skip)
        except ValidationError as exc:
            # The module's settings in a file or HZ_* variable are invalid, so neither its step nor its check can run.
            detail = f"its configuration is invalid: {invalid(exc)}{self._reset_hint(record, exc)}"
        except (HeinzelError, OSError) as exc:
            detail = str(exc)
        except (KeyboardInterrupt, ScriptExhausted):
            raise
        except Exception as exc:  # noqa: BLE001 - a broken plugin's step is reported, the other steps still run
            detail = f"{type(exc).__name__}: {exc} (a bug in the step; report it to the module's authors)"
        else:
            return StepResult(module=record.qualname, required=let.required, outcome=outcome)
        self.prompter.say(f"{record.qualname} failed: {detail}")
        return StepResult(module=record.qualname, required=let.required, outcome=Outcome.FAILED, detail=detail)

    def _reset_hint(self, record: ModuleRecord, exc: ValidationError) -> str:
        """How to reset the invalid setting, when it is one of the module's own."""
        models = [let.model for owner, let in self.kernel.lets(SettingsLet) if owner.qualname == record.qualname]
        location = exc.errors()[0]["loc"]
        if location and any(model.__name__ == exc.title for model in models):
            return f"; `hz system config unset {record.qualname}.{location[0]}` resets it, then run setup again"
        return ""

    def _ask_and_run(self, record: ModuleRecord, let: SetupLet, *, offer_skip: bool) -> Outcome:
        ask = self.prompter.confirm
        if is_configured(self.kernel, record):
            if not ask(f"{record.qualname} is already set up. Change it?", default=False):
                return Outcome.KEPT
        elif offer_skip and not let.required and not ask(f"Set up {record.qualname} now?"):
            return Outcome.SKIPPED
        self.kernel.call(let.func)
        return Outcome.DONE


def invalid(exc: ValidationError) -> str:
    """The first problem of invalid settings, and where to look for it."""
    error = exc.errors()[0]
    where = ".".join(map(str, error["loc"])) or "input"
    return f"{exc.title} {where}: {error['msg']}. `hz system config show` lists the files and variables it comes from"


def summary(results: list[StepResult]) -> Table:
    """A table of every step and what became of it."""
    table = Table(title="Setup")
    for column in ("Module", "Step", "Outcome", "Detail"):
        table.add_column(column, overflow="fold")
    for result in results:
        kind = "required" if result.required else "optional"
        table.add_row(Text(result.module), kind, result.outcome.value, Text(result.detail))
    return table


def exit_code(results: list[StepResult]) -> int:
    """130 after Ctrl-C, 1 when a step failed, else 0."""
    if any(r.outcome is Outcome.INTERRUPTED for r in results):
        return EX_INTERRUPTED
    return 1 if any(r.outcome is Outcome.FAILED for r in results) else 0


@dataclass(frozen=True, slots=True)
class SetupCommands:
    """Builds the `setup` commands of the CLI head."""

    kernel: Kernel
    console: Console
    err_console: Console

    def everything(self) -> None:
        """Set up every module that has a setup step, one after the other; optional steps can be skipped.

        Raises:
            typer.Exit: With 1 when a step failed or there is no terminal, 130 after Ctrl-C.
        """
        with self._errors():
            self._everything()

    def _everything(self) -> None:
        plan = steps(self.kernel)
        if not plan:
            self.console.print("No module has a setup step.")
            return
        prompter = self._prompter(plan)
        names = ", ".join(f"{r.qualname} ({'required' if s.required else 'optional'})" for r, s in plan)
        prompter.say(f"Setting up {len(plan)} module{'s' if len(plan) > 1 else ''}: {names}.")
        prompter.say("Ctrl-C stops; steps already finished keep what they wrote.")
        results = Runner(self.kernel, prompter).run(plan, offer_skip=True)
        self.console.print()
        self.console.print(TerminalSafe(summary(results)))
        if not any(r.outcome is Outcome.INTERRUPTED for r in results):
            self._doctor()
        if code := exit_code(results):
            raise typer.Exit(code=code)

    def module(self, record: ModuleRecord, let: SetupLet) -> None:
        """Run one module's setup step.

        Raises:
            typer.Exit: With 1 when the step failed or there is no terminal, 130 after Ctrl-C.
        """
        plan = [(record, let)]
        with self._errors():
            prompter = self._prompter(plan)
        results = Runner(self.kernel, prompter).run(plan, offer_skip=False)
        result = results[0]
        self.console.print(Text(f"{record.qualname}: {result.outcome.value}"), highlight=False)
        if code := exit_code(results):
            raise typer.Exit(code=code)

    @contextmanager
    def _errors(self) -> Generator[None]:
        """Invalid configuration and other expected failures as an error line and exit code 1, like every command.

        Raises:
            typer.Exit: With 1 for a `HeinzelError`, an invalid configuration or a file that can't be written.
        """
        try:
            yield
        except ValidationError as exc:
            self.err_console.print(message("Configuration error:", invalid(exc)), highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc
        except (HeinzelError, OSError) as exc:
            self.err_console.print(message("Error:", str(exc)), highlight=False, soft_wrap=True)
            raise typer.Exit(code=1) from exc

    def _prompter(self, plan: list[tuple[ModuleRecord, SetupLet]]) -> Prompter:
        """The prompter a test bound, the terminal, or a refusal listing the equivalent commands.

        Raises:
            typer.Exit: With 1 when nobody can answer.
        """
        bound = self.kernel.resolve(Prompter)
        if bound.interactive:
            return bound
        if stdin_is_terminal():
            return ConsolePrompter(self.console)
        lines = [f"  {command}" for record, _ in plan for command in equivalent_commands(self.kernel, record)]
        self.err_console.print(
            message("Error:", "setup asks questions and needs a terminal. Without one, configure with:"),
            highlight=False,
            soft_wrap=True,
        )
        listing = terminal_safe("\n".join(lines)) or "  (nothing to configure)"
        self.err_console.print(Text(listing), highlight=False, soft_wrap=True)
        raise typer.Exit(code=1)

    def _doctor(self) -> None:
        """`hz system doctor` on the configuration as it is now on disk, not as it was when `hz` started."""
        fresh = Kernel(records=self.kernel.modules, errors=self.kernel.errors, config=Config.load())
        doctor = next(
            (let for owner, let in fresh.lets(OperationLet) if (owner.qualname, let.name) == DOCTOR),
            None,
        )
        if doctor is None:
            return
        report = fresh.invoke(doctor)
        self.console.print()
        self.console.print(TerminalSafe(doctor.render(report)) if doctor.render else report)
