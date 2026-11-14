"""Service of the access module: every installed AI access option, the one in use, live tests and the switch."""

import fcntl
import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    NotSupportedError,
    Progress,
    Settings,
    Status,
    UsageEntry,
    UsageLog,
)
from heinzel.kernel import CapabilityView, clean_text
from heinzel.kernel._logging import log_event
from heinzel.modules.ai.access._models import (
    AccessList,
    AccessOption,
    AccessState,
    AccessTestReport,
    AccessUse,
    Bindings,
    LastTest,
    ModelTestResult,
    Outcome,
    Setup,
)
from heinzel.modules.ai.access._plugin_models import failure

logger = logging.getLogger(__name__)

CAPABILITY = "ai-access"
SECTION = "capabilities"
KEY = f"{SECTION}.ai_access"
RUNNER_KEY = f"{SECTION}.agent_runner"
PLUGIN_MODEL_KEY = "ai.access.plugin_model"
ENGINE_RUNNER = "engine"
"""The agent runner that runs a plugin's models for the chat with the picker and coding (ADR 0159)."""
VARIABLE = "HZ_CAPABILITIES__AI_ACCESS"
DEFAULT = "claude-code"
TESTS_FILE = "tests.json"
_TESTS = TypeAdapter(dict[str, LastTest])
DETAIL_LIMIT = 300
MAX_MODELS_TESTED = 24
"""The most models one `hz ai access test --all` asks, so a long list never runs for long or spends much."""
MODEL_TEST_TIMEOUT = 15.0
RUN_LOCK = ".test-all.lock"
"""Held while `hz ai access test --all` runs, so a second run, from any head, is refused instead of spending twice."""
"""How long one model has to answer in `hz ai access test --all` before it counts as unanswered."""


_CLIENT = re.compile(r"heinzel\[([a-z0-9-]+)\]")
"""An optional Python client of the core named in a fix, e.g. `heinzel[openai]`: an extra, never a plugin."""
CLIENTS = {"openai": "OpenAI", "anthropic": "Anthropic"}
MODEL_API = "pydantic-ai"
GATEWAY = "gateway"


def state(status: AccessStatus, last: LastTest | None) -> AccessState:
    """The one status word people read: sign-in as far as the option tells offline, else the last test."""
    if not status.installed:
        return "needs setup"
    if status.signed_in is not None:
        return "signed in" if status.signed_in else "not signed in"
    if last is None:
        return "not tested"
    return "tested" if last.ok else "test failed"


