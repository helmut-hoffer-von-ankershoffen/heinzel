"""Building the team graph from team contracts' promises and telemetry, deterministically and without a model.

**Nodes.** An artifact per team contract's output (`contract:team/<name>#<output>`), per team artifact a contract
reads, and per target telemetry names that isn't a known artifact's place; a part of an artifact (a tracker's row a
Heinzelmännchen reports with `part`) is its own node with the artifact as `parent`. A Heinzelmännchen per id in the
telemetry; a team contract nobody's Heinzelmännchen reports for gets one named after the contract, so an unkept
promise shows (as offline).

**Edges.** `writes` from a Heinzelmännchen to the artifact (or its part) of the contract it keeps, declared by the
contract and observed when it reported a draft, an approval or a publication there; `reads` from the keeper of every
contract that reads an artifact (declared) and from every Heinzelmännchen that reported reading one (observed).

**Promises.** Each run of a scheduled team contract (a slot of its schedule) promises its artifact by a deadline: the
next run of a contract that reads it, or else its own next run. The entries a keeper reports from that slot until the
next one count for it, and its state is one of five:

- kept (published, or approved when the contract publishes nothing): `on_time` by the deadline, `late` after it, and
  `fell_back` when the keeper also reported using a stale input;
- not kept yet: `late` once the deadline has passed; before it, `offline` when the keeper has been silent longer than
  `[graph] offline_after`, `fell_back` when it reported a stale input, `waiting_for_approval` when it drafted but
  nobody approved yet, else `on_time`, on track.

The state of an artifact is the worst of its keepers'; weeks count promises by their deadline's ISO week; the flow
waits where a contract's current promise isn't kept and some of what it reads isn't updated yet. Nothing here counts
anything per person.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from itertools import pairwise
from urllib.parse import urlsplit

from heinzel import TeamPromise, TelemetryEntry
from heinzel.modules.graph._models import (
    STATES,
    Edge,
    EdgeMetadata,
    Filters,
    Graph,
    GraphMetadata,
    Node,
    NodeMetadata,
    State,
    TeamGraphDocument,
    Waiting,
    Week,
)
from heinzel.modules.graph._schedule import next_run, runs

WRITES = frozenset({"drafted", "approved", "published"})
HEINZEL = "heinzel:"
"""What a Heinzelmännchen's node id starts with, before its id: `heinzel:costas-tracker`."""
type Found = tuple[dict[str, "Node"], dict[tuple[str, str, str], "Edge"], dict[str, str]]
"""The nodes and edges found so far, and each team artifact's place."""


def worst(states: Iterable[State | None]) -> State | None:
    """The worst of `states`, ignoring none; none if all are none."""
    known: list[State] = [s for s in states if s is not None]
    return max(known, key=STATES.index) if known else None


@dataclass(slots=True)
class Keeper:
    """A Heinzelmännchen as the telemetry shows it."""

    id: str
    user: str | None
    contract: str | None = None
    last_seen: datetime | None = None
    entries: list[TelemetryEntry] = field(default_factory=list[TelemetryEntry])

    @property
    def part(self) -> str | None:
        """The part it writes, from its newest write that names one."""
        parts = [e.part for e in self.entries if e.verb in WRITES and e.part]
        return parts[-1] if parts else None


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One run's promise: from its slot to the next, due at its deadline, and how it stands."""

    slot: datetime
    deadline: datetime
    state: State
    kept: bool


@dataclass(slots=True)
class Inputs:
    """Everything the graph is built from, and how it is read."""

    promises: Sequence[TeamPromise]
    entries: Sequence[TelemetryEntry]
    as_of: datetime
    zone: tzinfo
    zone_name: str
    offline_after: timedelta
    weeks: int
    problems: Sequence[str] = ()


