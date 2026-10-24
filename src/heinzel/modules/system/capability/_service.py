"""Service of the capability module: the report and its health checks."""

from heinzel import Check, Status
from heinzel.kernel import CAPABILITIES, MODES, CapabilityView
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

    def matrix(self) -> dict[str, str]:
        """Where data can go, one entry per requirement: `requester → need` to `adapter → hosts`, or the problem."""
        return {
            f"{use.requester} → {use.requirement}": (
                f"unmet: {use.problem}" if use.problem else f"{use.adapter} → {', '.join(use.hosts)}"
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
        uses = self.uses()
        checks.extend(self._host_checks(uses))
        for use in uses:
            name = f"{use.requirement} for {use.requester}"
            if use.problem is None:
                checks.append(Check(name=name, status=Status.UP, detail=f"served by {use.adapter}"))
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
