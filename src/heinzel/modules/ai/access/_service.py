"""Service of the access module: every installed AI access option, the one in use, live tests and the switch."""

import logging
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from heinzel import (
    AccessStatus,
    AccessTest,
    Adapters,
    AiAccess,
    Check,
    Config,
    ConfigEditor,
    HeinzelError,
    Settings,
    Status,
)
from heinzel.kernel import CapabilityView, clean_text
from heinzel.modules.ai.access._models import (
    AccessList,
    AccessOption,
    AccessState,
    AccessTestReport,
    AccessUse,
    Bindings,
    LastTest,
)

logger = logging.getLogger(__name__)

CAPABILITY = "ai-access"
SECTION = "capabilities"
KEY = f"{SECTION}.ai_access"
RUNNER_KEY = f"{SECTION}.agent_runner"
VARIABLE = "HZ_CAPABILITIES__AI_ACCESS"
DEFAULT = "claude-code"
TESTS_FILE = "tests.json"
_TESTS = TypeAdapter(dict[str, LastTest])
DETAIL_LIMIT = 300


def state(status: AccessStatus, last: LastTest | None) -> AccessState:
    """The one status word people read: sign-in as far as the option tells offline, else the last test."""
    if not status.installed:
        return "not installed"
    if status.signed_in is not None:
        return "signed in" if status.signed_in else "not signed in"
    if last is None:
        return "not tested"
    return "tested" if last.ok else "test failed"


