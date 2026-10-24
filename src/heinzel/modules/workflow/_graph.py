"""The contract graph: the workflow is not written anywhere; it is the contracts linked by their inputs (ADR 0021).

A contract is an invariant about the asset it maintains, its output. An input that reads another contract's output,
`contract:<namespace>/<name>#<output>`, is an edge from that contract to this one. Edges come only from such explicit
inputs, never from matching prose. The graph is derived afresh from the contracts found, like the daemon's jobs.

Three things are wrong in a graph, and each becomes a diagnostic of the contract with the input: a cycle (`HZC030`,
an error: a contract that depends on itself, even through others, has no first run), an input no contract maintains
(`HZC031`, a warning: it may simply not be written yet), and a team contract reading a personal one (`HZC032`, an
error: a team's contract can't depend on what only you have).
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass

from heinzel.modules.workflow._contracts import Contract
from heinzel.modules.workflow._models import (
    ContractGraph,
    Diagnostic,
    GraphEdge,
    GraphNode,
    Input,
    Severity,
    Step,
    Unmaintained,
)

CODES = frozenset({"HZC030", "HZC031", "HZC032"})
"""The diagnostics only the whole set of contracts shows; `hz workflow check --path` can't find them."""


@dataclass(frozen=True, slots=True)
class Link:
    """One input of one contract and what it reads."""

    contract: Contract
    name: str
    spec: Input

    @property
    def source(self) -> str:
        """The node it reads: an asset's id, or a source's id."""
        if self.spec.upstream is not None:
            return self.spec.read
        args = " ".join(
            f"{k}={json.dumps(v, sort_keys=True, ensure_ascii=False)}" for k, v in sorted(self.spec.args.items())
        )
        return f"{self.spec.read} {args}".rstrip()


def asset(contract: Contract, output: str) -> str:
    """The id of an output a contract maintains, as an input that reads it names it."""
    return f"contract:{contract.namespace}/{contract.name}#{output}"


def _declared(contracts: Iterable[Contract]) -> list[Contract]:
    """Contracts whose frontmatter could be read; only those declare inputs and outputs."""
    return [c for c in contracts if c.frontmatter is not None and c.namespace in {"me", "team"}]


def _links(contracts: list[Contract]) -> list[Link]:
    return [
        Link(contract, name, spec)
        for contract in contracts
        for name, spec in (contract.frontmatter.hz.inputs.items() if contract.frontmatter else ())
    ]


def _maintainers(contracts: list[Contract]) -> dict[str, Contract]:
    return {
        asset(contract, output): contract
        for contract in contracts
        for output in (contract.frontmatter.hz.outputs if contract.frontmatter else {})
    }


def build(contracts: Iterable[Contract]) -> ContractGraph:
    """The graph of the contracts found: nodes, edges, cycles, unmaintained inputs and a Mermaid flowchart."""
    contracts = list(contracts)
    declared = _declared(contracts)
    maintainers = _maintainers(declared)
    links = _links(declared)
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    for contract in declared:
        nodes[contract.qualified] = GraphNode(id=contract.qualified, kind="contract")
    for id_, contract in maintainers.items():
        nodes[id_] = GraphNode(id=id_, kind="asset")
        edges.append(GraphEdge(source=contract.qualified, target=id_, kind="maintains"))
    unmaintained: list[Unmaintained] = []
    for link in links:
        source = link.source
        if link.spec.upstream is None:
            nodes.setdefault(source, GraphNode(id=source, kind="source"))
        elif source not in maintainers:
            nodes.setdefault(source, GraphNode(id=source, kind="asset", maintained=False))
            unmaintained.append(
                Unmaintained(contract=link.contract.qualified, input=link.name, asset=source, why=_why(link, contracts))
            )
        edges.append(GraphEdge(source=source, target=link.contract.qualified, kind="reads", input=link.name))
    cycles = _cycles(declared, links, maintainers)
    graph = ContractGraph(
        nodes=sorted(nodes.values(), key=lambda n: (n.kind, n.id)),
        edges=edges,
        cycles=cycles,
        unmaintained=unmaintained,
        mermaid="",
        next=_next(cycles, unmaintained, declared),
    )
    return graph.model_copy(update={"mermaid": mermaid(graph)})


def problems(contracts: Iterable[Contract]) -> dict[str, list[Diagnostic]]:
    """The graph's diagnostics, by the qualified name of the contract whose input they are about."""
    contracts = list(contracts)
    declared = _declared(contracts)
    maintainers = _maintainers(declared)
    links = _links(declared)
    found: dict[str, list[Diagnostic]] = {}
    cycles = _cycles(declared, links, maintainers)
    cycle_of = {name: cycle for cycle in cycles for name in cycle}
    for link in links:
        upstream = link.spec.upstream
        if upstream is None:
            continue
        where = ("hz", "inputs", link.name, "read")
        contract = link.contract
        problem: tuple[str, Severity, str, str] | None = None
        # A team contract reading a personal output is wrong whether or not that output exists on this machine, so
        # it comes first: `check` in a team's CI must give the same answer as on a colleague's laptop.
        if contract.namespace == "team" and upstream[0] == "me":
            problem = (
                "HZC032",
                "error",
                f"a team contract can't read {link.source}, a personal contract's output: only you have it",
                "read a team contract's output instead, or make this a contract of your own",
            )
        elif link.source not in maintainers:
            problem = (
                "HZC031",
                "warning",
                f"no contract maintains {link.source}: {_why(link, contracts)}",
                _fix(link, contracts),
            )
        elif (cycle := cycle_of.get(contract.qualified)) and maintainers[link.source].qualified in cycle:
            problem = (
                "HZC030",
                "error",
                f"it is on a cycle of {', '.join(cycle)}: a contract can't depend on itself, even through others",
                "remove one of the inputs on the cycle; `hz workflow graph` shows it",
            )
        if problem is not None:
            code, severity, message, hint = problem
            found.setdefault(contract.qualified, []).append(
                Diagnostic(
                    file=contract.path,
                    line=contract.line_of(*where),
                    code=code,
                    severity=severity,
                    field=".".join(where),
                    message=message,
                    hint=hint,
                )
            )
    return found


