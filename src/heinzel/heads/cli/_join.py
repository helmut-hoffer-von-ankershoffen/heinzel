"""`hz join <team repository>`: the front door of onboarding, and what the invite line posted in chat runs.

The same line for everyone (the perfect-onboarding ADR). It says what hz keeps about you, finds out who you are (the
identity step: your gh and gws logins, this machine's clock; it asks only your role and working hours), joins the team
through the team module's `join` operation (a read-only clone with your own access, then you in the roster, or a pull
request asking its owners to add you), then runs the setup steps still missing and doctor. `hz setup` re-runs whatever
is still missing afterwards.

The head knows no module: it finds the `team join` operation and the `org` settings through the kernel, like doctor.
Every question has a key, so `--answers`, `--yes`, `--propose`, `--role` and `--handle` answer them and a coding agent
runs the same line without a terminal; without one, a question nobody answered fails naming its key and its flag, and
nothing is changed without an explicit yes.
"""

import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from packaging.version import Version
from pydantic import BaseModel
from rich.console import Console

from heinzel import __version__
from heinzel.heads._presence import mark_presence
from heinzel.heads.cli._journey import SetupState, load_answers
from heinzel.heads.cli._render import message
from heinzel.heads.cli._setup import (
    ANSWERS_HELP,
    PRIVACY,
    Outcome,
    Runner,
    SetupCommands,
    StepResult,
    is_configured,
    steps,
)
from heinzel.kernel import Config, HeinzelError, Kernel, ModuleRecord, OperationLet, Prompter, SettingsLet, SetupLet

TEAM_JOIN = ("team", "join")
SCOPE = "join"
"""The answers file's table for what `join` itself asks: `[join] yes = true`, `propose = true`, `handle = "theo"`.

`join` is a reserved module name, so no module's questions share it.
"""
SOURCE = "git+https://github.com/helmut-hoffer-von-ankershoffen/heinzel"
"""Where the invite line installs hz from, until the distribution is published on an index."""
NONE_OF_THESE = "none of these"
HELP = "Join a team from its invite line: finds out who you are, joins the team, then sets up what is missing."
URL_HELP = "The team repository's URL, the invite: https, ssh or a local path."
YES_HELP = "Make it your team without asking (when its roster lists you)."
PROPOSE_HELP = "When the roster doesn't list you, ask its owners to add you without asking: opens a pull request."
HINTS = {
    "org.role": "pass `--role <role>`",
    "org.handle": "pass `--handle <handle>`",
    f"{SCOPE}.yes": "pass `--yes`",
    f"{SCOPE}.propose": "pass `--propose`",
}


def invite_line(url: str) -> str:
    """The line to post for the next person: runs this release of hz without an install, then joins `url`."""
    version = Version(__version__)
    ref = "main" if version.is_devrelease or version.local or version == Version("0.0.0") else f"v{version}"
    return f"uvx --from {SOURCE}@{ref} heinzel join {shlex.quote(url)}"


