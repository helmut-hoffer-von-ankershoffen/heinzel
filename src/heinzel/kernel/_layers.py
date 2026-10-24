"""Layers: a plugin may sit in a company's tree, under a parent layer and at a site layer (ADR 0040).

A layer is an ordinary plugin whose module declares `module.layer(parent=..., site=...)`. Layers are untyped: the
words company, org, site and team are labels derived from a layer's position (the root; an inner node; a layer named
as a site; a leaf), never kinds the kernel enforces. The kernel checks the tree when it registers modules (installed
parent and site, no cycle, one parent per layer, a site outside the layer's own line) and stacks the layers a person
is in general to specific: the company, each org down the parent chain, the site, then the team.

What a layer brings is what any plugin brings: adapters, roles and a playbook (ADR 0037). Stacking orders their
recommended settings most specific first; nothing is applied until the person accepts it. Where an org and the site
recommend different values for one key, `ORG_SITE_PRECEDENCE` decides: the open question of ADR 0040, answered for now
by `team_decides`.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from heinzel.kernel._lets import NAME_PATTERN, Let
from heinzel.kernel._playbooks import PlaybookLet

DISTRIBUTION_PREFIX = "heinzel-"
"""Plugin distributions are `heinzel-<name>` (ADR 0025): the plugin to install for a missing layer."""


class Label(StrEnum):
    """Where a layer sits in its tree, derived from its position; layers have no kind."""

    COMPANY = "company"
    """The root of a tree: a layer without a parent."""
    ORG = "org"
    """A layer with a parent and layers below it."""
    SITE = "site"
    """A layer some other layer names as its site: a location, orthogonal to the org tree."""
    TEAM = "team"
    """A layer with a parent and nothing below it."""


@dataclass(frozen=True, kw_only=True, slots=True)
class LayerLet(Let):
    """The module is a layer: it names its parent layer and, optionally, a site layer (ADR 0040). One per module.

    Named after its module. `parent` and `site` name other layers by their module names, e.g. `acme-payments` and
    `acme-lisbon`; the root (the company) has no parent. `roles` extends the core role vocabulary for everyone whose
    stack includes this layer.
    """

    parent: str | None = None
    site: str | None = None
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Refuse names that aren't module names, a layer that is its own parent or site, and a site that is the parent.

        Raises:
            ValueError: If a name is malformed or the layer refers to itself, or the site is the parent.
        """
        for what, value in (("parent", self.parent), ("site", self.site)):
            if value is not None and not NAME_PATTERN.fullmatch(value):
                msg = f"Layer {self.name!r}: {what} {value!r} must be a module name matching {NAME_PATTERN.pattern}"
                raise ValueError(msg)
            if value == self.name:
                msg = f"Layer {self.name!r} can't be its own {what}"
                raise ValueError(msg)
        if self.site is not None and self.site == self.parent:
            msg = (
                f"Layer {self.name!r}: {self.site!r} can't be both its parent and its site; a site is orthogonal to "
                "the org tree, so name the org as parent and the location as site"
            )
            raise ValueError(msg)
        roles = tuple(self.roles)
        if bad := [role for role in roles if not NAME_PATTERN.fullmatch(role)]:
            msg = f"Layer {self.name!r}: role {bad[0]!r} must be lowercase words joined by dashes"
            raise ValueError(msg)
        if duplicates := sorted({role for role in roles if roles.count(role) > 1}):
            msg = f"Layer {self.name!r} names the role {duplicates[0]!r} twice"
            raise ValueError(msg)
        object.__setattr__(self, "roles", roles)


