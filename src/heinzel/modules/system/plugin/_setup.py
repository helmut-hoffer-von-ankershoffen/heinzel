"""Setup of the plugin module: a playbook's recommended bindings, set at install, and its checklist (ADR 0144).

A playbook's `capabilities.*` defaults are what a company (a layer, ADR 0040) recommends serves each capability. This
writes them through the same `ConfigEditor` as `hz system config set` and `hz setup`, by four rules, in order:

1. A binding the person configured (any value, `none` included) is never changed: it is `kept`.
2. A recommendation is written only for a connector that is installed; otherwise it is `skipped`, and the checklist
   offers to install it.
3. A recommendation is written even where the only installed connector serves already, so a second connector
   installed later doesn't unbind it.
4. Layers that disagree (`ORG_SITE_PRECEDENCE`) or two playbooks that recommend different connectors bind nothing:
   a `conflict`, for the person to choose.

Every write is remembered with the module that recommended it and the operation, so `unbind <module>` undoes
exactly those that still hold what setup wrote.
"""

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from packaging.utils import canonicalize_name
from pydantic import JsonValue

from heinzel import Check, ConfigEditor, HealthLet, HeinzelError, Kernel, Login, ModuleRecord, Progress, Status
from heinzel.kernel import CapabilityView, PlaybookLet
from heinzel.kernel._logging import get_logger, log_event, step
from heinzel.modules.system._phases import Phases
from heinzel.modules.system.plugin._setup_models import (
    BindingResult,
    Checklist,
    ChecklistRow,
    RowAction,
    SetupBind,
    SetupResult,
    SetupStep,
)

STATE_FILE = "setup-bindings.json"
"""Under the state directory: the bindings setup wrote, with who recommended them, and the playbooks considered."""
LINE = 60
"""Characters of a checklist row's status line."""
SECTION = "capabilities"
log = get_logger("plugin")


@dataclass(frozen=True, slots=True)
class _Recommendation:
    capability: str
    """The capability, as the core names it: `code-host`."""
    adapter: str | None
    """The connector; None for a conflict."""
    by: str
    """The module whose playbook recommends it."""
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{SECTION}.{self.capability.replace('-', '_')}"


def _line(text: str) -> str:
    """One status line of at most `LINE` characters, cut with an ellipsis."""
    text = " ".join(text.split())
    return text if len(text) <= LINE else text[: LINE - 1].rstrip() + "…"


def _run(kernel: Kernel, provider: Callable[..., list[Check]]) -> list[Check]:
    """A provider's checks; a provider that fails is a down check that says why, never a crash."""
    try:
        return kernel.call(provider)
    except HeinzelError as exc:
        return [Check(name="checks", status=Status.DOWN, detail=str(exc))]
    except Exception as exc:  # noqa: BLE001 - isolate plugins; their messages may carry what a terminal shouldn't show
        return [Check(name="checks", status=Status.DOWN, detail=f"the checks failed ({type(exc).__name__})")]