def setup_of(name: str, status: AccessStatus) -> tuple[Setup | None, str]:  # noqa: PLR0911 - one answer per gap
    """What setting option `name` up takes and the one thing missing, in a few words; none and empty when ready.

    Options are built into hz: what one lacks is a setting, a sign-in or an optional client, never a plugin.
    """
    if status.core_engine:
        if not status.installed:
            return None, f"{status.title} offers no models"
        return "plugin-model", ("Pick another model" if status.fix else "")
    if match := _CLIENT.search(status.fix):
        client = CLIENTS.get(match[1], match[1])
        return "client", f"Add the {client} client"
    if name == MODEL_API and (not status.installed or status.signed_in is False):
        if not status.models:
            return "model", "Add a model and API key"
        return "model", "Name the model's vendor" if not status.installed else "Add an API key"
    if name == GATEWAY and not status.installed:
        return "gateway", "Add your gateway command"
    if status.signed_in is False:
        return "sign-in", f"Sign in to {status.title}"
    if not status.installed:
        return None, f"Set up {status.title}"
    return None, ""


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
    """`call()`, or why it failed, by kind only: a plugin's option never breaks the listing."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - isolate plugins: one broken option leaves the others listed
        log_event(
            logger, "ai.access.option_failed", level=logging.WARNING, option=name, call=what, error=type(exc).__name__
        )
        return f"its {what} failed ({failure(exc)})"  # never the error's text: a provider's may echo a secret


class AccessService:
    """The AI access options installed on this machine (ADR 0057): list, test and switch."""

    def __init__(
        self,
        options: Adapters[AiAccess],
        view: CapabilityView,
        editor: ConfigEditor,
        settings: Settings,
        usage: UsageLog,
    ) -> None:
        """Create the service; every option's read handle, the capability view, the editor and settings are injected."""
        self._options = options
        self._view = view
        self._editor = editor
        self._tests = ResultStore(settings.state_dir / "ai")
        self._usage = usage

    @staticmethod
    def bindings() -> Bindings:
        """The bindings as the files and environment say now, not as at boot: `hz ui serve` keeps running.

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
        setup, needs = setup_of(name, status)
        return row.model_copy(
            update={
                "setup": setup,
                "needs": needs,
                "client": match[1] if setup == "client" and (match := _CLIENT.search(status.fix)) else None,
                "title": status.title,
                "summary": status.summary,
                "status": state(status, last),
                "detail": status.detail,
                "fix": status.fix,
                "models": status.models,
                "runner": status.runner,
                "model": status.model,
                "core_engine": status.core_engine,
                "unavailable_models": status.unavailable_models,
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
        elif not chosen.core_engine and chosen.runner and chosen.runner != runner:
            note = (
                f"{chosen.title} names the agent runner {chosen.runner}, but {runner or 'none'} is bound, so the chat "
                f"doesn't follow the AI access."
            )
            next_steps = [f"hz ai access use {chosen.name}"]
        # No "restart to apply" branch: a running head takes up a changed runner before its next call
        # (`Kernel.refresh_live_bindings`), so the files decide what runs.
        elif chosen.status in {"needs setup", "not signed in"} and chosen.fix:
            next_steps = [chosen.fix]
        elif chosen.status == "not tested":
            next_steps = [f"hz ai access test --name {chosen.name}"]
        through = f"the agent runner {runner}" if runner else "nothing: no agent runner is bound"
        if chosen is not None and chosen.core_engine:
            # A plugin's models: hz's own engine runs chat, drafts and schedules on them (ADR 0159).
            engine = f"hz's own engine on the models of {chosen.title}"
            uses = [
                AccessUse(use="chat", access=selected, runs=engine),
                AccessUse(use="turns", access=selected, runs=f"{engine}, for drafts and schedules"),
                AccessUse(
                    use="coding",
                    access=selected,
                    runs=f"{engine}, with read-only file tools in the session's folder"
                    if runner == ENGINE_RUNNER
                    else f"{through}, outside these models",
                ),
            ]
            return AccessList(selected=selected, options=options, uses=uses, note=note, next=next_steps)
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
        elif status.core_engine:
            # hz's own engine runs its models: chat needs no runner; coding binds the engine runner once installed.
            missing = self._use_models(status, runner)
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

    def _use_models(self, status: AccessStatus, runner: str | None) -> str | None:
        """Pin the plugin's model and bind the engine runner for coding; a note when coding stays elsewhere.

        The model in use is written to `ai.access.plugin_model`, so a new first row in the plugin's list never changes
        it, nor the fingerprint approved schedules are bound to (ADR 0159).
        """
        if status.model is None and (usable := [m for m in status.models if m not in status.unavailable_models]):
            self._editor.write(PLUGIN_MODEL_KEY, usable[0], "user")
        elif status.model is not None:
            self._editor.write(PLUGIN_MODEL_KEY, status.model, "user")
        if runner is not None and runner in self._view.installed("agent-runner"):
            self._editor.write(RUNNER_KEY, runner, "user")
            return None
        bound = self.bindings().agent_runner
        if bound is None:
            return None
        return (
            f"Chat, drafts and schedules use {status.title}; coding handoffs still run on the agent runner {bound}, "
            "outside these models."
        )

    def _tested(self, name: str | None) -> str:
        """`name`, or the option in use, once known to be installed.

        Raises:
            HeinzelError: If no installed option has that name, or none is in use and none was named.
        """
        if name is None:
            name = self.bindings().ai_access
            if name is None:
                msg = f"No AI access is chosen; name one to test, e.g. `hz ai access test --name {DEFAULT}`."
                raise HeinzelError(msg)
        self._known(name)
        return name

    def test(self, name: str | None, options: Adapters[AiAccess]) -> AccessTestReport:
        """Run the live check of `name`, or of the option in use, and keep its result for the listing.

        Raises:
            HeinzelError: If no installed option has that name, or none is in use and none was named.
        """
        name = self._tested(name)
        result = _isolated(name, "test", options[name].test)
        if isinstance(result, str):
            result = AccessTest(ok=False, detail=result)
        last = self._keep(name, result)
        self._usage.record(
            UsageEntry(
                at=last.at,
                cause="test",
                access=name,
                model=result.model,
                input_tokens=result.input_tokens or 0,
                output_tokens=result.output_tokens or 0,
                cost_usd=result.cost_usd,
            )
        )
        return AccessTestReport(name=name, **result.model_dump())

    def _keep(self, name: str, result: AccessTest) -> LastTest:
        """Keep `result` as the last test of `name` for the listing; a file that can't be written is only logged."""
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
            log_event(logger, "ai.access.keep_test_failed", level=logging.WARNING, option=name, error=str(exc))
        return last

    def test_all(self, name: str | None, options: Adapters[AiAccess], progress: Progress) -> AccessTestReport:
        """Ask every model of `name`, or of the option in use, one tiny request each, one after another (ADR 0159).

        Only when a person asks. At most `MAX_MODELS_TESTED` models, each within `MODEL_TEST_TIMEOUT`; the option
        keeps which answered, so the picker marks the others unavailable until a later test answers. Each request is
        recorded as usage, cause `test`. Ctrl-C stops between or during models; what was learned so far is kept. One
        run at a time per profile, whichever head starts it.

        Raises:
            HeinzelError: If no installed option has that name, none is in use and none was named, its models can't
                be listed, it can't test its models one by one, or another run is going on.
        """
        name = self._tested(name)
        option = options[name]
        status = self.status_of(name)
        if isinstance(status, str):
            msg = f"Couldn't list the models of {name}: {status}"
            raise HeinzelError(msg)
        if not status.models:
            msg = f"{status.title} names no models to test; `hz ai access test --name {name}` checks it."
            raise HeinzelError(msg)
        asked, untested = status.models[:MAX_MODELS_TESTED], status.models[MAX_MODELS_TESTED:]
        rows: list[ModelTestResult] = []
        with self._one_run():
            for index, model_id in enumerate(asked, 1):
                with progress.task(f"Testing {model_id} ({index}/{len(asked)})"):
                    rows.append(self._test_model(name, option, model_id, status.title))
        return self._summary(name, rows, untested)

    @contextmanager
    def _one_run(self) -> Iterator[None]:
        """Hold the one check of every model this profile may run at a time, across processes.

        Raises:
            HeinzelError: If another check of every model runs now, in a terminal or the app.
        """
        root = self._tests.path.parent
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle = os.open(root / RUN_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                msg = "A check of every model is already running, in a terminal or the app; wait for it to finish."
                raise HeinzelError(msg) from None
            yield
        finally:
            os.close(handle)  # closing releases the lock

    def _test_model(self, name: str, option: AiAccess, model_id: str, title: str) -> ModelTestResult:
        """One model's tiny request through `option`, recorded as usage when one was sent.

        Raises:
            HeinzelError: If the option can't test its models one by one.
        """
        try:
            result = option.test_model(model_id, MODEL_TEST_TIMEOUT)
        except NotSupportedError:
            msg = f"{title} can't test its models one by one; `hz ai access test --name {name}` checks the one in use."
            raise HeinzelError(msg) from None
        except Exception as exc:  # noqa: BLE001 - a plugin's failing check is a result
            detail = f"its test failed ({failure(exc)})"
            result = AccessTest(ok=False, detail=detail, model=model_id)
        if not result.skipped or result.input_tokens or result.output_tokens:
            self._usage.record(
                UsageEntry(
                    at=datetime.now(UTC),
                    cause="test",
                    access=name,
                    model=model_id,
                    input_tokens=result.input_tokens or 0,
                    output_tokens=result.output_tokens or 0,
                )
            )
        outcome: Outcome = "skipped" if result.skipped else "answered" if result.ok else "unanswered"
        return ModelTestResult(
            model=model_id,
            outcome=outcome,
            detail=clean_text(result.detail)[:DETAIL_LIMIT],
            duration_seconds=result.duration_seconds,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )

    def _summary(self, name: str, rows: list[ModelTestResult], untested: list[str]) -> AccessTestReport:
        """The report of a `--all` run, also kept as the option's last test."""
        count = {o: sum(r.outcome == o for r in rows) for o in ("answered", "unanswered", "skipped")}
        parts = [f"{count['answered']} of {count['answered'] + count['unanswered']} models answered"]
        if count["unanswered"]:
            parts.append(f"{count['unanswered']} marked unavailable until a test answers")
        if count["skipped"]:
            parts.append(f"{count['skipped']} skipped")
        if untested:
            parts.append(f"{len(untested)} not tested: at most {MAX_MODELS_TESTED} per run")
        result = AccessTest(
            ok=count["answered"] > 0,
            detail="; ".join(parts),
            duration_seconds=sum(r.duration_seconds for r in rows),
            input_tokens=sum(r.input_tokens or 0 for r in rows),
            output_tokens=sum(r.output_tokens or 0 for r in rows),
        )
        self._keep(name, result)
        return AccessTestReport(name=name, models=rows, untested=untested, **result.model_dump())

    def checks(self) -> list[Check]:
        """Doctor: the AI access in use is installed and signed in, and the bound agent runner is the one it names."""
        if self.bindings().ai_access is None:
            return [
                Check(name="ai access", status=Status.UP, detail="intentionally not configured; set up AI when needed")
            ]
        listing = self.listing()
        chosen = next((o for o in listing.options if o.in_use), None)
        if chosen is None:
            fix = listing.next[0] if listing.next else f"hz ai access use {DEFAULT}"
            return [Check(name="ai access", status=Status.DEGRADED, detail=listing.note or "none in use", fix=fix)]
        detail = f"{chosen.name}: {chosen.status}" + (f"; {chosen.detail}" if chosen.detail else "")
        if chosen.status in {"needs setup", "not signed in", "test failed", "error"}:
            fix = chosen.fix or f"hz ai access test --name {chosen.name}"
            return [Check(name="ai access", status=Status.DEGRADED, detail=detail, fix=fix)]
        if listing.note:
            fix = listing.next[0] if listing.next else ""
            return [Check(name="ai access", status=Status.DEGRADED, detail=listing.note, fix=fix)]
        return [Check(name="ai access", status=Status.UP, detail=detail)]


__all__ = ["AccessService", "ResultStore", "state"]
