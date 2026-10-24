"""What your team recommends, against what you have, and accepting it into your user file (`hz org sync`).

A team repository may recommend plugins, capability bindings, credential names and contracts; the `OrgContext` lists
them with their origin. Recommend-only: `sync` shows each one beside what you have, and applies only what you accept,
by writing your own user configuration through `ConfigEditor`. A binding becomes `[capabilities] <capability>`, a
contract an entry of `[workflow] schedule_team` pinned to the file's SHA-256. Plugins and credentials are never
applied by `sync`: installing code and storing a secret stay explicit commands, which it names.

Your team's layers (ADR 0040) recommend too: the capability bindings their playbooks recommend, as the stack decides
them (most specific first, an undecided org-site conflict left out), join the team repository's, which win for the same
capability. Their other settings are offered by `hz setup`.

`all` and the suggested command take only what you have no choice for yet (`pending`). What you chose otherwise
(`differs`) needs its id, and a contract whose content changed since you pinned it asks you to read it first.
"""

import logging
import shlex
from typing import Literal

from pydantic import BaseModel, Field
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel import Check, ConfigEditor, Credentials, HeinzelError, Kernel, Status
from heinzel.kernel import CapabilityView, LayerStack, OrgContext, Recommendation

logger = logging.getLogger(__name__)
WORKFLOW = "workflow"
SyncState = Literal["applied", "pending", "differs", "by-hand", "missing"]
OPEN: frozenset[str] = frozenset({"pending", "differs"})


class SyncItem(BaseModel):
    """One recommendation beside what you have."""

    id: str = Field(description="How you accept it: `<kind>:<name>`, e.g. `binding:documents`.")
    kind: str = Field(description="plugin, binding, credential or contract.")
    name: str = Field(description="The plugin, capability, credential (`service/name`) or contract.")
    recommended: str | None = Field(description="The adapter or the contract's `sha256:<hex>` recommended.")
    current: str | None = Field(description="What you have: the adapter bound, the contract's entry, or none.")
    state: SyncState = Field(
        description="applied (you have it), pending (accept it), differs (you chose otherwise; accept it by id to "
        "switch), by-hand (run `action` yourself), missing (the repository lacks the contract's file)."
    )
    origin: str = Field(description="Which repository and file recommend it.")
    action: str = Field(description="What accepting it writes, or what to do yourself.")
    applied_now: bool = Field(default=False, description="Whether this run applied it.")


class SyncReport(BaseModel):
    """Your team's recommendations and what became of them."""

    team: bool = Field(description="Whether a team you joined or your layers recommend anything to you.")
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
        joined = context is not None and context.team_checkout() is not None
        found = list(context.recommendations()) if context is not None and joined else []
        found += layer_bindings(self._kernel.layer_stack(), {r.name for r in found if r.kind == "binding"})
        if not joined and not found:
            return SyncReport(team=False, items=[], next=["Join your team first: hz team join <url>"])
        items = [self._item(r) for r in found]
        return SyncReport(team=True, items=items, next=_next(items))

    def sync(self, accept: str | None) -> SyncReport:
        """Apply the recommendations named in `accept` (comma-separated ids, or `all`) to your user configuration.

        `all` takes the pending ones only; one you chose otherwise needs its id.

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
            msg = f"Nothing recommends {', '.join(unknown)} to you; the recommendations: {ids}. See them: hz org sync"
            raise HeinzelError(msg)
        chosen = [
            i
            for i in report.items
            if (i.id in wanted and i.state in OPEN) or ("all" in wanted and i.state == "pending")
        ]
        notes = [_why_not(i) for i in report.items if i.id in wanted and i.state not in OPEN]
        self._apply(chosen)
        for item in chosen:
            item.applied_now = True
            item.current, item.state = item.recommended, "applied"
        report.next = notes + _next(report.items)
        return report

    def health(self) -> list[Check]:
        """One check for the team's recommendations: degraded while some wait for you to accept or install them."""
        try:
            report = self.report()
        except Exception as exc:  # noqa: BLE001 - isolation boundary: drift must not take the profile check down
            return [
                Check(name="recommendations", status=Status.DEGRADED, detail=f"can't compare: {exc}", fix="hz org sync")
            ]
        if not report.team:
            return []
        waiting = [i for i in report.items if i.state in {"pending", "by-hand"}]
        if not waiting:
            detail = f"{len(report.items)} recommended, none waiting"
            return [Check(name="recommendations", status=Status.UP, detail=detail)]
        detail = f"your team or its layers recommend {', '.join(i.id for i in waiting)}, which you don't have yet"
        return [Check(name="recommendations", status=Status.DEGRADED, detail=detail, fix="hz org sync")]

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
                action = f"install it: hz system plugin add {shlex.quote(recommendation.name)}"
                return _item(recommendation, current=None, state=state, action=action)
            case "credential":
                return self._credential(recommendation)

    def _binding(self, recommendation: Recommendation) -> SyncItem:
        current, how = self._view.binding(recommendation.name)
        adapters = {a.let.adapter for a in self._view.adapters if a.let.capability == recommendation.name}
        # Served only automatically, it is no choice of yours yet: a second provider would unbind it (ADR 0055).
        if current == recommendation.value and how != "automatic":
            return _item(recommendation, current=current, state="applied", action="you have it")
        if recommendation.value not in adapters:
            action = (
                f"install the plugin that provides the {recommendation.value} adapter first (see the plugins your "
                f"team recommends), then: hz org sync --accept {recommendation.id}"
            )
            return _item(recommendation, current=current, state="by-hand", action=action)
        # A binding only the core's default or the automatic choice made is no choice of yours: it is pending, like
        # an unbound one (ADR 0055).
        state: SyncState = "pending" if current is None or how in {"default", "automatic"} else "differs"
        action = f"sets capabilities.{_key(recommendation.name)} = {recommendation.value!r} in your user file"
        return _item(recommendation, current=current, state=state, action=action)

    def _contract(self, recommendation: Recommendation) -> SyncItem:
        current = next((e for e in self._schedule_team() if e.partition("@")[0] == recommendation.name), None)
        if recommendation.value is None:
            action = f"the team repository has no workflows/{recommendation.name}.prose.md; tell its owners"
            return _item(recommendation, current=current, state="missing", action=action)
        pinned = f"{recommendation.name}@{recommendation.value}"
        read = f"read it first: hz workflow show team:{recommendation.name}"
        state: SyncState
        if current is None:
            state = "pending"
            action = f"{read}; accepting adds {pinned} to workflow.schedule_team, so your daemon runs that content"
        elif current in {pinned, recommendation.name}:  # an entry without a pin runs whatever the file says
            state, action = "applied", "you have it"
        else:
            state = "differs"
            action = f"its content changed since you pinned it; {read}, then accept contract:{recommendation.name}"
        return _item(recommendation, current=current, state=state, action=action)

    def _credential(self, recommendation: Recommendation) -> SyncItem:
        try:
            credentials = self._kernel.resolve(Credentials)
            declared = next((d for d in credentials.declared if d.label == recommendation.name), None)
            stored = declared is not None and credentials.has(declared.let)
        except HeinzelError as exc:  # no credential service, or the keychain can't be read
            action = f"can't tell whether it is stored ({exc}); hz system doctor says more"
            return _item(recommendation, current=None, state="by-hand", action=action)
        if stored:
            return _item(recommendation, current="stored", state="applied", action="you have it")
        if declared is None:
            action = f"install the plugin that declares {recommendation.name} first; hz system doctor then says how"
        else:
            action = f"store it yourself: {declared.fix}"
        return _item(recommendation, current=None, state="by-hand", action=action)

    def _schedule_team(self) -> list[str]:
        settings = dict(self._kernel.settings_of()).get(WORKFLOW)
        entries = getattr(settings, "schedule_team", [])
        return [entry for entry in entries if isinstance(entry, str)]

    def _apply(self, items: list[SyncItem]) -> None:
        """Write the accepted recommendations to the user file: each binding, and the contracts in one write.

        Raises:
            HeinzelError: If the configuration refuses a value.
        """
        for item in items:
            if item.kind == "binding":
                self._editor.write(f"capabilities.{_key(item.name)}", item.recommended)
                logger.info("Applied the team's recommendation %s", item.id)
        contracts = {item.name: f"{item.name}@{item.recommended}" for item in items if item.kind == "contract"}
        if contracts:
            kept = [e for e in self._schedule_team() if e.partition("@")[0] not in contracts]
            self._editor.write(f"{WORKFLOW}.schedule_team", [*kept, *contracts.values()])
            logger.info("Applied the team's recommendations %s", ", ".join(f"contract:{n}" for n in contracts))


