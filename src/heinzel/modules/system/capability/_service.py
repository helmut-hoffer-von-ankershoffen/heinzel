"""Service of the capability module: the report and its health checks."""

from heinzel import Check, Status
from heinzel.kernel import CAPABILITIES, CORE_DISTRIBUTION, MODES, CapabilityView
from heinzel.modules.system.capability._models import AdapterInfo, CapabilityInfo, CapabilityReport, CapabilityUse

UNKNOWN_HOSTS = "unknown (configured)"
"""What the matrix says for an adapter whose hosts depend on its configuration and aren't known."""


class CapabilityService:
    """Reports capabilities, adapters, bindings and requirements; never calls an adapter."""

    def __init__(self, capabilities: CapabilityView) -> None:
        """Work on the kernel's read-only view of capabilities, injected."""
        self.capabilities = capabilities

    def uses(self) -> list[CapabilityUse]:
        """Every declared requirement with its adapter, hosts and problem."""
        return [
            CapabilityUse(
                requester=use.requester,
                kind=use.kind,
                requirement=use.requirement,
                capability=use.capability,
                modes=list(use.modes),
                adapter=use.adapter,
                hosts=list(use.hosts) or ([UNKNOWN_HOSTS] if use.adapter else []),
                problem=use.problem,
                optional=use.optional,
            )
            for use in self.capabilities.matrix()
        ]

    def report(self) -> CapabilityReport:
        """Capabilities with their bindings, installed adapters, and the matrix."""
        return CapabilityReport(
            capabilities=[
                CapabilityInfo(
                    name=spec.name,
                    summary=spec.summary,
                    modes=[mode for mode in MODES if mode in spec.modes],
                    bound=self.capabilities.bound(spec.name),
                )
                for spec in sorted(CAPABILITIES.values(), key=lambda spec: spec.name)
            ],
            adapters=[
                AdapterInfo(
                    adapter=adapter.let.adapter,
                    capability=adapter.let.capability,
                    modes=[mode for mode in MODES if mode in adapter.let.modes],
                    hosts=list(self.capabilities.hosts(adapter)[0]) or [UNKNOWN_HOSTS],
                    module=adapter.module,
                    distribution=adapter.distribution,
                )
                for adapter in self.capabilities.adapters
            ],
            uses=self.uses(),
        )

    def unconnected(self, use: CapabilityUse) -> bool:
        """Whether `use` is optional and unmet only because nothing is bound: no finding, unlike a broken binding."""
        return use.optional and use.problem is not None and self.capabilities.bound(use.capability) is None

    def matrix(self) -> dict[str, str]:
        """Where data can go, one entry per requirement: `requester → need` to `adapter → hosts`, or the problem."""
        return {
            f"{use.requester} → {use.requirement}": (
                f"{'optional: not connected' if self.unconnected(use) else 'unmet'}: {use.problem}"
                if use.problem
                else f"{use.adapter} → {', '.join(use.hosts)}"
            )
            for use in self.uses()
        }

    def checks(self) -> list[Check]:
        """A check per requirement, bad binding or binding key, and used adapter whose hosts are unknown."""
        checks = [
            Check(
                name=f"binding {key}",
                status=Status.DEGRADED,
                detail=f"[capabilities] {key} names no capability",
                fix=f"Use one of {', '.join(sorted(CAPABILITIES))} (with `_` or `-`), or remove the key",
            )
            for key in self.capabilities.unknown_keys
        ]
        checks.extend(
            Check(
                name=f"binding {capability}",
                status=Status.DEGRADED,
                detail=f"{capability} is bound to {adapter!r}, which no installed module provides",
                fix=self.capabilities.fix(capability),
            )
            for capability, adapter in self.capabilities.unknown_bindings()
        )
        checks.extend(self._default_checks())
        uses = self.uses()
        checks.extend(self._host_checks(uses))
        # A module's optional uses of one capability nobody connected are one check, not one per operation.
        unconnected: dict[tuple[str, str], list[CapabilityUse]] = {}
        for use in uses:
            if self.unconnected(use):
                unconnected.setdefault((use.capability, use.requester.split(" ")[0]), []).append(use)
        for use in uses:
            name = f"{use.requirement} for {use.requester}"
            if use.problem is None:
                checks.append(Check(name=name, status=Status.UP, detail=f"served by {use.adapter}"))
            elif self.unconnected(use):
                group = unconnected[use.capability, use.requester.split(" ")[0]]
                if use is not group[0]:
                    continue
                if len(group) > 1:
                    name = f"{use.capability} for {use.requester.split(' ')[0]} ({len(group)} uses)"
                detail = f"optional: not connected; to use it: {self.capabilities.command(use.capability)}"
                checks.append(Check(name=name, status=Status.UP, detail=detail))
            else:
                checks.append(
                    Check(
                        name=name,
                        status=Status.DEGRADED,
                        detail=f"{use.capability}:{','.join(use.modes)}: {use.problem}",
                        fix=self.capabilities.fix(use.capability),
                    )
                )
        return checks or [Check(name="requirements", status=Status.UP, detail="no module declares capabilities")]

    def _default_checks(self) -> list[Check]:
        """A check per capability served by the core's default while another module's adapter could serve it.

        Declared beats discovered (ADR 0028), so installing a plugin binds nothing: this says which binding to write,
        e.g. a directory of record instead of the guesses from 1:1s. Binding the default explicitly silences it.
        """
        checks: list[Check] = []
        for spec in sorted(CAPABILITIES.values(), key=lambda spec: spec.name):
            bound = self.capabilities.bound(spec.name)
            if bound is None or not self.capabilities.defaulted(spec.name):
                continue
            provided = [a for a in self.capabilities.adapters if a.let.capability == spec.name]
            default = {a.module for a in provided if a.let.adapter == bound}
            # A plugin's adapter, not another core one: the core's own options (`gateway`) are chosen, not discovered.
            others = sorted(
                {a.let.adapter for a in provided if a.module not in default and a.distribution != CORE_DISTRIBUTION}
            )
            if not others:
                continue
            key = spec.name.replace("-", "_")
            checks.append(
                Check(
                    name=f"binding {spec.name}",
                    status=Status.DEGRADED,
                    detail=f"{spec.name} is served by the default, {bound}; {', '.join(others)} could serve it",
                    fix=f"Bind one, or {bound} to keep it: hz system config set capabilities.{key} {others[0]}",
                )
            )
        return checks

    def _host_checks(self, uses: list[CapabilityUse]) -> list[Check]:
        """A check per adapter that is bound or serves a requirement and whose hosts couldn't be read.

        An installed adapter nobody uses doesn't make doctor degraded: it sends nothing anywhere.
        """
        used = {(use.capability, use.adapter) for use in uses}
        return [
            Check(
                name=f"hosts of {adapter.let.name}",
                status=Status.DEGRADED,
                detail=f"{problem}; shown as {UNKNOWN_HOSTS}",
                fix=(
                    f"Check the settings of {adapter.module} with `hz system config show` and set a valid URL or "
                    f"host name in your user configuration; if they are valid, report it to {adapter.distribution}"
                ),
            )
            for adapter in self.capabilities.adapters
            if (adapter.let.capability, adapter.let.adapter) in used
            or self.capabilities.bound(adapter.let.capability) == adapter.let.adapter
            if (problem := self.capabilities.hosts(adapter)[1])
        ]
