"""Service of the ui module: runs each contribution's source, views its result, and builds the calls the app runs."""

import inspect
import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from heinzel import (
    ActionLet,
    CardWidget,
    Config,
    ConfigEditor,
    ContributionLet,
    HeinzelError,
    Kernel,
    ListWidget,
    ModuleRecord,
    OperationLet,
    RunCommand,
    RunOperation,
    Slot,
    StatusItem,
    StatusLet,
    WidgetLet,
    clean_text,
)
from heinzel.kernel import Badge, Effect, OperationCall, Widget, operation_path, public_parameters
from heinzel.modules.system.ui._models import (
    ActionEntry,
    ContributionInfo,
    Entry,
    Kind,
    StatusEntry,
    UiSettings,
    WidgetEntry,
)

HEAD = "web"
"""The head the app calls operations through; a source or an operation effect must be exposed there."""
MAX_PROBLEM = 280
SECTION = "system.ui"
KEY = f"{SECTION}.contributions"
KINDS: dict[Kind, type[ContributionLet]] = {"status": StatusLet, "action": ActionLet, "widget": WidgetLet}
_ITEM = TypeAdapter(StatusItem | None)
_WIDGET: TypeAdapter[Any] = TypeAdapter(Widget | None)
_BADGE: TypeAdapter[Any] = TypeAdapter(Badge | None)


class Unusable(Exception):  # noqa: N818 - a reason, not a crash
    """Why a contribution's declaration can't work; its message is safe to show."""


def _on_head(operation: OperationLet, head: str) -> bool:
    """Whether a head exposes `operation`; `mcp` covers both MCP transports, as the heads read it."""
    return operation.heads is None or head in operation.heads


def _problems(exc: ValidationError) -> str:
    """Where a value is wrong and why, without the value itself."""
    return "; ".join(
        f"{'.'.join(map(str, e['loc']))}: {e['msg']}" if e["loc"] else e["msg"] for e in exc.errors(include_input=False)
    )


def _safe(text: str) -> str:
    return " ".join(clean_text(text).split())[:MAX_PROBLEM]


@dataclass(frozen=True, slots=True)
class Contribution:
    """A contribution with the module offering it."""

    record: ModuleRecord
    let: ContributionLet

    @property
    def id(self) -> str:
        """`<module>:<name>`."""
        return f"{self.record.qualname}:{self.let.name}"