def naming_hint(name: str, parent: str | None) -> str | None:
    """How `name` would follow the path from the root, or None when it does: a hint, never enforced.

    A layer's name is its path joined with dashes (`acme`, `acme-payments`, `acme-payments-checkout`, `acme-lisbon`),
    so the name alone says where a plugin sits.
    """
    if parent is None or name.startswith(f"{parent}-"):
        return None
    return (
        f"{name} sits under {parent}; named by its path it would be {parent}-<name> "
        f"(plugin heinzel-{parent}-<name>), so its name says where it sits"
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class Layer:
    """An installed layer, where it sits and what it brings."""

    name: str
    """The module name, e.g. `acme-payments-checkout`."""
    module: str
    """The qualified name of the module that declares it."""
    distribution: str
    """The plugin it comes with, e.g. `heinzel-acme-payments-checkout`."""
    parent: str | None
    site: str | None
    label: Label
    roles: tuple[str, ...] = ()
    adapters: tuple[str, ...] = ()
    """The adapters its plugin brings, `<adapter>:<capability>`."""
    playbook: PlaybookLet | None = None


class LayerProblemKind(StrEnum):
    """What can be wrong with a layer tree or a person's stack, each with its own fix."""

    MISSING_PARENT = "missing-parent"
    """Its parent layer's plugin isn't installed."""
    MISSING_SITE = "missing-site"
    """Its site layer's plugin isn't installed."""
    PARENT_LEFT_OUT = "parent-left-out"
    """Its parent is installed but was left out of the tree for a problem of its own."""
    CYCLE = "cycle"
    """Its parents go round in a circle; every layer on it is left out."""
    DECLARED_TWICE = "declared-twice"
    """Two modules declare a layer of one name, so it would have two parents; both are left out."""
    NOT_A_PLUGIN = "not-a-plugin"
    """A submodule or a built-in module declares a layer; a layer is a plugin's top-level module."""
    SITE_IN_LINE = "site-in-line"
    """Its site is one of its parents or below it; a site sits beside the org tree."""
    NOT_INSTALLED = "not-installed"
    """The person's declared layer isn't installed."""
    AMBIGUOUS = "ambiguous"
    """Nothing declares the person's layer and several could be theirs."""
    UNREADABLE = "unreadable"
    """What declares the person's layer couldn't be read."""


@dataclass(frozen=True, kw_only=True, slots=True)
class LayerProblem:
    """Something wrong with an installed layer or with a person's stack, and what fixes it."""

    layer: str
    """The layer it is about; empty when it is about no single layer."""
    problem: LayerProblemKind
    detail: str
    fix: str
    """A command or step that fixes it."""


def plugin_of(layer: str) -> str:
    """The plugin a layer comes with by convention: `heinzel-<layer>` (ADR 0025)."""
    return f"{DISTRIBUTION_PREFIX}{layer}"


@dataclass(frozen=True, kw_only=True, slots=True)
class Declared:
    """A layer as a module declares it, before the tree is checked."""

    let: LayerLet
    module: str
    """The qualified name of the declaring module."""
    distribution: str
    adapters: tuple[str, ...] = ()
    playbook: PlaybookLet | None = None
    plugin: bool = True
    """Whether the module is a plugin's top-level module, the only kind of module that can be a layer."""


class Layers:
    """Every installed layer, checked: refused declarations are left out and reported, never fatal."""

    def __init__(self, declared: Iterable[Declared]) -> None:
        """Check the declarations and build the tree.

        A layer two modules declare would have two parents: both are left out, and so are a submodule's or a built-in
        module's layer and every layer on a cycle. A missing parent or site keeps the layer, without what is missing.
        """
        self.problems: list[LayerProblem] = []
        self._dropped: dict[str, LayerProblem] = {}
        """Layers left out, by layer name and by plugin name, with why."""
        by_name: dict[str, list[Declared]] = {}
        for entry in declared:
            if entry.plugin:
                by_name.setdefault(entry.let.name, []).append(entry)
            else:
                self._drop(
                    (entry.let.name,),
                    LayerProblemKind.NOT_A_PLUGIN,
                    f"{entry.module} ({entry.distribution}) declares it, but a layer is a plugin's top-level module",
                    f"report it to the authors of {entry.distribution}: declare the layer on the plugin's root module",
                )
        kept: dict[str, Declared] = {}
        for name, entries in by_name.items():
            if len(entries) == 1:
                kept[name] = entries[0]
                continue
            modules = " and ".join(f"{e.module} ({e.distribution})" for e in entries)
            parents = ", ".join(sorted({e.let.parent or "none" for e in entries}))
            plugins = sorted({e.distribution for e in entries})
            self._drop(
                (name, *plugins),
                LayerProblemKind.DECLARED_TWICE,
                f"{modules} both declare it (parents: {parents}); a layer has one parent",
                "keep one of them: " + " or ".join(f"hz system plugin remove {p}" for p in plugins),
            )
        for cycle in _cycles({n: e.let.parent for n, e in kept.items()}):
            path = " → ".join([*cycle, cycle[0]])
            for name in cycle:
                self._drop(
                    (name, kept[name].distribution),
                    LayerProblemKind.CYCLE,
                    f"its parents go round in a circle: {path}",
                    f"one of {', '.join(cycle)} must name another parent; report it to the plugins' authors",
                )
                kept.pop(name, None)
        self._parents = {name: entry.let.parent for name, entry in kept.items()}
        sites: dict[str, str | None] = {}
        for name, entry in kept.items():
            sites[name] = self._check(name, entry, kept)
        named_sites = {site for site in sites.values() if site is not None}
        self._children = {parent for parent in self._parents.values() if parent is not None}
        self._layers: dict[str, Layer] = {
            name: Layer(
                name=name,
                module=entry.module,
                distribution=entry.distribution,
                parent=entry.let.parent if entry.let.parent in kept else None,
                site=sites[name],
                label=_label(name, entry.let.parent, named_sites, self._children),
                roles=entry.let.roles,
                adapters=entry.adapters,
                playbook=entry.playbook,
            )
            for name, entry in sorted(kept.items())
        }
        self.hints: dict[str, str] = {
            name: hint for name, layer in self._layers.items() if (hint := naming_hint(name, layer.parent))
        }

    def _drop(self, names: tuple[str, ...], kind: LayerProblemKind, detail: str, fix: str) -> None:
        """Report a layer left out, findable by its name and its plugins' names."""
        problem = LayerProblem(layer=names[0], problem=kind, detail=detail, fix=fix)
        self.problems.append(problem)
        for name in names:
            self._dropped.setdefault(name, problem)

    def _check(self, name: str, entry: Declared, kept: Mapping[str, Declared]) -> str | None:
        """Report what is wrong with the layer's parent and site; the site it keeps, or None."""
        parent, site = entry.let.parent, entry.let.site
        if parent is not None and parent not in kept:
            self.problems.append(self._unreachable(name, parent, "parent"))
        if site is None:
            return None
        if site not in kept:
            self.problems.append(self._unreachable(name, site, "site"))
            return None
        if site in self._ancestors(name) or name in self._ancestors(site):
            where = "one of its parents" if site in self._ancestors(name) else "below it"
            self.problems.append(
                LayerProblem(
                    layer=name,
                    problem=LayerProblemKind.SITE_IN_LINE,
                    detail=f"its site {site} is {where}; a site sits beside the org tree, under the company",
                    fix=f"report it to the authors of {entry.distribution}: name a site outside its line",
                )
            )
            return None
        return site

    def _unreachable(self, name: str, other: str, what: str) -> LayerProblem:
        """A parent or site that isn't in the tree: not installed, or left out for a problem of its own."""
        if (dropped := self._dropped.get(other)) is not None:
            return LayerProblem(
                layer=name,
                problem=LayerProblemKind.PARENT_LEFT_OUT,
                detail=f"its {what} {other} was left out ({dropped.problem.value}: {dropped.detail})",
                fix=dropped.fix,
            )
        kind = LayerProblemKind.MISSING_PARENT if what == "parent" else LayerProblemKind.MISSING_SITE
        return LayerProblem(
            layer=name,
            problem=kind,
            detail=f"its {what} layer {other} isn't installed",
            fix=f"hz system plugin add {plugin_of(other)}",
        )

    @property
    def layers(self) -> tuple[Layer, ...]:
        """Every installed layer that passed the checks, by name."""
        return tuple(self._layers.values())

    def get(self, name: str) -> Layer | None:
        """The installed layer `name`, or None."""
        return self._layers.get(name)

    def find(self, name: str) -> Layer | None:
        """The installed layer named `name`, or the one whose plugin is `name` (e.g. `heinzel-acme`)."""
        return self._layers.get(name) or next((la for la in self._layers.values() if la.distribution == name), None)

    def teams(self) -> list[Layer]:
        """The layers labelled team: the most specific ones a person can be in."""
        return [layer for layer in self._layers.values() if layer.label is Label.TEAM]

    def stack(self, name: str | None, *, source: str = "") -> "LayerStack":
        """The stack for the layer `name` (or its plugin's name), general to specific; empty, with a problem, without.

        The stack is the root, each layer down the parent chain, then the site, then `name`: company, org + site,
        team. The site is the one `name` declares, or the nearest one a layer above it declares; it comes before a
        layer with nothing below it (a team) and after one with layers below it (an org).
        """
        if name is None:
            return LayerStack(source=source)
        layer = self.find(name)
        if layer is None:
            problem = self._dropped.get(name) or LayerProblem(
                layer=name,
                problem=LayerProblemKind.NOT_INSTALLED,
                detail=f"your layer {name} ({source or 'declared'}) isn't installed",
                fix=f"hz system plugin add {name if name.startswith(DISTRIBUTION_PREFIX) else plugin_of(name)}",
            )
            return LayerStack(layer=name, source=source, problems=(problem,))
        chain: list[Layer] = []
        current: Layer | None = layer
        while current is not None:
            chain.append(current)
            current = self._layers.get(current.parent) if current.parent else None
        chain.reverse()
        site_name = next((c.site for c in reversed(chain) if c.site), None)
        site = self._layers.get(site_name) if site_name else None
        layers = list(chain)
        if site is not None and site not in layers:
            # A team (a leaf below a parent, even one that is missing or also someone's site): the site refines its org.
            before = layer.name not in self._children and (layer.label is Label.TEAM or layer.parent is not None)
            layers.insert(len(layers) - 1 if before else len(layers), site)
        site_ancestors = frozenset(self._ancestors(site.name)) if site is not None else frozenset[str]()
        names = {c.name for c in layers}
        return LayerStack(
            layer=layer.name,
            layers=tuple(layers),
            source=source,
            problems=tuple(p for p in self.problems if p.layer in names),
            site=site.name if site else None,
            site_ancestors=site_ancestors,
        )

    def _ancestors(self, name: str) -> list[str]:
        found: list[str] = []
        parent = self._parents.get(name)
        while parent is not None and parent not in found and parent != name:
            found.append(parent)
            parent = self._parents.get(parent)
        return found


def _label(name: str, parent: str | None, sites: set[str], has_children: set[str]) -> Label:
    if name in sites:
        return Label.SITE
    if parent is None:
        return Label.COMPANY
    return Label.ORG if name in has_children else Label.TEAM


def _cycles(parents: Mapping[str, str | None]) -> list[list[str]]:
    """Every cycle in the parent relation, each once, starting at its smallest name."""
    found: list[list[str]] = []
    done: set[str] = set()
    for start in sorted(parents):
        path: list[str] = []
        current: str | None = start
        while current is not None and current in parents and current not in done and current not in path:
            path.append(current)
            current = parents[current]
        if current is not None and current in path:
            cycle = path[path.index(current) :]
            first = cycle.index(min(cycle))
            found.append(cycle[first:] + cycle[:first])
        done.update(path)
    return found


@dataclass(frozen=True, kw_only=True, slots=True)
class LayerRecommendation:
    """A setting the stack recommends: the most specific layer's value, with what more general layers said."""

    key: str
    value: Any
    layer: str
    """The layer whose value is offered."""
    alternatives: tuple[tuple[str, Any], ...] = ()
    """What more general layers recommend for the same key, as `(layer, value)`, most specific first."""


@dataclass(frozen=True, kw_only=True, slots=True)
class LayerConflict:
    """An org and the site recommend different values for one key and the team is silent: nothing is offered."""

    key: str
    values: tuple[tuple[str, Any], ...]
    """Each layer's value, as `(layer, value)`, general to specific."""


type Precedence = Callable[["LayerStack", str, Sequence[tuple[Layer, Any]]], LayerRecommendation | LayerConflict]
"""Decides one key the stack's layers recommend: given the stack, the key and each layer's value general to specific,
the recommendation to offer, or a conflict to report."""


def team_decides(
    stack: "LayerStack", key: str, values: Sequence[tuple[Layer, Any]]
) -> LayerRecommendation | LayerConflict:
    """The proposal of ADR 0040 for the org-site question: the team decides.

    The most specific value wins, as everywhere in the stack, with one exception: where the site and an org that isn't
    above the site recommend different values, the stack can't tell which is more specific. Then the person's own
    layer (the team) decides when it sets the key; when it is silent, the conflict is reported, nothing is offered,
    and the person's file decides until the team sets it. No silent merge.
    """
    own = next(((layer, v) for layer, v in values if layer.name == stack.layer), None)
    winner, value = own or values[-1]
    alternatives = tuple((layer.name, v) for layer, v in reversed(values) if layer is not winner and v != value)
    decided = LayerRecommendation(key=key, value=value, layer=winner.name, alternatives=alternatives)
    if own is not None or stack.site is None:
        return decided
    site_value = next((v for layer, v in values if layer.name == stack.site), _UNSET)
    if site_value is _UNSET:
        return decided
    rivals = [
        layer.name
        for layer, v in values
        if layer.name != stack.site and layer.name not in stack.site_ancestors and v != site_value
    ]
    if not rivals:
        return decided
    return LayerConflict(key=key, values=tuple((layer.name, v) for layer, v in values))


_UNSET = object()

ORG_SITE_PRECEDENCE: Precedence = team_decides
"""The one place that decides an org-site conflict (ADR 0040, open): `team_decides` until the maintainer decides.

Kernel-internal on purpose: the decision is the maintainer's, in this assignment, never a plugin's."""


@dataclass(frozen=True, kw_only=True, slots=True)
class LayerStack:
    """The layers a person is in, general to specific, and what they bring together (ADR 0040).

    `Kernel.layer_stack()` builds the person's afresh on every call, so a team joined in this process counts; with a
    layer's name it builds that layer's.
    """

    layer: str | None = None
    """The person's layer, e.g. their team's; None when nothing says which layer they are in."""
    layers: tuple[Layer, ...] = ()
    """General to specific: the company, each org, the site, the team."""
    source: str = ""
    """Where the person's layer came from, e.g. "your team" or "the only team layer installed"."""
    problems: tuple[LayerProblem, ...] = ()
    """Why the stack is empty or incomplete, each with its fix."""
    site: str | None = None
    site_ancestors: frozenset[str] = field(default_factory=frozenset[str])
    """The layers above the site: the site refines them, so they never conflict with it."""

    @property
    def names(self) -> tuple[str, ...]:
        """The layers' names, general to specific."""
        return tuple(layer.name for layer in self.layers)

    def describe(self) -> str:
        """The stack in one line, e.g. `acme → acme-payments + acme-lisbon → acme-payments-checkout`."""
        parts: list[str] = []
        for layer in self.layers:
            if layer.name == self.site and parts:
                parts[-1] += f" + {layer.name}"
            else:
                parts.append(layer.name)
        return " → ".join(parts)

    def roles(self) -> tuple[str, ...]:
        """The roles the layers add, general to specific, each once."""
        return tuple(dict.fromkeys(role for layer in self.layers for role in layer.roles))

    def playbooks(self) -> tuple[tuple[Layer, PlaybookLet], ...]:
        """The layers' playbooks, most specific first: the order they are offered in."""
        return tuple((layer, layer.playbook) for layer in reversed(self.layers) if layer.playbook is not None)

    def recommendations(self) -> tuple[tuple[LayerRecommendation, ...], tuple[LayerConflict, ...]]:
        """The settings the layers' playbooks recommend, most specific first, and the conflicts nobody decided.

        A key named by several layers is offered once, as `ORG_SITE_PRECEDENCE` decides, with the other layers'
        values as alternatives. Nothing here is applied: a recommendation becomes configuration only when the person
        accepts it (ADR 0012, 0037).
        """
        by_key: dict[str, list[tuple[Layer, Any]]] = {}
        for layer in self.layers:
            if layer.playbook is not None:
                for key, value in layer.playbook.defaults.items():
                    by_key.setdefault(key, []).append((layer, value))
        offered: list[LayerRecommendation] = []
        conflicts: list[LayerConflict] = []
        for key, values in by_key.items():
            decided = ORG_SITE_PRECEDENCE(self, key, values)
            if isinstance(decided, LayerConflict):
                conflicts.append(decided)
            else:
                offered.append(decided)
        rank = {name: index for index, name in enumerate(self.names)}
        offered.sort(key=lambda r: -rank.get(r.layer, -1))  # stable: each layer's keys keep their declared order
        return tuple(offered), tuple(conflicts)