def layer_bindings(stack: LayerStack, taken: set[str]) -> list[Recommendation]:
    """The capability bindings your stack's playbooks recommend, most specific first, as team recommendations.

    A capability in `taken` (the team repository recommends it) is left to the repository's recommendation.
    """
    plugins = {layer.name: layer.distribution for layer in stack.layers}
    found: list[Recommendation] = []
    recommended, _ = stack.recommendations()
    for recommendation in recommended:
        section, _, key = recommendation.key.partition(".")
        capability = key.replace("_", "-")
        if section != "capabilities" or capability in taken or not isinstance(recommendation.value, str):
            continue
        origin = f"layer {recommendation.layer} ({plugins[recommendation.layer]}), its playbook"
        found.append(Recommendation(kind="binding", name=capability, value=recommendation.value, origin=origin))
        taken = taken | {capability}  # the stack offers one value per capability; never two rows for one id
    return found


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


def _next(items: list[SyncItem]) -> list[str]:
    """What to do about the open recommendations: accept the pending ones, decide the others one by one."""
    steps: list[str] = []
    if pending := [i.id for i in items if i.state == "pending"]:
        steps.append(f"Accept them: hz org sync --accept {shlex.quote(','.join(pending))}")
    steps += [f"{i.id}: {i.action}" for i in items if i.state in {"differs", "by-hand", "missing"}]
    return steps or ["You have everything your team and its layers recommend."]


def _why_not(item: SyncItem) -> str:
    if item.state == "applied":
        return f"{item.id}: you have it already"
    return f"{item.id}: not applied by sync; {item.action}"


def _key(capability: str) -> str:
    """The `[capabilities]` key of a capability: dashes become underscores, as in `code_host`."""
    return capability.replace("-", "_")


def render_sync(report: SyncReport) -> RenderableType:
    """One row per recommendation: what is recommended, what you have, and what accepting does."""
    if not report.items:
        return Group(*(Text(line) for line in report.next))
    origins = list(dict.fromkeys(item.origin.split(", ")[0] for item in report.items))
    table = Table(title=f"Recommended by {' and '.join(origins)}")
    for column in ("Id", "Recommended", "You have", "State", "What it does"):
        table.add_column(column, overflow="fold")
    for item in report.items:
        state = f"{item.state} (now)" if item.applied_now else item.state
        cells = (item.id, item.recommended or "-", item.current or "-", state, item.action)
        table.add_row(*map(Text, cells))
    return Group(table, *(Text(line) for line in report.next))
