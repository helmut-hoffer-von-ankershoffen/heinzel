"""What your team recommends, against what you have, and accepting it into your user file (`hz org sync`).

A team repository may recommend plugins, capability bindings, credential names and contracts; the `OrgContext` lists
them with their origin. Recommend-only: `sync` shows each one beside what you have, and applies only what you accept,
by writing your own user configuration through `ConfigEditor`. A binding becomes `[capabilities] <capability>`, a
contract an entry of `[workflow] schedule_team` pinned to the file you saw. Plugins and credentials are never applied
by `sync`: installing code and storing a secret stay explicit commands, which it names.
"""

import logging
import shlex
from typing import Literal

from pydantic import BaseModel, Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Check, ConfigEditor, Credentials, HeinzelError, Kernel, Status
from heinzel.kernel import CapabilityView, OrgContext, Recommendation

logger = logging.getLogger(__name__)
WORKFLOW = "workflow"
SyncState = Literal["applied", "pending", "differs", "by-hand", "missing"]


class SyncItem(BaseModel):
    """One recommendation beside what you have."""

    id: str = Field(description="How you accept it: `<kind>:<name>`, e.g. `binding:documents`.")
    kind: str = Field(description="plugin, binding, credential or contract.")
    name: str = Field(description="The plugin, capability, credential (`service/name`) or contract.")
    recommended: str | None = Field(description="The adapter or the contract's `sha256:<hex>` recommended.")
    current: str | None = Field(description="What you have: the adapter bound, the contract's entry, or none.")
    state: SyncState = Field(
        description="applied (you have it), pending (accept it), differs (you chose otherwise; accept to switch), "
        "by-hand (run `action` yourself), missing (the repository lacks the contract's file)."
    )
    origin: str = Field(description="Which repository and file recommend it.")
    action: str = Field(description="What accepting it writes, or the command to run yourself.")
    applied_now: bool = Field(default=False, description="Whether this run applied it.")


class SyncReport(BaseModel):
    """Your team's recommendations and what became of them."""

    team: bool = Field(description="Whether you are in a team that could recommend anything.")
    items: list[SyncItem] = Field(description="Every recommendation, in the manifest's order.")
    next: list[str] = Field(description="What to do next.")


