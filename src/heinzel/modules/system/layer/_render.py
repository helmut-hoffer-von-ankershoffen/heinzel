"""How the CLI head presents layers and stacks: one line per layer, so long path names never fold."""

import json

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from heinzel.modules.system.layer._models import LayerList, LayerView, StackView


def _layer(layer: LayerView) -> Text:
    """`acme-payments-checkout (team) under acme-payments, at acme-lisbon: brings ...; plugin ...`."""
    line = Text(f"  {layer.name}", style="bold")
    line.append(f" ({layer.label})")
    where = [f"under {layer.parent}"] if layer.parent else []
    if layer.site:
        where.append(f"at {layer.site}")
    if where:
        line.append(" " + ", ".join(where))
    brings = [*layer.adapters, *(f"role {role}" for role in layer.roles)]
    if layer.playbook:
        brings.append(f"playbook: {layer.playbook}")
    if brings:
        line.append(f"; brings {', '.join(brings)}")
    line.append(f"; plugin {layer.plugin}")
    if layer.hint:
        line.append(f"\n    hint: {layer.hint}", style="yellow")
    return line


def _next(steps: list[str]) -> list[RenderableType]:
    return [Text("Next:\n" + "\n".join(f"  {step}" for step in steps))] if steps else []


def render_list(listing: LayerList) -> RenderableType:
    """The installed layers, then what is wrong with them."""
    parts: list[RenderableType] = [Text("Layers:" if listing.layers else "No layer is installed.")]
    parts += [_layer(layer) for layer in listing.layers]
    parts += [Text(f"{p.layer}: {p.detail}. Fix: {p.fix}", style="yellow") for p in listing.problems]
    return Group(*parts, *_next(listing.next))


def render_stack(view: StackView) -> RenderableType:
    """The stack in one line, its layers, the recommendations most specific first, conflicts and problems."""
    parts: list[RenderableType] = []
    if view.layers:
        parts.append(Text(f"Your stack ({view.source}): {view.stack}"))
        parts += [_layer(layer) for layer in view.layers]
    else:
        parts.append(Text("You are in no layer."))
    if view.roles:
        parts.append(Text(f"Roles the layers add: {', '.join(view.roles)}"))
    if view.recommendations:
        table = Table(title="Recommended settings, most specific first; each applies only when you run its command")
        for column in ("Setting", "Value", "From", "Instead of"):
            table.add_column(column, overflow="fold")
        for r in view.recommendations:
            instead = "\n".join(f"{layer}: {json.dumps(value)}" for layer, value in r.alternatives.items())
            table.add_row(Text(r.key), Text(json.dumps(r.value)), Text(r.layer), Text(instead or "-"))
        parts.append(table)
    for conflict in view.conflicts:
        said = ", ".join(f"{layer} says {json.dumps(value)}" for layer, value in conflict.values.items())
        parts.append(Text(f"Undecided: {conflict.key} ({said}). {conflict.fix}", style="yellow"))
    parts += [
        Text(f"{p.layer + ': ' if p.layer else ''}{p.detail}. Fix: {p.fix}", style="yellow") for p in view.problems
    ]
    return Group(*parts, *_next(view.next))