class SetupService:
    """Sets a playbook's recommended bindings, undoes them, and says what is still to do."""

    def __init__(self, kernel: Kernel, view: CapabilityView, editor: ConfigEditor) -> None:
        """Work on the kernel, its read-only view of capabilities and the configuration editor, injected."""
        self._kernel = kernel
        self._view = view
        self._editor = editor

    # Recommendations ------------------------------------------------------------------------------------------------

    def _playbooks(self, module: str | None, plugins: Collection[str]) -> list[tuple[ModuleRecord, PlaybookLet]]:
        """The playbooks asked for: of `module`, of the distributions `plugins`, or of every plugin installed."""
        wanted = {canonicalize_name(name) for name in plugins}
        found: list[tuple[ModuleRecord, PlaybookLet]] = []
        for record, let in self._kernel.lets(PlaybookLet):
            if module is not None:
                chosen = record.qualname == module
            elif wanted:
                chosen = canonicalize_name(record.origin.distribution) in wanted
            else:
                chosen = not record.origin.builtin
            if chosen:
                found.append((record, let))
        return found

    def _recommendations(self, playbooks: Sequence[tuple[ModuleRecord, PlaybookLet]]) -> list[_Recommendation]:
        """Each capability the playbooks recommend a connector for, once, the stack's precedence applied.

        A layer's recommendation is the one its stack offers (the most specific layer's value wins, ADR 0040); a
        playbook outside the stack offers its own. Different connectors for one capability are a conflict.
        """
        stack = self._kernel.layer_stack()
        layers = {layer.module: layer for layer in stack.layers}
        module_of = {layer.name: layer.module for layer in stack.layers}
        winners, conflicts = stack.recommendations()
        won = {r.key: r for r in winners}
        disputed = {c.key: c for c in conflicts}
        by_key: dict[str, list[_Recommendation]] = {}
        for record, let in playbooks:
            for raw, value in let.defaults.items():
                section, _, rest = raw.partition(".")
                if section != SECTION:
                    continue
                capability = rest.replace("_", "-")
                key = f"{SECTION}.{rest.replace('-', '_')}"
                if record.qualname in layers and key in disputed:
                    said = ", ".join(f"{name} recommends {v}" for name, v in disputed[key].values)
                    found = _Recommendation(capability, None, record.qualname, said)
                elif record.qualname in layers and key in won:
                    winner = won[key]
                    found = _Recommendation(capability, str(winner.value), module_of.get(winner.layer, record.qualname))
                else:
                    found = _Recommendation(capability, str(value), record.qualname)
                by_key.setdefault(key, []).append(found)
        merged: list[_Recommendation] = []
        for each in by_key.values():
            if conflict := next((r for r in each if r.adapter is None), None):
                merged.append(conflict)
                continue
            adapters = list(dict.fromkeys(r.adapter for r in each))
            if len(adapters) > 1:
                said = ", ".join(f"{r.by} recommends {r.adapter}" for r in each)
                merged.append(_Recommendation(each[0].capability, None, each[0].by, said))
            else:
                merged.append(each[0])
        return merged

    # Binding --------------------------------------------------------------------------------------------------------

    def bind(  # noqa: PLR0913 - what to bind and how, each by keyword
        self,
        *,
        module: str | None = None,
        plugins: Collection[str] = (),
        operation_id: str | None = None,
        once: bool = False,
        decline: bool = False,
        dry_run: bool = False,
    ) -> SetupBind:
        """Write the recommended bindings of the playbooks asked for, by the rules of this module (ADR 0144).

        `once` (an automatic repair) considers each playbook once per version of its plugin; `decline` (an install
        with `--no-bind`) only remembers the playbooks as considered, so a repair doesn't bind them later; `dry_run`
        says what it would do and writes nothing.
        """
        playbooks = self._playbooks(module, plugins)
        state = self._state()
        considered = set(cast("list[str]", state.get("considered", [])))
        if once:
            playbooks = [(r, p) for r, p in playbooks if _version_key(r) not in considered]
        with step(
            log,
            "plugin.bind.run",
            operation="bind",
            step="bind",
            plugins=sorted({r.origin.distribution for r, _ in playbooks}),
            operation_id=operation_id,
            dry_run=dry_run,
            declined=decline,
        ) as running:
            results = [
                self._bind_one(recommendation, state, operation_id, decline=decline, dry_run=dry_run)
                for recommendation in self._recommendations(playbooks)
            ]
            if not dry_run:
                state["considered"] = sorted(considered | {_version_key(r) for r, _ in playbooks})
                self._save(state)
            running.note(
                written=[r.capability for r in results if r.state == "written"],
                kept=[r.capability for r in results if r.state == "kept"],
                conflicts=[r.capability for r in results if r.state == "conflict"],
            )
            if not results:
                running.skip("nothing recommended")
        return SetupBind(bindings=results, message=_message(results, dry_run=dry_run))

    def _bind_one(
        self,
        recommendation: _Recommendation,
        state: dict[str, Any],
        operation_id: str | None,
        *,
        decline: bool,
        dry_run: bool,
    ) -> BindingResult:
        """Apply one recommendation by the rules, logged as `setup.bind.binding` (ADR 0138)."""
        capability, adapter = recommendation.capability, recommendation.adapter
        with step(
            log,
            "plugin.bind.binding",
            operation="bind",
            step="bind",
            plugin=recommendation.by,
            capability=capability,
            adapter=adapter,
        ) as running:
            result = self._decide(recommendation, decline=decline)
            if result.state == "written" and not dry_run:
                try:
                    self._editor.write(recommendation.key, adapter)
                except (HeinzelError, ValueError) as exc:
                    running.outcome = "error"
                    running.note(error=str(exc))
                    result = result.model_copy(update={"state": "skipped", "reason": f"not saved: {exc}"})
                else:
                    written = cast("dict[str, object]", state.setdefault("written", {}))
                    written[recommendation.key] = {
                        "adapter": adapter,
                        "by": recommendation.by,
                        "operation_id": operation_id,
                        "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    }
            running.note(state=result.state)
            if result.state != "written":
                running.skip(result.state)
        return result

    def _decide(self, recommendation: _Recommendation, *, decline: bool) -> BindingResult:
        capability, adapter, by = recommendation.capability, recommendation.adapter, recommendation.by
        if adapter is None:
            return BindingResult(
                capability=capability, adapter=None, state="conflict", reason=recommendation.reason, by=by
            )
        bound, how = self._view.binding(capability)
        if how in {"configured", "off"}:
            reason = "already set" if bound == adapter else f"yours: {bound or 'none'}"
            return BindingResult(capability=capability, adapter=adapter, state="kept", reason=reason, by=by)
        if adapter not in self._view.installed(capability):
            return BindingResult(capability=capability, adapter=adapter, state="skipped", reason="not installed", by=by)
        if decline:
            return BindingResult(capability=capability, adapter=adapter, state="skipped", reason="declined", by=by)
        return BindingResult(capability=capability, adapter=adapter, state="written", by=by)

    def unbind(self, by: str) -> SetupBind:
        """Remove the bindings setup wrote for `by`'s playbook; one the person changed since stays theirs."""
        state = self._state()
        written = cast("dict[str, dict[str, Any]]", state.get("written", {}))
        results: list[BindingResult] = []
        with step(log, "plugin.unbind.run", operation="unbind", step="unbind", plugin=by) as running:
            for key, entry in list(written.items()):
                if entry.get("by") != by:
                    continue
                capability = key.partition(".")[2].replace("_", "-")
                adapter = cast("str | None", entry.get("adapter"))
                if self._view.binding(capability) == (adapter, "configured"):
                    self._editor.unset(key)
                    results.append(BindingResult(capability=capability, adapter=adapter, state="removed", by=by))
                else:
                    results.append(
                        BindingResult(
                            capability=capability, adapter=adapter, state="kept", reason="changed since", by=by
                        )
                    )
                del written[key]
            self._save(state)
            running.note(removed=[r.capability for r in results if r.state == "removed"])
            if not results:
                running.skip("nothing set by it")
        removed = sum(r.state == "removed" for r in results)
        return SetupBind(bindings=results, message=f"Removed {removed} binding{'s' * (removed != 1)} {by} set.")

    def set_by(self) -> dict[str, str]:
        """Which module set each binding setup wrote, by setting key: what Settings shows as `set by acme`."""
        written = cast("dict[str, dict[str, Any]]", self._state().get("written", {}))
        return {key: str(entry.get("by")) for key, entry in written.items()}

    # Setup as an operation ------------------------------------------------------------------------------------------

    def run(self, module: str, answers: Mapping[str, JsonValue] | None = None) -> SetupResult:
        """Set up `module`'s playbook without questions: its bindings, the settings in `answers`, then the checklist.

        Steps a terminal must ask (choosing assets, writing templates) are reported `needs-cli`: `hz setup <module>`.

        Raises:
            HeinzelError: If no installed module named `module` has a playbook, or `answers` names a setting the
                playbook doesn't recommend.
        """
        playbooks = self._playbooks(module, ())
        if not playbooks:
            msg = f"No installed module named {module!r} has a playbook to set up."
            raise HeinzelError(msg)
        playbook = playbooks[0][1]
        own = {key for key in playbook.defaults if not key.startswith(f"{SECTION}.")}
        if unknown := sorted(set(answers or {}) - own):
            msg = f"{module}'s playbook doesn't recommend {', '.join(unknown)}; nothing was set."
            raise HeinzelError(msg)
        phases = Phases(
            self._kernel.resolve(Progress),
            [
                ("bind", "Set up which connector serves what"),
                ("settings", f"Apply {module}'s settings"),
                ("templates", "Templates and assets"),
                ("check", "Check what is still yours to do"),
            ],
            operation="system.plugin.setup",
        )
        steps: list[SetupStep] = []
        with phases.step("bind", "Set up which connector serves what"):
            bound = self.bind(module=module)
        steps.append(SetupStep(name="bind", state="done", detail=bound.message))
        steps.append(self._settings_step(phases, module, playbook, answers or {}))
        if playbook.templates or playbook.assets:
            detail = f"hz setup {module} asks which assets are yours and writes its templates"
            phases.note("templates", "Templates and assets", "skipped", detail)
            steps.append(SetupStep(name="templates", state="needs-cli", detail=detail))
        else:
            phases.note("templates", "Templates and assets", "skipped", "none")
            steps.append(SetupStep(name="templates", state="skipped", detail="none"))
        now = {r.capability: r.adapter for r in bound.bindings if r.state == "written" and r.adapter}
        with phases.step("check", "Check what is still yours to do"):
            checklist = self.checklist(module, bound_now=now)
        todo = sum(row.state != "connected" for row in checklist.rows)
        steps.append(SetupStep(name="check", state="done", detail=f"{todo} still to do"))
        return SetupResult(module=module, steps=steps, bindings=bound.bindings, checklist=checklist)

    def _settings_step(
        self, phases: Phases, module: str, playbook: PlaybookLet, answers: Mapping[str, JsonValue]
    ) -> SetupStep:
        label = f"Apply {module}'s settings"
        own = [key for key in playbook.defaults if not key.startswith(f"{SECTION}.")]
        if not own:
            phases.note("settings", label, "skipped", "none")
            return SetupStep(name="settings", state="skipped", detail="none")
        if not answers:
            detail = f"{len(own)} to accept: hz setup {module}, or answers"
            phases.note("settings", label, "skipped", detail)
            return SetupStep(name="settings", state="needs-cli", detail=detail)
        with phases.step("settings", label):
            for key, value in answers.items():
                self._editor.write(key, value)
        return SetupStep(name="settings", state="done", detail=f"{len(answers)} set")

    # Checklist ------------------------------------------------------------------------------------------------------

    def checklist(self, module: str | None = None, *, bound_now: Mapping[str, str] | None = None) -> Checklist:
        """One row per connector the playbook names: installed? serving? signed in? and the one next action.

        `bound_now` names bindings written in this process, which its capability view doesn't know yet.

        Raises:
            HeinzelError: If `module` names no installed module with a playbook.
        """
        playbooks = self._playbooks(module, ())
        if module is not None and not playbooks:
            msg = f"No installed module named {module!r} has a playbook."
            raise HeinzelError(msg)
        now = dict(bound_now or {})
        by_adapter: dict[str, list[str]] = {}
        for recommendation in self._recommendations(playbooks):
            if recommendation.adapter is not None:
                by_adapter.setdefault(recommendation.adapter, []).append(recommendation.capability)
        modules = {adapter.let.adapter: adapter.module for adapter in self._view.adapters}
        prefix = module or "setup"
        rows = [
            self._row(f"{prefix}:{adapter}", adapter, capabilities, modules.get(adapter), now)
            for adapter, capabilities in by_adapter.items()
        ]
        return Checklist(module=module, rows=rows, done=all(row.state == "connected" for row in rows))

    def _row(
        self, id_: str, adapter: str, capabilities: list[str], qualname: str | None, now: Mapping[str, str]
    ) -> ChecklistRow:
        def row(state: str, line: str, action: RowAction, *, owner_only: bool, serves: list[str]) -> ChecklistRow:
            return ChecklistRow.model_validate(
                {
                    "id": id_,
                    "connector": adapter,
                    "capabilities": serves,
                    "state": state,
                    "line": _line(line),
                    "action": action,
                    "owner_only": owner_only,
                }
            )

        if qualname is None:
            action = RowAction(kind="install", connector=adapter, command=f"hz system plugin add {adapter} --confirm")
            return row("not-installed", "Not installed", action, owner_only=False, serves=capabilities)
        serves, unbound = [], []
        for capability in capabilities:
            bound, how = self._view.binding(capability)
            if now.get(capability) == adapter or bound == adapter:
                serves.append(capability)
            elif capability in now or how in {"configured", "off"}:
                continue  # the person chose another connector, or none: theirs
            else:
                unbound.append(capability)
        if unbound:
            action = RowAction(
                kind="bind",
                connector=adapter,
                capability=unbound[0],
                command=f"hz system config set {SECTION}.{unbound[0].replace('-', '_')} {adapter}",
            )
            return row(
                "needs-attention",
                f"Doesn't serve {', '.join(unbound)} yet",
                action,
                owner_only=False,
                serves=[*serves, *unbound],
            )
        checks = [check for provider in self._health(qualname) for check in _run(self._kernel, provider)]
        login = next((check.login for check in checks if check.login is not None), None)
        if login is not None and login.signed_in is False:
            action = RowAction(kind="sign-in", connector=adapter, command=login.command)
            return row("not-connected", "Not signed in", action, owner_only=True, serves=serves)
        if problem := next((check for check in checks if check.status is not Status.UP), None):
            command = problem.action.command if problem.action is not None else problem.fix
            action = RowAction(kind="settings", connector=adapter, command=command)
            line = problem.cause or problem.detail
            return row("needs-attention", line, action, owner_only=True, serves=serves)
        action = RowAction(kind="check", connector=adapter, command="hz system doctor")
        return row("connected", _signed_in(login), action, owner_only=False, serves=serves)

    def _health(self, qualname: str) -> list[Callable[..., list[Check]]]:
        return [
            let.provider
            for record, let in self._kernel.lets(HealthLet)
            if record.qualname == qualname and not let.live and self._kernel.available(let.provider)
        ]

    # State ----------------------------------------------------------------------------------------------------------

    @property
    def _path(self) -> Path:
        return self._kernel.settings.state_dir / STATE_FILE

    def _state(self) -> dict[str, Any]:
        try:
            loaded: object = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log_event(log, "plugin.bind.unreadable", level=logging.WARNING, path=str(self._path), outcome="error")
            return {}
        return cast("dict[str, Any]", loaded) if isinstance(loaded, dict) else {}

    def _save(self, state: Mapping[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=self._path.parent, prefix=f".{STATE_FILE}.")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(state, out, indent=2, sort_keys=True)
            Path(name).replace(self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                Path(name).unlink()
            raise


def _version_key(record: ModuleRecord) -> str:
    return f"{canonicalize_name(record.origin.distribution)}@{record.origin.version}"


def _signed_in(login: Login | None) -> str:
    if login is not None and login.account:
        return f"Signed in as {login.account}"
    return "Connected"


def _message(results: Sequence[BindingResult], *, dry_run: bool) -> str:
    """One line, such as `Set 5 connector bindings; kept 1.`."""
    if not results:
        return "Nothing to bind: no playbook recommends a connector."
    written = sum(r.state == "written" for r in results)
    parts = [f"{'Would set' if dry_run else 'Set'} {written} connector binding{'s' * (written != 1)}"]
    if kept := sum(r.state == "kept" for r in results):
        parts.append(f"kept {kept}")
    if skipped := sum(r.state == "skipped" for r in results):
        parts.append(f"skipped {skipped}")
    if conflicts := sum(r.state == "conflict" for r in results):
        parts.append(f"{conflicts} to choose")
    return "; ".join(parts) + "."