def _why(link: Link, contracts: list[Contract]) -> str:
    namespace, name, output = link.spec.upstream or ("", "", "")
    upstream = next((c for c in contracts if c.namespace == namespace and c.name == name), None)
    if upstream is None:
        return f"there is no contract {namespace}:{name}"
    if upstream.frontmatter is None:
        return f"{namespace}:{name} has errors, so it maintains nothing until they are fixed"
    outputs = ", ".join(upstream.frontmatter.hz.outputs)
    return f"{namespace}:{name} has no output {output!r}; it maintains {outputs}"


def _fix(link: Link, contracts: list[Contract]) -> str:
    namespace, name, _ = link.spec.upstream or ("", "", "")
    if any(c.namespace == namespace and c.name == name and c.frontmatter is None for c in contracts):
        return "fix its errors: hz workflow check"
    return f"fix the name, or write the contract: hz workflow new {name}{' --team' if namespace == 'team' else ''}"


def _cycles(declared: list[Contract], links: list[Link], maintainers: dict[str, Contract]) -> list[list[str]]:
    """Contracts that depend on themselves, one list per strongly connected component, in a stable order."""
    upstreams: dict[str, set[str]] = {c.qualified: set() for c in declared}
    for link in links:
        if (maintainer := maintainers.get(link.source)) is not None:
            upstreams[link.contract.qualified].add(maintainer.qualified)
    return [
        component
        for component in _components(upstreams)
        if len(component) > 1 or component[0] in upstreams[component[0]]
    ]


def _components(graph: dict[str, set[str]]) -> list[list[str]]:
    """The strongly connected components: contracts that reach each other; quadratic, fine for a team's contracts."""
    reach = {node: _reachable(graph, node) for node in graph}
    found: list[list[str]] = []
    seen: set[str] = set()
    for node in sorted(graph):
        if node not in seen:
            component = sorted({node} | {other for other in reach[node] if node in reach[other]})
            seen.update(component)
            found.append(component)
    return found


def _reachable(graph: dict[str, set[str]], start: str) -> set[str]:
    """Every node reachable from `start` in one or more steps; iterative, so a long chain can't exhaust the stack."""
    found: set[str] = set()
    todo = list(graph[start])
    while todo:
        node = todo.pop()
        if node not in found:
            found.add(node)
            todo.extend(graph.get(node, ()))
    return found


def _next(cycles: list[list[str]], unmaintained: list[Unmaintained], declared: list[Contract]) -> list[Step]:
    if not declared:
        return [Step(command="hz workflow new my-first-workflow", why="write a starter contract, with comments")]
    steps: list[Step] = []
    if cycles or unmaintained:
        steps.append(Step(command="hz workflow check", why="each problem by file and line, with a hint"))
    steps.append(Step(command="hz workflow graph --mermaid", why="the graph as a Mermaid flowchart to paste"))
    return steps


def mermaid(graph: ContractGraph) -> str:
    """A Mermaid flowchart: contracts as boxes, assets as rounded nodes, sources as flags; problems styled."""
    ids = {node.id: f"n{number}" for number, node in enumerate(graph.nodes)}
    on_cycle = {name for cycle in graph.cycles for name in cycle}
    lines = ["flowchart LR"]
    for node in graph.nodes:
        label = _label(node.id)
        shape = {"contract": f'["{label}"]', "asset": f'("{label}")', "source": f'>"{label}"]'}[node.kind]
        lines.append(f"  {ids[node.id]}{shape}")
    for edge in graph.edges:
        arrow = "-->" if edge.kind == "maintains" else f"-- {_label(edge.input or '')} -->"
        lines.append(f"  {ids[edge.source]} {arrow} {ids[edge.target]}")
    lines += ["  classDef missing stroke-dasharray: 5 5", "  classDef cycle stroke-width: 3px"]
    if missing := [ids[n.id] for n in graph.nodes if not n.maintained]:
        lines.append(f"  class {','.join(missing)} missing")
    if cyclic := [ids[n.id] for n in graph.nodes if n.id in on_cycle]:
        lines.append(f"  class {','.join(cyclic)} cycle")
    return "\n".join(lines) + "\n"


def _label(text: str) -> str:
    """Text safe inside a quoted Mermaid label: quotes and HTML as entity codes, no line breaks."""
    for raw, code in (("#", "#35;"), ("&", "#amp;"), ('"', "#quot;"), ("<", "#lt;"), (">", "#gt;"), ("`", "#96;")):
        text = text.replace(raw, code)
    return " ".join(text.split())