class UiService:
    """What modules contribute to the web app (ADR 0056): status items, actions and widgets, each one isolated."""

    def __init__(self, kernel: Kernel, editor: ConfigEditor) -> None:
        """Create the service; the kernel and the configuration editor are injected."""
        self._kernel = kernel
        self._editor = editor

    def chosen(self) -> dict[str, dict[Slot, bool]]:
        """What the person decided per contribution and slot, read now: a running `hz ui` sees `hz system ui show`."""
        try:
            return {
                key: dict(value) for key, value in Config.load().settings(UiSettings, SECTION).contributions.items()
            }
        except (ValidationError, HeinzelError):  # a broken table must not take the app's surfaces with it
            return {}

    def slots(
        self, contribution: "Contribution", chosen: dict[str, dict[Slot, bool]] | None = None
    ) -> dict[Slot, bool]:
        """Each slot the contribution may show in, and whether it does: its default unless the person decided."""
        decided = (self.chosen() if chosen is None else chosen).get(contribution.id, {})
        return {slot: decided.get(slot, default) for slot, default in contribution.let.slots.items()}

    def listing(self) -> list[ContributionInfo]:
        """Every contribution with where it may show and whether it does, without running anything."""
        chosen = self.chosen()
        found: list[ContributionInfo] = []
        for kind, let_type in KINDS.items():
            for contribution in self.contributions(let_type):
                let = contribution.let
                title = let.label if isinstance(let, ActionLet) else let.name.replace("-", " ").capitalize()
                found.append(
                    ContributionInfo(
                        id=contribution.id,
                        module=contribution.record.qualname,
                        kind=kind,
                        title=title,
                        slots=self.slots(contribution, chosen),
                        defaults=dict(let.slots),
                    )
                )
        return found

    def show(self, item: str, slot: str | None, *, shown: bool) -> list[ContributionInfo]:
        """Show or hide a contribution in a slot: writes `[system.ui] contributions` to the user file.

        Raises:
            HeinzelError: If no contribution has that id, or the slot is none it may show in, or isn't given when it
                may show in several.
        """
        every = [c for let_type in KINDS.values() for c in self.contributions(let_type)]
        contribution = next((c for c in every if c.id == item), None)
        if contribution is None:
            msg = (
                f"No contribution {item!r}; `hz system ui list` shows them: {', '.join(c.id for c in every) or 'none'}"
            )
            raise HeinzelError(msg)
        allowed = list(contribution.let.slots)
        picked: Slot | None = next((s for s in allowed if s == slot or (slot is None and len(allowed) == 1)), None)
        if picked is None:
            msg = f"{item} shows in {', '.join(allowed)}; name one with --slot"
            raise HeinzelError(msg)
        chosen = self.chosen()
        decided: dict[Slot, bool] = dict(chosen.get(item, {}))
        decided[picked] = shown
        chosen[item] = decided
        self._editor.write(KEY, chosen, "user")
        return self.listing()

    def contributions[L: ContributionLet](self, kind: type[L]) -> list[Contribution]:
        """Every contribution of a kind, sorted by order, then id."""
        found = [Contribution(record, let) for record, let in self._kernel.lets(kind)]
        return sorted(found, key=lambda c: (c.let.order, c.id))

    def status(self, item: str | None = None) -> list[StatusEntry]:
        """The status items, each with its value or its problem; only `item` when given."""
        entries: list[StatusEntry] = []
        for contribution in self._pick(StatusLet, item):
            let = contribution.let
            assert isinstance(let, StatusLet)  # noqa: S101 - picked by kind
            value, problem = self._viewed(contribution, let.source, let.view, _ITEM)
            if value is not None:
                value, problem = self._with_effects(contribution.record, value)
            entries.append(StatusEntry(**self._entry(contribution, problem), item=value))
        return entries

    def widgets(self, item: str | None = None) -> list[WidgetEntry]:
        """The widgets, each with its value or its problem; only `item` when given."""
        entries: list[WidgetEntry] = []
        for contribution in self._pick(WidgetLet, item):
            let = contribution.let
            assert isinstance(let, WidgetLet)  # noqa: S101 - picked by kind
            value, problem = self._viewed(contribution, let.source, let.view, _WIDGET)
            if value is not None:
                value, problem = self._with_effects(contribution.record, value)
            entries.append(WidgetEntry(**self._entry(contribution, problem), widget=value))
        return entries

    def actions(self, item: str | None = None) -> list[ActionEntry]:
        """The actions, each with its effect and badge, or its problem; only `item` when given."""
        entries: list[ActionEntry] = []
        for contribution in self._pick(ActionLet, item):
            let = contribution.let
            assert isinstance(let, ActionLet)  # noqa: S101 - picked by kind
            badge, problem = None, None
            try:
                effect: Effect | None = self.resolve(contribution.record, let.effect)
            except Unusable as exc:
                effect, problem = None, str(exc)
            if problem is None and let.source is not None and let.badge is not None:
                badge, problem = self._viewed(contribution, let.source, let.badge, _BADGE)
            entries.append(
                ActionEntry(
                    **self._entry(contribution, problem), label=let.label, icon=let.icon, effect=effect, badge=badge
                )
            )
        return entries

    def problems(self) -> list[tuple[str, str]]:
        """What keeps a declaration from working, without running anything: `(id, problem)` for doctor."""
        found: list[tuple[str, str]] = []
        for kind in (StatusLet, ActionLet, WidgetLet):
            for contribution in self.contributions(kind):
                let = contribution.let
                try:
                    source = getattr(let, "source", None)
                    if source is not None:
                        self.source(contribution.record, source)
                    if isinstance(let, ActionLet):
                        self.resolve(contribution.record, let.effect)
                except Unusable as exc:
                    found.append((contribution.id, str(exc)))
        return found

    def source(self, record: ModuleRecord, name: str) -> OperationLet:
        """The read-only operation `name` of `record`'s module that a view may read.

        Raises:
            Unusable: If there is none, it writes, takes a required argument, or the web head doesn't expose it.
        """
        operation = self._operation(record, name)
        if not operation.read_only:
            msg = f"its source `{name}` writes; a contribution reads a read-only operation"
            raise Unusable(msg)
        if not _on_head(operation, HEAD):
            msg = f"its source `{name}` isn't exposed on the web head, so the app may not show what it returns"
            raise Unusable(msg)
        if any(p.default is inspect.Parameter.empty for p in public_parameters(self._kernel, operation)):
            msg = f"its source `{name}` takes a required argument; a source is called without arguments"
            raise Unusable(msg)
        return operation

    def resolve(self, record: ModuleRecord, effect: Effect) -> Effect:
        """`effect` with what only the server may say: an operation's call, whether a command runs as typed.

        Raises:
            Unusable: If an operation effect names no operation of the module the web head exposes, or its arguments
                don't fit the operation.
        """
        if isinstance(effect, RunOperation):
            return effect.model_copy(update={"call": self._call(record, effect)})
        if isinstance(effect, RunCommand):
            return effect.model_copy(update={"runs": self.runs(effect.command)})
        return effect

    def runs(self, command: str) -> bool:
        """Whether `command` names a read-only operation the CLI exposes, so the terminal may press Enter for it."""
        words = shlex.split(command)[1:]
        best: tuple[int, bool] = (0, False)
        for record, operation in self._kernel.lets(OperationLet):
            path = operation_path(self._kernel, record, operation)
            if len(path) > best[0] and tuple(words[: len(path)]) == path and _on_head(operation, "cli"):
                best = (len(path), operation.read_only)
        return best[1]

    def _call(self, record: ModuleRecord, effect: RunOperation) -> OperationCall:
        operation = self._operation(record, effect.operation)
        if not _on_head(operation, HEAD):
            msg = f"its operation `{effect.operation}` isn't exposed on the web head"
            raise Unusable(msg)
        parameters = {p.name: p for p in public_parameters(self._kernel, operation)}
        if unknown := sorted(set(effect.arguments) - set(parameters)):
            msg = f"`{effect.operation}` takes no argument {', '.join(unknown)}"
            raise Unusable(msg)
        if missing := sorted(
            name
            for name, p in parameters.items()
            if p.default is inspect.Parameter.empty and name not in effect.arguments
        ):
            msg = f"`{effect.operation}` needs {', '.join(missing)}"
            raise Unusable(msg)
        for name, value in effect.arguments.items():
            try:
                TypeAdapter(parameters[name].annotation).validate_python(value)
            except ValidationError as exc:
                msg = f"`{effect.operation}` argument {name}: {_problems(exc)}"
                raise Unusable(msg) from exc
        path = operation_path(self._kernel, record, operation)
        words = ["hz", *path]
        for name, value in effect.arguments.items():
            text = value if isinstance(value, str) else json.dumps(value)
            words += [f"--{name.replace('_', '-')}", shlex.quote(text)]
        summary = (inspect.getdoc(operation.func) or operation.summary).splitlines()[0]
        return OperationCall(
            method="GET" if operation.read_only else "POST",
            path="/" + "/".join(path),
            query=dict(effect.arguments),
            command=" ".join(words),
            summary=summary,
            read_only=operation.read_only,
        )

    def _operation(self, record: ModuleRecord, name: str) -> OperationLet:
        for owner, operation in self._kernel.lets(OperationLet):
            if owner.qualname == record.qualname and operation.name == name:
                if not self._kernel.available(operation.func):
                    msg = f"`{name}` needs a module that is turned off"
                    raise Unusable(msg)
                return operation
        msg = f"module {record.qualname} has no operation `{name}`"
        raise Unusable(msg)

    def _pick[L: ContributionLet](self, kind: type[L], item: str | None) -> list[Contribution]:
        found = self.contributions(kind)
        if item is None:
            return found
        picked = [c for c in found if c.id == item]
        if not picked:
            known = ", ".join(c.id for c in found) or "none"
            msg = f"No contribution {item!r}; there are: {known}"
            raise HeinzelError(msg)
        return picked

    def _viewed(
        self, contribution: Contribution, source: str, view: Callable[[Any], Any], adapter: TypeAdapter[Any]
    ) -> tuple[Any, str | None]:
        """The view of the source's result, checked against its shape; or None and why, never a traceback."""
        try:
            operation = self.source(contribution.record, source)
            result = self._kernel.invoke(operation)
            value = view(result)
            if isinstance(value, BaseModel):
                value = value.model_dump()  # validated again below, so a model built without checks can't pass
            return adapter.validate_python(value), None
        except Unusable as exc:
            return None, f"{contribution.id}: {exc}"
        except HeinzelError as exc:
            return None, _safe(f"{contribution.id}: {exc}")
        except ValidationError as exc:
            return None, _safe(f"{contribution.id} returned something the app can't show: {_problems(exc)}")
        except Exception as exc:  # noqa: BLE001 - isolation boundary: one broken contribution never breaks the others
            return None, f"{contribution.id} failed: {type(exc).__name__}"

    def _with_effects[T: BaseModel](self, record: ModuleRecord, value: T) -> tuple[T | None, str | None]:
        """`value` with every effect in it resolved; None and why when one can't work."""
        try:
            return _map_effects(value, lambda effect: self.resolve(record, effect)), None
        except Unusable as exc:
            return None, f"{record.qualname}: {exc}"

    def _entry(self, contribution: Contribution, problem: str | None) -> dict[str, Any]:
        record = contribution.record
        return Entry(
            id=contribution.id,
            module=record.qualname,
            plugin=None if record.origin.builtin else record.origin.distribution,
            order=contribution.let.order,
            refresh=int(contribution.let.refresh.total_seconds()),
            slots=self.slots(contribution),
            problem=problem,
        ).model_dump()


def _map_effects[T: BaseModel](value: T, resolve: Callable[[Effect], Effect]) -> T:
    """`value` with `resolve` applied to each effect it holds: a status item's, a card's actions', a list's lines'."""
    if isinstance(value, StatusItem):
        actions = [a.model_copy(update={"effect": resolve(a.effect)}) for a in value.actions]
        effect = resolve(value.effect) if value.effect else None
        return value.model_copy(update={"effect": effect, "actions": actions})
    if isinstance(value, CardWidget):
        actions = [a.model_copy(update={"effect": resolve(a.effect)}) for a in value.actions]
        return value.model_copy(update={"actions": actions})
    if isinstance(value, ListWidget):
        items = [i.model_copy(update={"effect": resolve(i.effect)}) if i.effect else i for i in value.items]
        return value.model_copy(update={"items": items})
    return value
