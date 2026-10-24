"""`hz join <team repository>`: the front door of onboarding, what `uvx heinzel join <url>` runs.

One line, the same for everyone, posted in the team's chat. It says what hz keeps about you, finds out who you are
(the identity step: your gh and gws logins, this machine's clock; asks only your role and working hours), joins the team
through the team module's `join` operation (clone with your own access, find you in the roster, or ask its owners to
add you), then runs the setup steps still missing and doctor. `hz setup` re-runs whatever is still missing afterwards.

The head knows no module: it finds the `team join` operation and the `org` settings through the kernel, like doctor.
Every question has a key, so `--answers`, `--yes`, `--role` and `--as` answer them and a coding agent runs the same
line without a terminal; without a terminal, a question nobody answered fails naming its key.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel
from rich.console import Console

from heinzel.heads.cli._journey import SetupState, load_answers
from heinzel.heads.cli._setup import (
    ANSWERS_HELP,
    PRIVACY,
    Runner,
    SetupCommands,
    is_configured,
    steps,
)
from heinzel.kernel import Config, HeinzelError, Kernel, ModuleRecord, OperationLet, Prompter, SettingsLet, SetupLet

TEAM_JOIN = ("team", "join")
SCOPE = "join"
"""The answers file's table for the questions `join` itself asks: `[join] yes = true`, `handle = "theo"`."""
NONE_OF_THESE = "none of these"
HELP = (
    "Join a team from the one line posted in its chat: who you are is found from your logins, the team repository is "
    "the invite, and the setup steps still missing run after it."
)
URL_HELP = "The team repository's URL, the invite: https, ssh or a local path."


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
            yes: Annotated[bool, typer.Option("--yes", help="Make it your team without asking.")] = False,
            role: Annotated[
                str | None, typer.Option("--role", help="Your role, if hz should not ask it.", show_default=False)
            ] = None,
            handle: Annotated[
                str | None, typer.Option("--as", help="Your handle, if not your GitHub login.", show_default=False)
            ] = None,
            answers: Annotated[
                Path | None, typer.Option("--answers", help=ANSWERS_HELP, dir_okay=False, show_default=False)
            ] = None,
            as_json: Annotated[bool, typer.Option("--json", help="Print what happened as JSON on stdout.")] = False,
        ) -> None:
            self.run(url, yes=yes, role=role, handle=handle, answers=answers, as_json=as_json)

        join.__doc__ = HELP
        return join

    def run(  # noqa: PLR0913 - the options of `hz join`
        self,
        url: str,
        *,
        yes: bool = False,
        role: str | None = None,
        handle: str | None = None,
        answers: Path | None = None,
        as_json: bool = False,
    ) -> None:
        """Say what hz keeps, find who you are, join the team, then set up what is still missing.

        Raises:
            typer.Exit: With 1 when joining or a step failed, 130 after Ctrl-C.
        """
        setup = SetupCommands(self.kernel, self.console, self.err_console)
        with setup.errors():
            operation = self._operation()
            presets: dict[str, object] = dict(load_answers(answers)) if answers is not None else {}
            presets |= {key: value for key, value in (("org.role", role), ("org.handle", handle)) if value}
            if yes:
                presets[f"{SCOPE}.yes"] = True
            plan = steps(self.kernel)
            prompter = setup.prompter(plan, presets, as_json=as_json)
            state = SetupState.load(self.kernel.settings.state_dir)
            prompter.say(PRIVACY)
            runner = Runner(self.kernel, prompter, state)
            identity, rest = self._split(SetupCommands.pending(plan, state))
            results = runner.run(identity, offer_skip=True)
            if any(r.outcome.value in {"failed", "interrupted"} for r in results):
                setup.finish(results, as_json=as_json)
                return
            joined = self._join(operation, url, prompter)
            results += runner.run(rest, offer_skip=True) if joined.get("done") else []
            self._next(prompter, url, joined)
        setup.finish(results, as_json=as_json, extra={"team": joined})

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
        self, pending: list[tuple[ModuleRecord, SetupLet]]
    ) -> tuple[list[tuple[ModuleRecord, SetupLet]], list[tuple[ModuleRecord, SetupLet]]]:
        """Who-you-are steps first, then the others; a module already configured isn't asked again."""
        fresh = [(record, let) for record, let in pending if not is_configured(self.kernel, record)]
        return [p for p in fresh if p[1].phase == "identity"], [p for p in fresh if p[1].phase != "identity"]

    def _profile(self) -> dict[str, Any]:
        """The `[org]` settings, as the identity step left them on disk; empty without an org module."""
        for owner, let in self.kernel.lets(SettingsLet):
            if owner.qualname == "org":
                fresh = Kernel(records=self.kernel.modules, errors=self.kernel.errors, config=Config.load())
                return fresh.resolve(let.model).model_dump(mode="json")
        return {}

    def _join(self, operation: OperationLet, url: str, prompter: Prompter) -> dict[str, Any]:
        """Join through the team module: show its plan, then carry it out on a yes, or propose you when not listed."""
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
            # Without a person, only an explicit yes (`--yes`, `[join] yes = true`) changes anything.
            if not prompter.confirm(f"Make {team} your team?", default=prompter.interactive, key="yes"):
                return plan
            return _dump(self.kernel.invoke(operation, url=url, login=login, yes=True))
        prompter.say(f"The roster of {team} doesn't list you yet.")
        handle = profile.get("handle")
        if candidates := list(plan.get("candidates", [])):
            options = [*candidates, NONE_OF_THESE]
            mine = handle if handle in candidates else NONE_OF_THESE
            picked = prompter.choice("Which roster entry is you?", options, default=mine, key="handle")
            handle = handle if picked == NONE_OF_THESE else picked
        ask = f"Ask the owners of {team} to add you? (a pull request they merge)"
        if not prompter.confirm(ask, default=prompter.interactive, key="yes"):
            return plan
        role = profile.get("role") or "engineer"
        return _dump(
            self.kernel.invoke(operation, url=url, login=login, handle=handle, role=role, propose=True, yes=True)
        )

    def _next(self, prompter: Prompter, url: str, joined: dict[str, Any]) -> None:
        for line in joined.get("next", []):
            prompter.say(str(line))
        if joined.get("pull_request"):
            prompter.say(f"Pull request: {joined['pull_request']}")
        prompter.say("")
        prompter.say("What happens next")
        prompter.say("  hz setup             sets up whatever is still missing, any time")
        prompter.say(f"  invite the next one  post the same line: uvx heinzel join {url}")


def _dump(result: object) -> dict[str, Any]:
    """The team plan as JSON-ready data; the head reads its fields without importing the team module."""
    return result.model_dump(mode="json") if isinstance(result, BaseModel) else {}
