"""The team graph: are the team's promises kept? Built from team contracts and telemetry, never from a model (ADR 0054).

Nodes are artifacts and Heinzelmännchen; edges are `reads` and `writes`, declared by team contracts and observed in
what Heinzelmännchen report (`hz report`). Each run of a scheduled team contract promises its artifact by a deadline,
the next run of a contract that reads it, and stands in one of five states: on_time, waiting_for_approval, late,
fell_back or offline. The graph shows the team's flow (promises kept per week, where it waits now: "Weekly report
waiting on 2 updates"), never anyone's performance, and only the team layer: your sessions, lessons, amendments and
edits stay yours.

`hz graph --json` is JSON Graph Format v2, profile `hz-team` (`docs/schemas/team-graph-1.json`). It reads your team's
data, so it is on the CLI, MCP and the web app, not the unauthenticated REST head. The module also serves the
`team-graph` capability (adapter `hz-graph`), and its playbook's template, the team graph keeper, is a contract that
reads it and nudges only its own user: the keeper is a Heinzelmännchen like any other, not code here.
"""

from datetime import datetime
from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import Module
from heinzel.modules.graph._models import GraphSettings, State, TeamGraphDocument
from heinzel.modules.graph._render import render_graph
from heinzel.modules.graph._service import GraphService, HzGraph

module = Module("graph", "Are your team's promises kept? Artifacts and Heinzelmännchen, and where the flow waits.")
module.settings(GraphSettings)
module.service(GraphService)
module.provides("team-graph", modes={"read"}, name="hz-graph")(HzGraph)
module.playbook(
    "Keep the team graph in view: a morning note to you alone on where the flow waits and what changed.",
    templates=[Path(__file__).parent / "templates"],
    setup=False,
)


@module.operation(render=render_graph, heads={"cli", "mcp", "web"}, audience="everyday")
def graph(
    service: GraphService,
    artifact: Annotated[
        str | None, Field(description="Only this artifact, its parts and what reads or writes them, by node id.")
    ] = None,
    heinzel: Annotated[
        str | None,
        Field(description="Only this Heinzelmännchen and what it reads and writes, by its id; with --artifact, both."),
    ] = None,
    state: Annotated[
        State | None,
        Field(description="Only nodes in this state: on_time, waiting_for_approval, late, fell_back or offline."),
    ] = None,
    at: Annotated[
        datetime | None, Field(description="The graph as of this time, ISO 8601 with offset; now by default.")
    ] = None,
) -> TeamGraphDocument:
    """Show your team's promises: who keeps which artifact, how each promise stands, where the flow waits."""
    return service.graph(artifact=artifact, heinzel=heinzel, state=state, at=at)


__all__ = ["GraphService", "GraphSettings", "HzGraph", "TeamGraphDocument", "module"]
