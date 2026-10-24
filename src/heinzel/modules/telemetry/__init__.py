"""Telemetry, named after OpenTelemetry: where Heinzelmännchen log what they did, as `hz report` (ADR 0053).

A Heinzelmännchen reports each action the team should see, one command per verb: what it `read` (and by when its
contract needs it), what it `drafted`, who `approved` it, where it `published`, when it `fell-back` on a stale input,
and a `heartbeat` that it is alive. Each one appends to its own append-only log in your state directory; `hz report
sync` copies the logs into your team repository's checkout, where every member's `hz graph` reads them. Entries are
metadata, validated strictly: never a draft, a message body, a token, or an address other than a publish target.

The module is `report`, for what a Heinzelmännchen does with it; it provides the `TelemetryLog` that `graph` reads.
Every operation writes, so they are CLI and MCP only: a web page can reach a local REST server (ADR 0019).
"""

from datetime import datetime
from typing import Annotated

from pydantic import Field

from heinzel import Module, TelemetryLog
from heinzel.modules.telemetry._models import Reported, Synced, SyncedLog, TelemetrySettings
from heinzel.modules.telemetry._render import render_reported, render_synced
from heinzel.modules.telemetry._service import TeamTelemetry, TelemetryService

module = Module(
    "report",
    "Telemetry: log what a Heinzelmännchen did, for your team's graph (`hz report published <id> <url>`).",
)
module.settings(TelemetrySettings)
module.service(TelemetryService, TelemetryService.for_user)
module.service(TelemetryLog, TeamTelemetry)

HEADS = frozenset({"cli", "mcp"})
Heinzel = Annotated[
    str, Field(description="The Heinzelmännchen's id, a slug unique in your team, e.g. costas-tracker.")
]
Target = Annotated[
    str,
    Field(
        description="What it acted on: an https URL, an email address, contract:team/<name>#<output> or chat:<channel>."
    ),
]
Description = Annotated[
    str, Field(description="One line a reader of the team's graph should know; never content or addresses.")
]
Contract = Annotated[str | None, Field(description="The team contract it keeps, team:<name>.")]
Part = Annotated[str | None, Field(description="The part of the target it concerns, e.g. its row in a tracker.")]
User = Annotated[str | None, Field(description="Your roster handle, when `[report] user` doesn't say.")]
At = Annotated[
    datetime | None,
    Field(description="When it happened, ISO 8601 with offset, within the last 30 days; now by default."),
]
Version = Annotated[str | None, Field(description="The artifact's version, as its source names it.")]


def _report(  # noqa: PLR0913 - one entry's fields
    service: TelemetryService,
    verb: str,
    heinzel: str,
    target: str | None,
    *,
    description: str,
    contract: str | None,
    part: str | None,
    user: str | None,
    at: datetime | None,
    **specific: object,
) -> Reported:
    fields = {
        "heinzel_id": heinzel,
        "verb": verb,
        "target": target,
        "description": description,
        "contract": contract,
        "part": part,
        **specific,
    }
    return service.report({k: v for k, v in fields.items() if v is not None}, user=user, at=at)


@module.operation(read_only=False, render=render_reported, heads=HEADS, audience="everyday")
def read(  # noqa: PLR0913, PLR0917 - one entry's fields
    service: TelemetryService,
    heinzel: Heinzel,
    target: Target,
    version: Version = None,
    needed_by: Annotated[
        datetime | None, Field(description="By when the reader's contract needs it, ISO 8601 with offset.")
    ] = None,
    description: Description = "",
    contract: Contract = None,
    part: Part = None,
    user: User = None,
    at: At = None,
) -> Reported:
    """Log that a Heinzelmännchen read an artifact, which version, and by when its contract needs it."""
    return _report(
        service, "read", heinzel, target, description=description, contract=contract, part=part, user=user, at=at,
        version=version, needed_by=needed_by,
    )  # fmt: skip


@module.operation(read_only=False, render=render_reported, heads=HEADS, audience="everyday")
def drafted(  # noqa: PLR0913, PLR0917 - one entry's fields
    service: TelemetryService,
    heinzel: Heinzel,
    target: Target,
    contract_version: Annotated[str | None, Field(description="The contract's version it drafted from.")] = None,
    description: Description = "",
    contract: Contract = None,
    part: Part = None,
    user: User = None,
    at: At = None,
) -> Reported:
    """Log that a Heinzelmännchen put a draft where it will be approved, and from which contract version."""
    return _report(
        service, "drafted", heinzel, target, description=description, contract=contract, part=part, user=user, at=at,
        contract_version=contract_version,
    )  # fmt: skip


@module.operation(read_only=False, render=render_reported, heads=HEADS, audience="everyday")
def approved(  # noqa: PLR0913, PLR0917 - one entry's fields
    service: TelemetryService,
    heinzel: Heinzel,
    target: Target,
    by: Annotated[str, Field(description="Who approved it, by roster handle.")],
    description: Description = "",
    contract: Contract = None,
    part: Part = None,
    user: User = None,
    at: At = None,
) -> Reported:
    """Log that a Heinzelmännchen's draft was approved, by whom; the entry's time is when."""
    return _report(
        service, "approved", heinzel, target, description=description, contract=contract, part=part, user=user, at=at,
        by=by,
    )  # fmt: skip


@module.operation(read_only=False, render=render_reported, heads=HEADS, audience="everyday")
def published(  # noqa: PLR0913, PLR0917 - one entry's fields
    service: TelemetryService,
    heinzel: Heinzel,
    target: Target,
    version: Version = None,
    description: Description = "",
    contract: Contract = None,
    part: Part = None,
    user: User = None,
    at: At = None,
) -> Reported:
    """Log where a Heinzelmännchen published: a document, an email address, a chat channel."""
    return _report(
        service, "published", heinzel, target, description=description, contract=contract, part=part, user=user, at=at,
        version=version,
    )  # fmt: skip


@module.operation(read_only=False, render=render_reported, heads=HEADS, audience="everyday")
def fell_back(  # noqa: PLR0913, PLR0917 - one entry's fields
    service: TelemetryService,
    heinzel: Heinzel,
    target: Target,
    input: Annotated[str, Field(description="The input whose stale version it used, as a target.")],  # noqa: A002 - the entry's field
    description: Description = "",
    contract: Contract = None,
    part: Part = None,
    user: User = None,
    at: At = None,
) -> Reported:
    """Log that a Heinzelmännchen used a stale version of an input to keep its deadline."""
    return _report(
        service, "fell_back", heinzel, target, description=description, contract=contract, part=part, user=user, at=at,
        input=input,
    )  # fmt: skip


@module.operation(read_only=False, render=render_reported, heads=HEADS, audience="everyday")
def heartbeat(
    service: TelemetryService,
    heinzel: Heinzel,
    contract: Contract = None,
    user: User = None,
    at: At = None,
) -> Reported:
    """Log that a Heinzelmännchen is alive; one a day is enough for the team to see it isn't offline."""
    return _report(service, "heartbeat", heinzel, None, description="", contract=contract, part=None, user=user, at=at)


@module.operation(read_only=False, render=render_synced, heads=HEADS)
def sync(service: TelemetryService) -> Synced:
    """Copy your Heinzelmännchen's logs into your team repository's checkout, for you to commit and push."""
    return service.sync()


__all__ = ["Reported", "Synced", "SyncedLog", "TeamTelemetry", "TelemetryService", "TelemetrySettings", "module"]
