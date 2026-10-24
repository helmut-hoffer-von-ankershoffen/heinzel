"""The team graph as JSON Graph Format v2 (https://jsongraphformat.info), profile `hz-team`, and its settings.

The shape is a contract with the web app's graph view and with the graph keeper's contract: `hz graph --json` emits
exactly this, `docs/schemas/team-graph-1.json` is generated from these models, and a test holds the fixture
`tests/fixtures/graph/weekly-report.jgf.json` (copied to `web/src/test/fixtures/graph.jgf.json`) to it.
"""

from datetime import datetime
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from heinzel import ModuleSettings
from heinzel.kernel import parse_duration

State = Literal["on_time", "waiting_for_approval", "late", "fell_back", "offline"]
STATES: tuple[State, ...] = ("on_time", "waiting_for_approval", "fell_back", "offline", "late")
"""Every state, from best to worst; an artifact kept by several Heinzelmännchen shows the worst of theirs."""
NodeKind = Literal["artifact", "heinzel"]
Relation = Literal["reads", "writes"]


class GraphSettings(ModuleSettings):
    """The team graph: the `[graph]` table or `HZ_GRAPH__*` variables."""

    section_title: ClassVar[str | None] = "Team graph"  # the heading in Settings

    time_zone: str | None = Field(
        default=None,
        title="Schedule time zone",
        description="The time zone your team's contract schedules are read in; your computer's when not set.",
        json_schema_extra={"format": "iana-timezone"},
    )
    offline_after: str = Field(
        default="2d",
        title="Offline after",
        description="How long a Heinzelmännchen may stay silent, without even a heartbeat, before it shows as offline.",
        examples=["36h"],
        json_schema_extra={"format": "duration"},
    )
    weeks: int = Field(
        default=4,
        ge=1,
        le=26,
        title="Weeks counted",
        description="How many weeks of kept promises the team graph counts.",
        json_schema_extra={"x-unit": "weeks"},
    )

    @field_validator("offline_after")
    @classmethod
    def _duration(cls, value: str) -> str:
        parse_duration(value)
        return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeMetadata(_Strict):
    """What the team sees of an artifact or a Heinzelmännchen: never content, descriptions or per-person counts."""

    kind: NodeKind = Field(description="`artifact` (a thing the team keeps or reads) or `heinzel` (who keeps it).")
    owner: str | None = Field(
        description="The roster handle of the user whose Heinzelmännchen keeps it, or who it belongs to; none when "
        "several keep it or nobody does."
    )
    state: State | None = Field(
        description="The current promise's state: on_time, waiting_for_approval, late, fell_back or offline; none "
        "when nothing is promised (no schedule, or an artifact nobody on the team keeps)."
    )
    contract: str | None = Field(description="The team contract that keeps it, or that it keeps: `team:<name>`.")
    contract_version: str | None = Field(description="That contract's version: its `version`, else `sha256:<hex>`.")
    deadline: datetime | None = Field(
        description="When the current promise is due: the next run of a contract that reads it, else its own next run."
    )
    url: str | None = Field(description="Where the artifact lives, as last reported; none if never reported.")
    parent: str | None = Field(
        description="For a part of an artifact (a tracker's row): the containing artifact's node id."
    )
    last_seen: datetime | None = Field(description="For a Heinzelmännchen: its newest entry, a heartbeat included.")


class Node(_Strict):
    """A JGF node: an artifact or a Heinzelmännchen."""

    label: str = Field(description="What to call it.")
    metadata: NodeMetadata = Field(description="What the team sees of it.")


class EdgeMetadata(_Strict):
    """Whether a read or write is promised, happened, and how the promise behind it stands."""

    state: State | None = Field(
        description="For `writes`: the writer's current promise; for `reads`: the state of what is read."
    )
    declared: bool = Field(description="A team contract says so.")
    observed: bool = Field(description="A Heinzelmännchen reported it.")
    last_version: str | None = Field(description="The artifact's version last read or published, as reported.")
    needed_by: datetime | None = Field(description="For `reads`: by when the reader said it needs it, as reported.")


class Edge(_Strict):
    """A JGF edge from a Heinzelmännchen to an artifact: it reads it or writes it."""

    source: str = Field(description="The Heinzelmännchen's node id, `heinzel:<id>`: who reads or writes.")
    target: str = Field(description="The artifact's node id.")
    relation: Relation = Field(description="`reads` or `writes`.")
    metadata: EdgeMetadata = Field(description="How the promise behind it stands.")


class Week(_Strict):
    """Promises due in one ISO week, by how they ended: the team's flow, never anyone's performance."""

    week: str = Field(description="The ISO week, e.g. 2026-W43.")
    due: int = Field(description="Promises whose deadline fell in the week and has passed, or that were kept.")
    on_time: int = Field(description="Kept by the deadline.")
    fell_back: int = Field(description="Kept by the deadline on a stale input.")
    late: int = Field(description="Kept after the deadline, or not at all.")


class Waiting(_Strict):
    """Where the flow waits now: an artifact whose current promise isn't kept yet, and what it waits on."""

    artifact: str = Field(description="The artifact's node id.")
    summary: str = Field(description="One line, e.g. `Weekly report waiting on 2 updates`.")
    on: list[str] = Field(description="The node ids of the inputs (or their parts) not updated yet.")


class Filters(_Strict):
    """The filters the graph was cut with; none means everything."""

    artifact: str | None = Field(description="Only this artifact and what is around it.")
    heinzel: str | None = Field(description="Only this Heinzelmännchen and what is around it.")
    state: State | None = Field(description="Only nodes in this state.")


class GraphMetadata(_Strict):
    """When the graph is as of, how it was cut, the weeks, where the flow waits and what was skipped."""

    as_of: datetime = Field(description="When the graph is as of, in UTC.")
    time_zone: str = Field(description="The zone schedules were read in.")
    filters: Filters = Field(description="How the graph was cut.")
    weeks: list[Week] = Field(description="Promises kept per week, oldest first.")
    waiting: list[Waiting] = Field(description="Where the flow waits now.")
    summary: str = Field(
        description="The flow in one line, e.g. `Weekly report waiting on 2 updates`, or `Nothing waits`."
    )
    problems: list[str] = Field(description="Telemetry lines that were skipped, and why.")


class Graph(_Strict):
    """A JGF v2 graph of profile `hz-team`."""

    directed: Literal[True] = Field(default=True, description="Edges go from a Heinzelmännchen to an artifact.")
    type: Literal["hz-team"] = Field(default="hz-team", description="The JGF profile: `hz-team`.")
    label: str = Field(default="Team graph", description="What the graph is.")
    metadata: GraphMetadata = Field(description="When, how it was cut, the weeks and where the flow waits.")
    nodes: dict[str, Node] = Field(description="Every node by its id.")
    edges: list[Edge] = Field(description="Every edge, sorted by source, target and relation.")


class TeamGraphDocument(_Strict):
    """A JSON Graph Format v2 document holding one graph: `{"graph": {...}}`."""

    schema_url: ClassVar[str] = "https://jsongraphformat.info/v2.1/json-graph-schema.json"
    graph: Graph = Field(description="The team graph.")
