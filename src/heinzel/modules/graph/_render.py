"""How the CLI head presents `hz graph`: where the flow waits, the weeks, then each node with its edges.

Labels, ids and targets come from team contracts and telemetry others write, so they are shown as text, never markup.
"""

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel.modules.graph._models import TeamGraphDocument


def render_graph(document: TeamGraphDocument) -> RenderableType:
    """The waiting list, the kept-on-time table and the nodes, each with its state, deadline and edges."""
    graph = document.graph
    meta = graph.metadata
    parts: list[RenderableType] = [Text(f"Team graph as of {meta.as_of.isoformat()} ({meta.time_zone})", style="bold")]
    parts += [Text(f"  {w.summary}", style="yellow") for w in meta.waiting] or [Text("  Nothing waits.")]
    weeks = Table("Week", "Due", "On time", "Fell back", "Late", title="Promises kept", title_justify="left")
    for week in meta.weeks:
        weeks.add_row(week.week, str(week.due), str(week.on_time), str(week.fell_back), str(week.late))
    parts.append(weeks)
    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        how = "+".join(k for k, v in (("declared", edge.metadata.declared), ("observed", edge.metadata.observed)) if v)
        outgoing.setdefault(edge.source, []).append(f"{edge.relation} {edge.target} ({how})")
    for node_id, node in graph.nodes.items():
        meta_node = node.metadata
        state = meta_node.state or "-"
        due = f", due {meta_node.deadline.isoformat()}" if meta_node.deadline else ""
        owner = f", {meta_node.owner}" if meta_node.owner else ""
        parts.append(Text(f"{meta_node.kind:8} {state:20} {node_id}  {node.label}{owner}{due}"))
        parts += [Text(f"         → {line}", style="dim") for line in outgoing.get(node_id, [])]
    parts += [Text(f"skipped: {problem}", style="yellow") for problem in meta.problems]
    if not graph.nodes:
        parts.append(Text("No team contracts or telemetry yet: hz team join <url>, then hz report heartbeat <id>"))
    return Group(*parts)