@dataclass(frozen=True, slots=True)
class JoinCommand:
    """Builds `hz join <team repository>`."""

    kernel: Kernel
    console: Console
    err_console: Console

    def command(self) -> Callable[..., None]:
        """The Typer callable of `hz join`."""

        def join(  # noqa: PLR0913, PLR0917 - the options of one command
            url: Annotated[str, typer.Argument(help=URL_HELP, metavar="URL", show_default=False)],
            yes: Annotated[bool, typer.Option("--yes", help=YES_HELP)] = False,
            propose: Annotated[bool, typer.Option("--propose", help=PROPOSE_HELP)] = False,
            role: Annotated[
                str | None, typer.Option("--role", help="Your role, so hz doesn't ask it.", show_default=False)
            ] = None,
            handle: Annotated[
                str | None,
                typer.Option("--handle", "--as", help="Your handle in the team's roster.", show_default=False),
            ] = None,
            answers: Annotated[
                Path | None, typer.Option("--answers", help=ANSWERS_HELP, dir_okay=False, show_default=False)
            ] = None,
            as_json: Annotated[bool, typer.Option("--json", help="Print what happened as JSON on stdout.")] = False,
        ) -> None:
            mark_presence(self.kernel, "team")
            presets: dict[str, object] = {}
            presets |= {key: value for key, value in (("org.role", role), ("org.handle", handle)) if value}
            presets |= {f"{SCOPE}.{key}": True for key, flag in (("yes", yes), ("propose", propose)) if flag}
            self.run(url, presets, answers=answers, as_json=as_json)

        join.__doc__ = HELP
        return join

    def run(self, url: str, flags: dict[str, object], *, answers: Path | None = None, as_json: bool = False) -> None:
        """Say what hz keeps, find who you are, join the team, then set up what is still missing.

        Raises:
            typer.Exit: With 1 when joining or a step failed, 130 after Ctrl-C.
            HeinzelError: Never past `setup.errors()`, which turns it into the error line and exit code 1.
        """
        setup = SetupCommands(self.kernel, self.console, self.err_console)
        joined: dict[str, Any] | None = None
        results: list[StepResult] = []
        error = ""
        with setup.errors():
            operation = self._operation()
            presets = {**(load_answers(answers) if answers is not None else {}), **flags}
            plan = steps(self.kernel)
            prompter = setup.prompter(plan, presets, as_json=as_json)
            prompter.hints = HINTS
            state = SetupState.load(self.kernel.settings.state_dir)
            prompter.say(PRIVACY)
            runner = Runner(self.kernel, prompter, state, from_file=answers is not None)
            identity, rest = self._split(plan, SetupCommands.pending(plan, state), prompter)
            results = runner.run(identity, offer_skip=True)
            if not any(r.outcome in {Outcome.FAILED, Outcome.INTERRUPTED} for r in results):
                try:
                    joined = self._join(operation, url, prompter)
                except KeyboardInterrupt:
                    prompter.say("")
                    results.append(StepResult(module="team", required=True, outcome=Outcome.INTERRUPTED))
                except HeinzelError as exc:
                    if not as_json:
                        raise
                    error = str(exc)
                    results.append(StepResult(module="team", required=True, outcome=Outcome.FAILED, detail=error))
                else:
                    results += runner.run(rest, offer_skip=True) if joined.get("done") else []
                    self._next(prompter, url, joined)
        extra = {"joined": bool(joined and joined.get("done")), "team": joined, "error": error or None}
        if not as_json and joined is not None and not joined.get("done") and not results_failed(results):
            self.err_console.print(
                message("Not joined:", "nothing was changed; run the line again and say yes when asked."),
                highlight=False,
                soft_wrap=True,
            )
        setup.finish(results, as_json=as_json, extra=extra)

    def _operation(self) -> OperationLet:
        """The team module's `join` operation.

        Raises:
            HeinzelError: If no installed module provides it.
        """
        found = next(
            (let for owner, let in self.kernel.lets(OperationLet) if (owner.qualname, let.name) == TEAM_JOIN), None
        )
        if found is None:
            msg = "This hz has no team module to join a team with; update hz (`uv tool upgrade heinzel`) and try again."
            raise HeinzelError(msg)
        return found

    def _split(
        self,
        plan: list[tuple[ModuleRecord, SetupLet]],
        pending: list[tuple[ModuleRecord, SetupLet]],
        prompter: Prompter,
    ) -> tuple[list[tuple[ModuleRecord, SetupLet]], list[tuple[ModuleRecord, SetupLet]]]:
        """Who-you-are steps first, then the others: those still missing, and any the answers or flags answer."""

        def wanted(record: ModuleRecord, let: SetupLet) -> bool:
            if any(key.startswith(f"{record.qualname}.") for key in prompter.presets):
                return True
            return (record, let) in pending and not is_configured(self.kernel, record)

        fresh = [(record, let) for record, let in plan if wanted(record, let)]
        return [p for p in fresh if p[1].phase == "identity"], [p for p in fresh if p[1].phase != "identity"]

    def _profile(self) -> dict[str, Any]:
        """The `[org]` settings, as the identity step left them on disk; empty without an org module."""
        for owner, let in self.kernel.lets(SettingsLet):
            if owner.qualname == "org":
                fresh = Kernel(records=self.kernel.modules, errors=self.kernel.errors, config=Config.load())
                return fresh.resolve(let.model).model_dump(mode="json")
        return {}

    def _join(self, operation: OperationLet, url: str, prompter: Prompter) -> dict[str, Any]:
        """Join through the team module: show its plan, then carry it out on a yes, or propose you when not listed.

        Raises:
            HeinzelError: If nobody is there to say yes and no yes was given in advance, or the team module refuses.
        """
        profile = self._profile()
        login = profile.get("github")
        prompter.scope = SCOPE
        plan = _dump(self.kernel.invoke(operation, url=url, login=login, yes=False))
        team = plan.get("team") or url
        prompter.say("")
        prompter.say(f"Joining {team}:")
        for action in plan.get("actions", []):
            prompter.say(f"  {'done ' if action.get('done') else 'to do'}  {action.get('what', '')}")
        if member := plan.get("member"):
            prompter.say(f"The roster lists you as {member['handle']} ({', '.join(member.get('roles', []))}).")
            if plan.get("done"):  # nothing waited for a yes: it was your team already
                return plan
            if not self._yes(prompter, f"Make {team} your team?", "yes", what="joining", default=True):
                return plan
            return _dump(self.kernel.invoke(operation, url=url, login=login, yes=True))
        prompter.say(f"The roster of {team} doesn't list you yet.")
        handle = profile.get("handle")
        if candidates := list(plan.get("candidates", [])):
            options = [*candidates, NONE_OF_THESE]
            mine = handle if handle in candidates else NONE_OF_THESE
            picked = prompter.choice("Which roster entry is you?", options, default=mine, key="handle")
            handle = handle if picked == NONE_OF_THESE else picked
        ask = f"Ask the owners of {team} to add you? This opens a pull request from your account."
        if not self._yes(prompter, ask, "propose", what="asking its owners to add you", default=False):
            return plan
        role = profile.get("role") or "engineer"
        return _dump(
            self.kernel.invoke(operation, url=url, login=login, handle=handle, role=role, propose=True, yes=True)
        )

    @staticmethod
    def _yes(prompter: Prompter, question: str, key: str, *, what: str, default: bool) -> bool:
        """A yes or no; without a person only a yes given in advance counts, and its absence names the flag.

        Raises:
            HeinzelError: If nobody can answer and no answer was given in advance.
        """
        if not prompter.interactive and not prompter.has_preset(key):
            full = prompter.full_key(key)
            msg = f"Not joined: {what} needs a yes; {HINTS[full]} (or `{key} = true` under [{SCOPE}]). Nothing changed."
            raise HeinzelError(msg)
        return prompter.confirm(question, default=default, key=key)

    def _next(self, prompter: Prompter, url: str, joined: dict[str, Any]) -> None:
        for line in joined.get("next", []):
            prompter.say(str(line))
        if joined.get("pull_request"):
            prompter.say(f"Pull request: {joined['pull_request']}")
        prompter.say("")
        prompter.say("What happens next")
        prompter.say(f"  keep hz             uv tool install {SOURCE}")
        prompter.say("  what is missing     hz setup, any time")
        prompter.say(f"  invite the next one post the same line: {invite_line(url)}")


def results_failed(results: list[StepResult]) -> bool:
    """Whether a step failed or was interrupted."""
    return any(r.outcome in {Outcome.FAILED, Outcome.INTERRUPTED} for r in results)


def _dump(result: object) -> dict[str, Any]:
    """The team plan as JSON-ready data; the head reads its fields without importing the team module."""
    return result.model_dump(mode="json") if isinstance(result, BaseModel) else {}