class Builder:
    """Builds one graph; a fresh one per call."""

    def __init__(self, inputs: Inputs) -> None:
        """Index the promises and the entries."""
        self.inputs = inputs
        self.promises = {p.contract: p for p in inputs.promises}
        self.by_artifact = {p.writes: p for p in inputs.promises}
        self.keepers: dict[str, Keeper] = {}
        self.problems = list(inputs.problems)
        for entry in sorted(inputs.entries, key=lambda e: (e.time, e.heinzel_id)):
            if entry.time <= inputs.as_of:
                self._add(entry)
        for promise in inputs.promises:
            if not any(k.contract == promise.contract for k in self.keepers.values()):
                node = f"{HEINZEL}{promise.contract}"
                self.keepers[node] = Keeper(node, None, contract=promise.contract)
        self.readers: dict[str, list[TeamPromise]] = defaultdict(list)
        for promise in inputs.promises:
            for read in promise.reads:
                self.readers[read].append(promise)
        self.since = inputs.as_of - timedelta(weeks=inputs.weeks)

    def _add(self, entry: TelemetryEntry) -> None:
        """Count an entry for its Heinzelmännchen: one user and one team contract each, the first ones reported."""
        node = f"{HEINZEL}{entry.heinzel_id}"
        keeper = self.keepers.setdefault(node, Keeper(node, entry.user))
        if entry.user != keeper.user:
            self.problems.append(
                f"{entry.heinzel_id}: an entry of {entry.user} for {keeper.user}'s Heinzelmännchen, skipped"
            )
            return
        if entry.contract in self.promises:
            if keeper.contract not in {None, entry.contract}:
                self.problems.append(
                    f"{entry.heinzel_id}: keeps {keeper.contract}, so its entry for {entry.contract} is skipped; "
                    "a Heinzelmännchen keeps one contract"
                )
                return
            keeper.contract = entry.contract
        keeper.entries.append(entry)
        keeper.last_seen = entry.time

    # Promises --------------------------------------------------------------------------------------------------------

    def deadline(self, promise: TeamPromise, slot: datetime, following: datetime) -> datetime:
        """The next run of a contract that reads the promise's artifact, else the promise's own next run."""
        reads = [
            next_run(reader.schedule, slot, self.inputs.zone)
            for reader in self.readers.get(promise.writes, [])
            if reader.schedule
        ]
        return min(reads) if reads else following

    def occurrences(self, keeper: Keeper) -> list[Occurrence]:
        """The keeper's promises from `weeks` ago (or its first entry) until now, oldest first; none unscheduled."""
        promise = self.promises.get(keeper.contract or "")
        if promise is None or promise.schedule is None:
            return []
        zone = self.inputs.zone
        slots = runs(promise.schedule, self.since - timedelta(weeks=1), self.inputs.as_of, zone)
        if not slots:
            return []
        runs_ = list(pairwise([*slots, next_run(promise.schedule, slots[-1], zone)]))
        first = keeper.entries[0].time if keeper.entries else None
        # Runs before a Heinzelmännchen's first entry weren't its promises yet: it can't be late for them.
        kept_since = [(s, f) for s, f in runs_ if first is not None and f > first] or runs_[-1:]
        return [self.occurrence(keeper, promise, slot, following) for slot, following in kept_since]

    def occurrence(self, keeper: Keeper, promise: TeamPromise, slot: datetime, following: datetime) -> Occurrence:
        """One run's promise and its state."""
        deadline = self.deadline(promise, slot, following).astimezone(UTC)
        mine = [e for e in keeper.entries if slot <= e.time < following and e.contract in {None, promise.contract}]
        done_verbs = {"published"} if promise.publishes else {"published", "approved"}
        done = next((e for e in mine if e.verb in done_verbs), None)
        stale = any(e.verb == "fell_back" for e in mine)
        now = self.inputs.as_of
        if done is not None:
            state: State = "late" if done.time > deadline else "fell_back" if stale else "on_time"
            return Occurrence(slot, deadline, state, kept=True)
        if now > deadline:
            return Occurrence(slot, deadline, "late", kept=False)
        if keeper.last_seen is None or now - keeper.last_seen > self.inputs.offline_after:
            state = "offline"
        elif stale:
            state = "fell_back"
        elif any(e.verb == "drafted" for e in mine) and not any(e.verb == "approved" for e in mine):
            state = "waiting_for_approval"
        else:
            state = "on_time"
        return Occurrence(slot, deadline, state, kept=False)

    # The graph -------------------------------------------------------------------------------------------------------

    def build(self) -> TeamGraphDocument:
        """The whole graph."""
        nodes: dict[str, Node] = {}
        edges: dict[tuple[str, str, str], Edge] = {}
        current: dict[str, Occurrence | None] = {}
        history: list[Occurrence] = []
        urls = self._urls()
        for keeper in self.keepers.values():
            occurrences = self.occurrences(keeper)
            history += occurrences
            current[keeper.id] = occurrences[-1] if occurrences else None
        for promise in self.inputs.promises:
            nodes[promise.writes] = self._artifact(promise.writes, promise.title, urls, promise=promise)
            for read in promise.reads:
                nodes.setdefault(read, self._artifact(read, _label(read), urls))
        for keeper in self.keepers.values():
            nodes[keeper.id] = self._heinzel(keeper, current[keeper.id])
            self._edges_of(keeper, current[keeper.id], nodes, edges, urls)
        self._states(nodes, edges, current)
        return TeamGraphDocument(
            graph=Graph(
                metadata=GraphMetadata(
                    as_of=self.inputs.as_of,
                    time_zone=self.inputs.zone_name,
                    filters=Filters(artifact=None, heinzel=None, state=None),
                    weeks=self._weeks(history),
                    waiting=(waiting := self._waiting(nodes, current)),
                    summary="; ".join(w.summary for w in waiting) or "Nothing waits",
                    problems=self.problems,
                ),
                nodes=dict(sorted(nodes.items())),
                edges=sorted(edges.values(), key=lambda e: (e.source, e.target, e.relation)),
            )
        )

    def _urls(self) -> dict[str, str]:
        """Each team artifact's place, from the newest write of a keeper that names one: artifact (or part) to URL."""
        found: dict[str, str] = {}
        for keeper in self.keepers.values():
            promise = self.promises.get(keeper.contract or "")
            if promise is None:
                continue
            for entry in keeper.entries:
                if entry.verb in WRITES and entry.target and entry.target.startswith("https://"):
                    node = f"{promise.writes}/{entry.part}" if entry.part else promise.writes
                    found[node] = entry.target  # a place to open: never an address or a channel
        return found

    @staticmethod
    def _resolve(target: str, urls: dict[str, str]) -> str:
        """The artifact a reported target is: the whole team artifact whose (part's) place it is, else itself."""
        if target.startswith("contract:"):
            return target
        for node, url in urls.items():
            if url == target:
                return _whole(node)
        return target

    def _artifact(
        self,
        node: str,
        label: str,
        urls: dict[str, str],
        *,
        promise: TeamPromise | None = None,
        parent: str | None = None,
    ) -> Node:
        return Node(
            label=label,
            metadata=NodeMetadata(
                kind="artifact",
                owner=None,
                state=None,
                contract=promise.contract if promise else None,
                contract_version=promise.version if promise else None,
                deadline=None,
                url=urls.get(node) or (node if node.startswith("https://") else None),
                parent=parent,
                last_seen=None,
            ),
        )

    def _heinzel(self, keeper: Keeper, occurrence: Occurrence | None) -> Node:
        promise = self.promises.get(keeper.contract or "")
        stale = keeper.last_seen is None or self.inputs.as_of - keeper.last_seen > self.inputs.offline_after
        state: State | None = occurrence.state if occurrence else ("offline" if stale else None)
        return Node(
            label=promise.title if promise and keeper.user is None else keeper.id.removeprefix(HEINZEL),
            metadata=NodeMetadata(
                kind="heinzel",
                owner=keeper.user,
                state=state,
                contract=keeper.contract,
                contract_version=promise.version if promise else None,
                deadline=occurrence.deadline if occurrence else None,
                url=None,
                parent=None,
                last_seen=keeper.last_seen,
            ),
        )

    def _edges_of(
        self,
        keeper: Keeper,
        occurrence: Occurrence | None,
        nodes: dict[str, Node],
        edges: dict[tuple[str, str, str], Edge],
        urls: dict[str, str],
    ) -> None:
        promise = self.promises.get(keeper.contract or "")
        if promise is not None:
            target = promise.writes
            if (part := keeper.part) is not None:
                target = f"{promise.writes}/{part}"
                label = f"{promise.title}: {part}"
                nodes.setdefault(target, self._artifact(target, label, urls, promise=promise, parent=promise.writes))
            writes = [e for e in keeper.entries if e.verb in WRITES and e.contract in {None, promise.contract}]
            versions = [e.version for e in writes if e.version]
            edges[keeper.id, target, "writes"] = Edge(
                source=keeper.id,
                target=target,
                relation="writes",
                metadata=EdgeMetadata(
                    state=occurrence.state if occurrence else None,
                    declared=True,
                    observed=bool(writes),
                    last_version=versions[-1] if versions else None,
                    needed_by=None,
                ),
            )
            for read in promise.reads:
                edges[keeper.id, read, "reads"] = _reads(keeper.id, read, declared=True)
        for entry in keeper.entries:
            if entry.verb == "read" and entry.target:
                self._observed_read(keeper, entry.target, entry, (nodes, edges, urls))
            if entry.verb == "fell_back" and entry.input:
                self._observed_read(keeper, entry.input, None, (nodes, edges, urls))
            if entry.verb in WRITES and entry.target and promise is None:
                node = self._resolve(entry.target, urls)
                nodes.setdefault(node, self._artifact(node, _label(node), urls))
                key = (keeper.id, node, "writes")
                version = entry.version or (edges[key].metadata.last_version if key in edges else None)
                edges[key] = Edge(
                    source=keeper.id,
                    target=node,
                    relation="writes",
                    metadata=EdgeMetadata(
                        state=None, declared=False, observed=True, last_version=version, needed_by=None
                    ),
                )

    def _observed_read(self, keeper: Keeper, target: str, entry: TelemetryEntry | None, found: "Found") -> None:
        nodes, edges, urls = found
        node = self._resolve(target, urls)
        nodes.setdefault(node, self._artifact(node, _label(node), urls))
        key = (keeper.id, node, "reads")
        edge = edges.get(key) or _reads(keeper.id, node, declared=False)
        update: dict[str, object] = {"observed": True}
        if entry is not None and entry.version:
            update["last_version"] = entry.version
        if entry is not None and entry.needed_by:
            update["needed_by"] = entry.needed_by
        edges[key] = edge.model_copy(update={"metadata": edge.metadata.model_copy(update=update)})

    def _states(
        self, nodes: dict[str, Node], edges: dict[tuple[str, str, str], Edge], current: dict[str, Occurrence | None]
    ) -> None:
        """Artifacts take the worst state of their keepers, owner and deadline; reads take what they read."""
        writers: dict[str, list[str]] = defaultdict(list)
        for edge in edges.values():
            if edge.relation == "writes" and edge.metadata.declared:
                writers[edge.target].append(edge.source)
        for node_id, sources in writers.items():
            occurrences = [o for s in sources if (o := current.get(s)) is not None]
            owners = {self.keepers[s].user for s in sources}
            self._set(nodes, node_id, sources, occurrences, owners)
        parts: dict[str, list[str]] = defaultdict(list)
        for node_id, node in nodes.items():
            if node.metadata.parent is not None:
                parts[node.metadata.parent].append(node_id)
        for parent, children in parts.items():
            meta = nodes[parent].metadata
            deadlines = [d for c in children if (d := nodes[c].metadata.deadline) is not None]
            nodes[parent] = nodes[parent].model_copy(
                update={
                    "metadata": meta.model_copy(
                        update={
                            "state": worst([meta.state, *(nodes[c].metadata.state for c in children)]),
                            "deadline": min(deadlines) if deadlines else meta.deadline,
                        }
                    )
                }
            )
        for key, edge in edges.items():
            if edge.relation == "reads":
                state = nodes[edge.target].metadata.state
                edges[key] = edge.model_copy(update={"metadata": edge.metadata.model_copy(update={"state": state})})

    @staticmethod
    def _set(
        nodes: dict[str, Node],
        node_id: str,
        sources: list[str],
        occurrences: list[Occurrence],
        owners: set[str | None],
    ) -> None:
        meta = nodes[node_id].metadata
        deadlines = [o.deadline for o in occurrences]
        nodes[node_id] = nodes[node_id].model_copy(
            update={
                "metadata": meta.model_copy(
                    update={
                        "state": worst(o.state for o in occurrences) if occurrences else meta.state,
                        "owner": next(iter(owners)) if len(owners) == 1 and len(sources) == 1 else None,
                        "deadline": min(deadlines) if deadlines else None,
                    }
                )
            }
        )

    def _weeks(self, history: list[Occurrence]) -> list[Week]:
        """Promises decided (kept, or past their deadline) per ISO week of their deadline, the last `weeks` weeks."""
        counts: dict[str, dict[str, int]] = {}
        start = self.inputs.as_of.astimezone(self.inputs.zone)
        for back in range(self.inputs.weeks - 1, -1, -1):
            year, week, _ = (start - timedelta(weeks=back)).isocalendar()
            counts[f"{year}-W{week:02d}"] = {"due": 0, "on_time": 0, "fell_back": 0, "late": 0}
        for occurrence in history:
            if not occurrence.kept and occurrence.deadline > self.inputs.as_of:
                continue
            year, week, _ = occurrence.deadline.astimezone(self.inputs.zone).isocalendar()
            if (row := counts.get(f"{year}-W{week:02d}")) is None:
                continue
            row["due"] += 1
            if occurrence.state in {"on_time", "fell_back", "late"}:
                row[occurrence.state] += 1
        return [Week(week=week, **row) for week, row in counts.items()]

    def _waiting(self, nodes: dict[str, Node], current: dict[str, Occurrence | None]) -> list[Waiting]:
        """Each artifact whose next run reads updates that are due for it and not made yet."""
        found: list[Waiting] = []
        for promise in self.inputs.promises:
            if not promise.reads or promise.schedule is None:
                continue
            upcoming = next_run(promise.schedule, self.inputs.as_of, self.inputs.zone)
            on: list[str] = []
            for read in promise.reads:
                upstream = self.by_artifact.get(read)
                if upstream is None:
                    continue
                for keeper in (k for k in self.keepers.values() if k.contract == upstream.contract):
                    occurrence = current.get(keeper.id)
                    if occurrence is not None and occurrence.deadline <= upcoming and not occurrence.kept:
                        on.append(f"{read}/{keeper.part}" if keeper.part else read)
            if on:
                noun = "update" if len(on) == 1 else "updates"
                summary = f"{nodes[promise.writes].label} waiting on {len(on)} {noun}"
                found.append(Waiting(artifact=promise.writes, summary=summary, on=sorted(on)))
        return found


