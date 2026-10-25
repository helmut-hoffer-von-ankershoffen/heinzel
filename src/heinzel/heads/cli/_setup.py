"""`hz setup [MODULE]`: modules' interactive setup steps, wizard style (ADR 0032, amended).

Only the CLI runs setup steps: MCP and REST clients are not people at a keyboard. `hz join <team repository>` is the
front door; `hz setup` is what you re-run for whatever is still missing. Steps run in their phase's order (who you are
first), built-in modules first inside a phase, then the others alphabetically; phases order the steps and are never
shown. Optional steps can be skipped, required ones can't; a module that is already configured is offered a change.
Every finished step is recorded in `<state_dir>/setup.json`, so a run continues where the last one stopped, and an
answers file (`--answers`) answers keyed questions without a terminal. Ctrl-C stops and keeps what finished steps wrote.
"""

import getpass
import json
import shlex
import sys
import warnings
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table
from rich.text import Text

from heinzel.heads.cli import _playful
from heinzel.heads.cli._journey import SetupState, answers_toml, load_answers
from heinzel.heads.cli._render import TerminalSafe, message, terminal_safe
from heinzel.heads.cli._update import print_notice
from heinzel.kernel import (
    SETUP_PHASES,
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
HELP = (
    "Set up what is still missing, wizard style: every step not finished yet, in order; optional steps can be "
    "skipped, and a run continues where the last one stopped. With MODULE, set up only that module."
)
MODULE_HELP = "Set up only this module, by qualified name (`hz setup clock`); every module when left out."
PRIVACY = (
    "hz keeps your profile and answers on this machine only, readable by you alone. Joining a team puts your handle, "
    "GitHub login and role in its roster; your name and email stay off every repository, and hz records nothing "
    "about how much you do."
)
FINISHED = frozenset({"done", "kept"})
"""Outcomes after which a re-run doesn't offer the step again; skipped, failed and interrupted ones are offered."""


def has_terminal() -> bool:
    """Whether a person sees the questions on standard output and answers on standard input; the seam tests fake.

    Both must be a terminal: `hz setup > out.txt` would otherwise wait for answers to questions nobody sees.
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


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
            if secret:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)  # it would echo the secret instead
                    return getpass.getpass(terminal_safe(prompt))
            return self.console.input(Text(terminal_safe(prompt)))
        except EOFError:
            raise KeyboardInterrupt from None
        except getpass.GetPassWarning as exc:
            msg = "The terminal can't hide what you type, so the secret wasn't read; use `hz system credential set`."
            raise HeinzelError(msg) from exc

    def show(self, renderable: object) -> None:
        """Print a renderable (a table, a preview) as it is."""
        self.console.print(renderable)

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
    """Every setup step that can run, in order: by phase, then built-in modules first, then by qualified name.

    Among plugins, the layers of your stack come first, most specific first, so your team's recommendations are
    offered before your org's and your company's (ADR 0040); the order is the stack's when the run starts.
    """
    found = [(record, let) for record, let in kernel.lets(SetupLet) if kernel.available(let.func)]
    layers = [layer.module for layer in kernel.layer_stack().layers]
    rank = {module: index for index, module in enumerate(reversed(layers))}
    return sorted(
        found,
        key=lambda pair: (
            SETUP_PHASES.index(pair[1].phase),
            not pair[0].origin.builtin,
            rank.get(pair[0].path[0], len(rank)),
            pair[0].path,
        ),
    )


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

    def __init__(
        self, kernel: Kernel, prompter: Prompter, state: SetupState | None = None, *, from_file: bool = True
    ) -> None:
        """Run steps of `kernel`, asking through `prompter`, recording each finished step in `state`.

        `from_file` says the preset answers came from a file someone else may have written, so a person confirms
        before they change a module already set up; flags the person typed need no such question.
        """
        self.kernel = kernel
        self.prompter = prompter
        self.state = state
        self.from_file = from_file
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
        result = self._outcome(record, let, offer_skip=offer_skip)
        if self.state is not None and result.outcome is not Outcome.FAILED:
            prefix = f"{record.qualname}."
            answers = {key: value for key, value in self.prompter.answered.items() if key.startswith(prefix)}
            if result.outcome is Outcome.KEPT and (earlier := self.state.steps.get(record.qualname)):
                answers = earlier.answers  # nothing changed: the answers that configured it still do
            self.state.record(record.qualname, result.outcome.value, answers)
        return result

    def _outcome(self, record: ModuleRecord, let: SetupLet, *, offer_skip: bool) -> StepResult:
        kind = "required" if let.required else "optional"
        self.prompter.say("")
        self.prompter.say(f"{record.qualname} ({kind}): {let.summary}")
        self.prompter.scope = record.qualname  # its keyed questions are `<module>.<key>` in an answers file
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
        prefix = f"{record.qualname}."
        configured = is_configured(self.kernel, record)
        if any(key.startswith(prefix) for key in self.prompter.presets):
            # Answered in advance: run it without asking, unless a person is there and it is configured already, so
            # a team's answers file never overwrites someone's settings unasked.
            if (
                configured
                and self.from_file
                and self.prompter.interactive
                and not ask(
                    f"{record.qualname} is already set up; the answers file would change it. Change it?", default=False
                )
            ):
                return Outcome.KEPT
        elif not self.prompter.interactive:
            # Only an answers file: a configured module keeps its configuration, an optional step without answers is
            # skipped, and a required one runs and names the first answer it lacks.
            if configured:
                return Outcome.KEPT
            if offer_skip and not let.required:
                return Outcome.SKIPPED
        elif configured:
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


@dataclass(frozen=True, kw_only=True, slots=True)
class Options:
    """How a setup run goes: from the start, with answers given in advance, and with JSON instead of tables."""

    restart: bool = False
    answers: Path | None = None
    as_json: bool = False


RESTART_HELP = "Forget which steps finished and go through every one again."
ANSWERS_HELP = (
    'Answer in advance from a TOML file with a table per module, e.g. `role = "engineer"` in the org table; runs '
    "without a terminal, for coding agents and teams. Never put secrets in it."
)
PRINT_ANSWERS_HELP = "Print the answers of the steps that finished as an answers file, and run nothing."
JSON_HELP = "Print the outcome of every step as JSON on stdout; questions, if any, go to stderr."


@dataclass(frozen=True, slots=True)
class SetupCommands:
    """Builds `hz setup [MODULE]`, the one command that runs setup steps (ADR 0032)."""

    kernel: Kernel
    console: Console
    err_console: Console

    def command(self) -> Callable[..., None]:
        """The Typer callable of `hz setup [MODULE]`."""

        def complete(incomplete: str) -> list[str]:
            return [record.qualname for record, _ in steps(self.kernel) if record.qualname.startswith(incomplete)]

        def setup(
            module: Annotated[
                str | None,
                typer.Argument(help=MODULE_HELP, metavar="MODULE", show_default=False, autocompletion=complete),
            ] = None,
            restart: Annotated[bool, typer.Option("--restart", help=RESTART_HELP)] = False,
            answers: Annotated[
                Path | None, typer.Option("--answers", help=ANSWERS_HELP, dir_okay=False, show_default=False)
            ] = None,
            print_answers: Annotated[bool, typer.Option("--print-answers", help=PRINT_ANSWERS_HELP)] = False,
            as_json: Annotated[bool, typer.Option("--json", help=JSON_HELP)] = False,
        ) -> None:
            if print_answers:
                self.print_answers()
                return
            self.run(module, Options(restart=restart, answers=answers, as_json=as_json))

        setup.__doc__ = HELP
        return setup

    def state(self) -> SetupState:
        """Which steps finished, from `<state_dir>/setup.json`."""
        return SetupState.load(self.kernel.settings.state_dir)

    def print_answers(self) -> None:
        """Print the recorded answers as an answers file.

        Raises:
            typer.Exit: With 1 when no step recorded an answer yet.
        """
        recorded = self.state().answers()
        if not recorded:
            self.err_console.print(
                message("Error:", "no answers recorded yet; run `hz setup` first, then `hz setup --print-answers`."),
                highlight=False,
                soft_wrap=True,
            )
            raise typer.Exit(code=1)
        typer.echo(answers_toml(recorded), nl=False)

    def run(self, name: str | None, options: Options | None = None) -> None:
        """Run every step still missing, or only the step of the module `name`.

        Raises:
            typer.BadParameter: If no module named `name` has a step that can run.
            typer.Exit: With 1 when a step failed, doctor reports a check down or there is no terminal, 130 after
                Ctrl-C.
        """
        options = options or Options()
        plan = steps(self.kernel)
        if name is not None:
            every, plan = plan, [(record, let) for record, let in plan if record.qualname == name]
            if not plan:
                raise typer.BadParameter(self._no_step(name, every), param_hint="MODULE")
        with self.errors():
            presets = load_answers(options.answers) if options.answers is not None else None
            state = self.state()
            if options.restart:
                state.clear(name)
            if name is None:
                self._everything(plan, state, presets, as_json=options.as_json)
            else:
                self._module(plan, state, presets, as_json=options.as_json)

    def _no_step(self, name: str, every: list[tuple[ModuleRecord, SetupLet]]) -> str:
        """Why `hz setup <name>` has nothing to run, and which modules do have a step."""
        having = f"Modules with one: {', '.join(record.qualname for record, _ in every) or 'none'}."
        if not any(record.qualname == name for record in self.kernel.modules):
            return f"No module is named {name!r}. {having}"
        if any(owner.qualname == name for owner, _ in self.kernel.lets(SetupLet)):
            return f"The setup step of {name!r} can't run: something it needs is missing (`hz system doctor`). {having}"
        return f"Module {name!r} has no setup step. {having}"

    def _everything(
        self,
        plan: list[tuple[ModuleRecord, SetupLet]],
        state: SetupState,
        presets: Mapping[str, object] | None,
        *,
        as_json: bool,
    ) -> None:
        if not plan:
            if as_json:
                typer.echo(json.dumps({"steps": [], "doctor": None}, indent=2))
            else:
                self.console.print("No module has a setup step.")
                self._notice()
            return
        prompter = self.prompter(plan, presets, as_json=as_json)
        if not as_json and _playful.allowed(self.kernel.settings, self.console):
            self.console.print(_playful.swarm())
        prompter.say(PRIVACY)
        pending = self.pending(plan, state)
        finished = [record.qualname for record, let in plan if (record, let) not in pending]
        if finished and not pending:
            last = max(state.steps[name].at for name in finished)[:10]
            prompter.say(
                f"Everything is set up (last on {last}). `hz setup <module>` changes one step; `hz setup --restart` "
                "goes through every step again."
            )
        elif finished:
            prompter.say(f"Already done: {', '.join(finished)}.")
        if pending:
            names = ", ".join(f"{r.qualname} ({'required' if s.required else 'optional'})" for r, s in pending)
            prompter.say(f"Still to do: {names}.")
            prompter.say("Ctrl-C stops; steps already finished keep what they wrote, and `hz setup` continues there.")
        results = Runner(self.kernel, prompter, state).run(pending, offer_skip=True)
        self.finish(results, as_json=as_json)

    @staticmethod
    def pending(plan: list[tuple[ModuleRecord, SetupLet]], state: SetupState) -> list[tuple[ModuleRecord, SetupLet]]:
        """The steps of `plan` no earlier run finished: never run, skipped, failed or interrupted."""
        return [
            (record, let)
            for record, let in plan
            if (step := state.steps.get(record.qualname)) is None or step.outcome not in FINISHED
        ]

    def finish(self, results: list[StepResult], *, as_json: bool, extra: Mapping[str, object] | None = None) -> None:
        """The summary, doctor on the configuration just written, and the exit code; `extra` joins the JSON.

        Raises:
            typer.Exit: With the exit code when it isn't 0.
        """
        code = exit_code(results)
        if results and not as_json:
            self.console.print()
            self.console.print(TerminalSafe(summary(results)))
        doctor = None if code == EX_INTERRUPTED else self._doctor(show=not as_json)
        if as_json:
            typer.echo(
                json.dumps(
                    {**(extra or {}), "steps": [_json(r) for r in results], "doctor": doctor[1] if doctor else None},
                    indent=2,
                )
            )
        if doctor is None:
            if not as_json:
                self._notice()  # doctor's release check says it otherwise (ADR 0031)
        elif doctor[0]:
            self.err_console.print(
                message("Error:", "hz system doctor reports a problem with the configuration; see its fixes above."),
                highlight=False,
                soft_wrap=True,
            )
            code = code or doctor[0]
        if code:
            raise typer.Exit(code=code)

    def _module(
        self,
        plan: list[tuple[ModuleRecord, SetupLet]],
        state: SetupState,
        presets: Mapping[str, object] | None,
        *,
        as_json: bool,
    ) -> None:
        prompter = self.prompter(plan, presets, as_json=as_json)
        [result] = Runner(self.kernel, prompter, state).run(plan, offer_skip=False)
        if as_json:
            typer.echo(json.dumps({"steps": [_json(result)], "doctor": None}, indent=2))
        else:
            if result.outcome.value == "done" and _playful.allowed(self.kernel.settings, self.console):
                self.console.print(_playful.glyph(), end=" ")
            self.console.print(Text(f"{result.module}: {result.outcome.value}"), highlight=False)
            self._notice()
        if code := exit_code([result]):
            raise typer.Exit(code=code)

    def _notice(self) -> None:
        """The update notice after human output, like every other command (ADR 0031)."""
        print_notice(self.kernel, self.err_console)

    @contextmanager
    def errors(self) -> Generator[None]:
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

    def prompter(
        self, plan: list[tuple[ModuleRecord, SetupLet]], presets: Mapping[str, object] | None, *, as_json: bool
    ) -> Prompter:
        """The prompter a test bound, the terminal, the answers alone, or a refusal listing the equivalent commands.

        With `--json`, questions go to stderr so stdout holds only the JSON.

        Raises:
            typer.Exit: With 1 when nobody can answer and no answers were given.
        """
        bound = self.kernel.resolve(Prompter)
        if bound.interactive:
            chosen = bound
        elif has_terminal():
            chosen = ConsolePrompter(self.err_console if as_json else self.console)
        elif presets is not None:
            chosen = Prompter()  # nobody to ask: every keyed question needs its answer in the file
        else:
            self._refuse(plan)
        if presets is not None:
            chosen.presets = presets
        return chosen

    def _refuse(self, plan: list[tuple[ModuleRecord, SetupLet]]) -> NoReturn:
        """Say that setup needs a terminal or an answers file, and the commands that configure the same.

        Raises:
            typer.Exit: Always, with 1.
        """
        lines = [f"  {command}" for record, _ in plan for command in equivalent_commands(self.kernel, record)]
        self.err_console.print(
            message(
                "Error:",
                "setup asks questions and needs a terminal on standard input and output, or the answers in a file "
                "(`hz setup --answers answers.toml`). Without either, configure with:",
            ),
            highlight=False,
            soft_wrap=True,
        )
        listing = terminal_safe("\n".join(lines)) or "  (nothing to configure)"
        self.err_console.print(Text(listing), highlight=False, soft_wrap=True)
        raise typer.Exit(code=1)

    def _doctor(self, *, show: bool = True) -> tuple[int, str] | None:
        """`hz system doctor` on the configuration as it is now on disk, not as it was when `hz` started.

        Returns:
            Doctor's exit code (non-zero when a check is down) and overall status, or None when doctor is disabled.
        """
        fresh = Kernel(records=self.kernel.modules, errors=self.kernel.errors, config=Config.load())
        doctor = next(
            (let for owner, let in fresh.lets(OperationLet) if (owner.qualname, let.name) == DOCTOR),
            None,
        )
        if doctor is None:
            return None
        report = fresh.invoke(doctor)
        if show:
            self.console.print()
            self.console.print(TerminalSafe(doctor.render(report)) if doctor.render else report)
        status = str(getattr(report, "status", ""))
        return (doctor.exit_code(report) if doctor.exit_code else 0), status


def _json(result: StepResult) -> dict[str, object]:
    return {
        "module": result.module,
        "required": result.required,
        "outcome": result.outcome.value,
        "detail": result.detail,
    }