class ResultStore:
    """The last live check of each option, in `<state_dir>/ai/tests.json`, readable by this user only."""

    def __init__(self, root: Path) -> None:
        """Keep the file in `root`."""
        self.path = root / TESTS_FILE

    def load(self) -> dict[str, LastTest]:
        """The kept results by option; none when the file is missing or unreadable."""
        try:
            return _TESTS.validate_json(self.path.read_bytes())
        except (OSError, ValidationError):
            return {}

    def keep(self, name: str, last: LastTest) -> None:
        """Record `last` for `name`, replacing the file atomically."""
        results = self.load() | {name: last}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".tests-", suffix=".json")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(_TESTS.dump_json(results, indent=2))
            Path(temporary).replace(self.path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


def _isolated[T](name: str, what: str, call: Callable[[], T]) -> T | str:
    """`call()`, or why it failed: a plugin's option never breaks the listing."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - isolate plugins: one broken option leaves the others listed
        logger.warning("AI access %s: %s failed: %s", name, what, type(exc).__name__)
        return clean_text(f"its {what} failed ({type(exc).__name__}: {exc})")[:DETAIL_LIMIT]


class AccessService:
    """The AI access options installed on this machine (ADR 0057): list, test and switch."""

    def __init__(
        self, options: Adapters[AiAccess], view: CapabilityView, editor: ConfigEditor, settings: Settings
    ) -> None:
        """Create the service; every option's read handle, the capability view, the editor and settings are injected."""
        self._options = options
        self._view = view
        self._editor = editor
        self._tests = ResultStore(settings.state_dir / "ai")

    @staticmethod
    def bindings() -> Bindings:
        """The bindings as the files and environment say now, not as at boot: `hz ui` keeps running.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        return Config.load().settings(Bindings, SECTION)

    def status_of(self, name: str) -> AccessStatus | str:
        """What option `name` says about itself, or why it couldn't say."""
        return _isolated(name, "status", self._options[name].status)

    def option(self, name: str, selected: str | None, last: LastTest | None) -> AccessOption:
        """The listing's row of option `name`."""
        adapter = next(a for a in self._view.adapters if (a.let.capability, a.let.adapter) == (CAPABILITY, name))
        status = self.status_of(name)
        row = AccessOption(
            name=name,
            title=name,
            summary="",
            module=adapter.module,
            plugin=None if adapter.distribution == "heinzel" else adapter.distribution,
            in_use=name == selected,
            status="error",
            hosts=list(self._view.hosts(adapter)[0]),
            last_test=last,
        )
        if isinstance(status, str):
            return row.model_copy(update={"detail": status})
        return row.model_copy(
            update={
                "title": status.title,
                "summary": status.summary,
                "status": state(status, last),
                "detail": status.detail,
                "fix": status.fix,
                "models": status.models,
                "runner": status.runner,
            }
        )

    def listing(self) -> AccessList:
        """Every installed option with its status, the one in use, and what runs chat, turns and coding today.

        Raises:
            HeinzelError: If a configuration file is not valid TOML.
        """
        bindings = self.bindings()
        selected, runner = bindings.ai_access, bindings.agent_runner
        tests = self._tests.load()
        options = [self.option(name, selected, tests.get(name)) for name in self._options]
        chosen = next((o for o in options if o.in_use), None)
        note, next_steps = None, list[str]()
        if selected is None:
            note = "No AI access is chosen, so nothing reaches AI."
            next_steps = [f"hz ai access use {DEFAULT}"]
        elif chosen is None:
            note = f"No installed option is called {selected!r}; install its plugin or pick another."
            next_steps = [f"hz ai access use {o.name}" for o in options][:1]
        elif chosen.runner and chosen.runner != runner:
            note = (
                f"{chosen.title} names the agent runner {chosen.runner}, but {runner or 'none'} is bound, so the chat "
                f"doesn't follow the AI access."
            )
            next_steps = [f"hz ai access use {chosen.name}"]
        elif runner != self._view.bound("agent-runner"):
            # Bindings are read once at boot: a running `hz ui` or MCP server keeps the runner it started with.
            note = (
                f"The agent runner is now {runner or 'none'}, but this hz process started with "
                f"{self._view.bound('agent-runner') or 'none'}; restart `hz ui` or the MCP server to apply it."
            )
        elif chosen.status in {"not installed", "not signed in"} and chosen.fix:
            next_steps = [chosen.fix]
        elif chosen.status == "not tested":
            next_steps = [f"hz ai access test --name {chosen.name}"]
        through = f"the agent runner {runner}" if runner else "nothing: no agent runner is bound"
        uses = [
            AccessUse(use="chat", access=selected, runs=through),
            AccessUse(
                use="turns",
                access=selected,
                runs=f"{through}, for workflow renders; the turn engine (ADR 0050) isn't built yet",
            ),
            AccessUse(use="coding", access=selected, runs=f"not built yet (ADR 0050); handoffs will use {through}"),
        ]
        return AccessList(selected=selected, options=options, uses=uses, note=note, next=next_steps)

    def _known(self, name: str) -> None:
        if name not in self._options:
            known = ", ".join(self._options) or "none"
            msg = (
                f"No AI access option is called {name!r}; installed: {known}. Pick one, e.g. "
                f"`hz ai access use {DEFAULT}`, or install the plugin that offers it."
            )
            raise HeinzelError(msg)

    def use(self, name: str) -> AccessList:
        """Make `name` the AI access in use, and bind the agent runner it names, in the user configuration file.

        Raises:
            HeinzelError: If no installed option has that name, or a file can't be written.
        """
        self._known(name)
        status = self.status_of(name)
        runner = status.runner if isinstance(status, AccessStatus) else None
        self._editor.write(KEY, name, "user")
        missing = None
        if not isinstance(status, AccessStatus):
            missing = f"{name}'s status couldn't be read, so no agent runner was bound: {status}"
        elif runner is None:
            missing = f"{name} has no agent of its own; the chat keeps its agent runner."
        elif runner in self._view.installed("agent-runner"):
            self._editor.write(RUNNER_KEY, runner, "user")
        else:
            missing = f"{name} names the agent runner {runner}, which no installed module provides."
        listing = self.listing()
        if listing.selected != name:
            overridden = f"{VARIABLE} in your environment decides the AI access ({listing.selected}); unset it."
            return listing.model_copy(update={"note": overridden, "next": [f"unset {VARIABLE}"]})
        return listing.model_copy(update={"note": " ".join(p for p in (missing, listing.note) if p) or None})

    def test(self, name: str | None) -> AccessTestReport:
        """Run the live check of `name`, or of the option in use, and keep its result for the listing.

        Raises:
            HeinzelError: If no installed option has that name, or none is in use and none was named.
        """
        if name is None:
            name = self.bindings().ai_access
            if name is None:
                msg = f"No AI access is chosen; name one to test, e.g. `hz ai access test --name {DEFAULT}`."
                raise HeinzelError(msg)
        self._known(name)
        result = _isolated(name, "test", self._options[name].test)
        if isinstance(result, str):
            result = AccessTest(ok=False, detail=result)
        last = LastTest(
            at=datetime.now(UTC),
            ok=result.ok,
            detail=result.detail,
            model=result.model,
            cost_usd=result.cost_usd,
            duration_seconds=result.duration_seconds,
        )
        try:
            self._tests.keep(name, last)
        except OSError as exc:
            logger.warning("Keeping the test of %s failed: %s", name, exc)
        return AccessTestReport(name=name, **result.model_dump())

    def checks(self) -> list[Check]:
        """Doctor: the AI access in use is installed and signed in, and the bound agent runner is the one it names."""
        listing = self.listing()
        chosen = next((o for o in listing.options if o.in_use), None)
        if chosen is None:
            fix = listing.next[0] if listing.next else f"hz ai access use {DEFAULT}"
            return [Check(name="ai access", status=Status.DEGRADED, detail=listing.note or "none in use", fix=fix)]
        detail = f"{chosen.name}: {chosen.status}" + (f"; {chosen.detail}" if chosen.detail else "")
        if chosen.status in {"not installed", "not signed in", "test failed", "error"}:
            fix = chosen.fix or f"hz ai access test --name {chosen.name}"
            return [Check(name="ai access", status=Status.DEGRADED, detail=detail, fix=fix)]
        if listing.note:
            fix = listing.next[0] if listing.next else ""
            return [Check(name="ai access", status=Status.DEGRADED, detail=listing.note, fix=fix)]
        return [Check(name="ai access", status=Status.UP, detail=detail)]


__all__ = ["AccessService", "ResultStore", "state"]