class SyncService:
    """Compares your team's recommendations with your configuration, and applies the ones you accept."""

    def __init__(self, kernel: Kernel, view: CapabilityView, editor: ConfigEditor) -> None:
        """Read recommendations through the kernel's `OrgContext`, bindings through `view`, write through `editor`."""
        self._kernel = kernel
        self._view = view
        self._editor = editor

    def report(self) -> SyncReport:
        """Every recommendation beside what you have; changes nothing."""
        context = self._context()
        if context is None or context.team_checkout() is None:
            return SyncReport(team=False, items=[], next=["Join your team first: hz team join <url>"])
        items = [self._item(r) for r in context.recommendations()]
        open_ = [i.id for i in items if i.state in {"pending", "differs"}]
        steps = [f"Accept them: hz org sync --accept {shlex.quote(','.join(open_))}"] if open_ else []
        steps += [i.action for i in items if i.state == "by-hand"]
        return SyncReport(team=True, items=items, next=steps or ["You have everything your team recommends."])

    def sync(self, accept: str | None) -> SyncReport:
        """Apply the recommendations named in `accept` (comma-separated ids, or `all`) to your user configuration.

        Raises:
            HeinzelError: If an id names no recommendation, or writing the configuration fails.
        """
        report = self.report()
        if not accept:
            return report
        wanted = {part.strip() for part in accept.split(",") if part.strip()}
        known = {item.id for item in report.items}
        if unknown := sorted(wanted - known - {"all"}):
            ids = ", ".join(sorted(known)) or "none"
            msg = f"Your team recommends no {', '.join(unknown)}; its recommendations: {ids}. See them: hz org sync"
            raise HeinzelError(msg)
        for item in report.items:
            if ("all" in wanted or item.id in wanted) and item.state in {"pending", "differs"}:
                self._apply(item)
                item.applied_now = True
                item.current, item.state = item.recommended, "applied"
        report.next = [i.action for i in report.items if i.state == "by-hand"] or report.next
        return report

    def health(self) -> list[Check]:
        """Drift from the team's recommendations, as one degraded check per recommendation you don't follow."""
        report = self.report()
        checks = [
            Check(
                name=f"recommendation {item.id}",
                status=Status.DEGRADED,
                detail=_drift(item),
                fix=item.action if item.state == "by-hand" else f"hz org sync --accept {item.id}",
            )
            for item in report.items
            if item.state in {"pending", "differs", "by-hand"}
        ]
        if report.team and not checks:
            checks.append(Check(name="recommendations", status=Status.UP, detail=f"{len(report.items)} followed"))
        return checks

    # Comparing and applying ------------------------------------------------------------------------------------------

    def _context(self) -> OrgContext | None:
        return self._kernel.resolve(OrgContext) if self._kernel.provides(OrgContext) else None

    def _item(self, recommendation: Recommendation) -> SyncItem:
        match recommendation.kind:
            case "binding":
                return self._binding(recommendation)
            case "contract":
                return self._contract(recommendation)
            case "plugin":
                installed = {record.origin.distribution for record in self._kernel.modules}
                state: SyncState = "applied" if recommendation.name in installed else "by-hand"
                action = f"hz system plugin add {shlex.quote(recommendation.name)}"
                return _item(recommendation, current=None, state=state, action=action)
            case "credential":
                return self._credential(recommendation)

    def _binding(self, recommendation: Recommendation) -> SyncItem:
        current = self._view.bound(recommendation.name)
        state: SyncState = "applied" if current == recommendation.value else "pending" if current is None else "differs"
        action = f"sets capabilities.{_key(recommendation.name)} = {recommendation.value!r} in your user file"
        return _item(recommendation, current=current, state=state, action=action)

    def _contract(self, recommendation: Recommendation) -> SyncItem:
        entries = self._schedule_team()
        current = next((e for e in entries if e.partition("@")[0] == recommendation.name), None)
        if recommendation.value is None:
            state: SyncState = "missing"
            action = f"the team repository has no workflows/{recommendation.name}.prose.md; tell its owners"
        else:
            pinned = f"{recommendation.name}@{recommendation.value}"
            # An entry without a pin runs whatever the file says, so it follows the recommendation too.
            state = (
                "pending" if current is None else "applied" if current in {pinned, recommendation.name} else "differs"
            )
            action = f"adds {pinned} to workflow.schedule_team in your user file, so your daemon runs that content"
        return _item(recommendation, current=current, state=state, action=action)

    def _credential(self, recommendation: Recommendation) -> SyncItem:
        try:
            credentials = self._kernel.resolve(Credentials)
            declared = next((d for d in credentials.declared if d.label == recommendation.name), None)
            stored = declared is not None and credentials.has(declared.let)
        except HeinzelError:  # no credential service, or the keychain can't be read: nothing counts as stored
            declared, stored = None, False
        service, _, name = recommendation.name.partition("/")
        fix = declared.fix if declared is not None else f"hz system credential set {name} --service {service}"
        state: SyncState = "applied" if stored else "by-hand"
        return _item(recommendation, current="stored" if stored else None, state=state, action=fix)

    def _schedule_team(self) -> list[str]:
        settings = dict(self._kernel.settings_of()).get(WORKFLOW)
        entries = getattr(settings, "schedule_team", [])
        return [entry for entry in entries if isinstance(entry, str)]

    def _apply(self, item: SyncItem) -> None:
        """Write one accepted recommendation to the user file.

        Raises:
            HeinzelError: If the configuration refuses the value.
        """
        if item.kind == "binding":
            self._editor.write(f"capabilities.{_key(item.name)}", item.recommended)
        elif item.kind == "contract":
            kept = [e for e in self._schedule_team() if e.partition("@")[0] != item.name]
            self._editor.write(f"{WORKFLOW}.schedule_team", [*kept, f"{item.name}@{item.recommended}"])
        logger.info("Applied the team's recommendation %s", item.id)


def _item(recommendation: Recommendation, *, current: str | None, state: SyncState, action: str) -> SyncItem:
    return SyncItem(
        id=recommendation.id,
        kind=recommendation.kind,
        name=recommendation.name,
        recommended=recommendation.value,
        current=current,
        state=state,
        origin=recommendation.origin,
        action=action,
    )


def _key(capability: str) -> str:
    """The `[capabilities]` key of a capability: dashes become underscores, as in `code_host`."""
    return capability.replace("-", "_")


def _drift(item: SyncItem) -> str:
    recommended = f" → {item.recommended}" if item.kind == "binding" else ""
    have = f"you have {item.current}" if item.current else "you have none"
    return f"your team recommends {item.kind} {item.name}{recommended}; {have} ({item.origin})"


def render_sync(report: SyncReport) -> RenderableType:
    """One row per recommendation: what is recommended, what you have, and where it comes from."""
    if not report.items:
        return Group(*(Text(line) for line in report.next))
    table = Table(title="Your team recommends")
    for column in ("Id", "Recommended", "You have", "State", "From"):
        table.add_column(column, overflow="fold")
    for item in report.items:
        state = f"{item.state} (now)" if item.applied_now else item.state
        cells = (item.id, item.recommended or "-", item.current or "-", state, item.origin)
        table.add_row(*map(Text, cells))
    return Group(table, *(Text(line) for line in report.next))