def _reads(source: str, target: str, *, declared: bool) -> Edge:
    return Edge(
        source=source,
        target=target,
        relation="reads",
        metadata=EdgeMetadata(state=None, declared=declared, observed=False, last_version=None, needed_by=None),
    )


def _whole(node: str) -> str:
    """The artifact a part's node id belongs to (`contract:team/x#row/priya` → `contract:team/x#row`), or the id."""
    name, hash_, rest = node.partition("#")
    return f"{name}{hash_}{rest.partition('/')[0]}" if hash_ and "/" in rest else node


def _label(node: str) -> str:
    """A name for an artifact known only by its target: a contract output's name, a URL's host and path."""
    if node.startswith("contract:"):
        name, _, output = node.removeprefix("contract:").partition("#")
        return f"{name.partition('/')[2]} {output}"
    if node.startswith("https://"):
        parts = urlsplit(node)
        return f"{parts.hostname}{parts.path}".rstrip("/")
    return node


def cut(document: TeamGraphDocument, filters: Filters) -> TeamGraphDocument:
    """The graph around the nodes the filters select: those nodes, their edges, the other ends and their parents."""
    graph = document.graph.model_copy(
        update={"metadata": document.graph.metadata.model_copy(update={"filters": filters})}
    )
    if filters.artifact is None and filters.heinzel is None and filters.state is None:
        return document.model_copy(update={"graph": graph})
    focus = set(graph.nodes)
    if filters.artifact is not None or filters.heinzel is not None:
        focus = {
            node_id
            for node_id, node in graph.nodes.items()
            if node_id in {filters.artifact, filters.heinzel}
            or (filters.artifact is not None and node.metadata.parent == filters.artifact)
        }
    if filters.state is not None:
        focus = {n for n in focus if graph.nodes[n].metadata.state == filters.state}
    edges = [e for e in graph.edges if e.source in focus or e.target in focus]
    keep = focus | {e.source for e in edges} | {e.target for e in edges}
    keep |= {p for n in keep if (p := graph.nodes[n].metadata.parent) is not None}
    nodes = {n: graph.nodes[n] for n in sorted(keep)}
    return document.model_copy(update={"graph": graph.model_copy(update={"nodes": nodes, "edges": edges})})
