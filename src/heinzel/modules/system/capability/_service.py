"""Service of the capability module: the report and its health checks."""

from heinzel import Check, Status
from heinzel.kernel import CAPABILITIES, MODES, Capabilities
from heinzel.modules.system.capability._models import AdapterInfo, CapabilityInfo, CapabilityReport


class CapabilityService:
    """Reports capabilities, adapters, bindings and requirements; never calls an adapter."""

    def __init__(self, capabilities: Capabilities) -> None:
        """Work on the kernel's capabilities, injected."""
        self.capabilities = capabilities

    def report(self) -> CapabilityReport:
        """Capabilities with their bindings, installed adapters, and the matrix."""
        settings = self.capabilities.settings
        return CapabilityReport(
            capabilities=[
                CapabilityInfo(
                    name=spec.name,
                    summary=spec.summary,
                    modes=[mode for mode in MODES if mode in spec.modes],
                    bound=settings.bound(spec.name),
                )
                for spec in sorted(CAPABILITIES.values(), key=lambda spec: spec.name)
            ],
            adapters=[
                AdapterInfo(
                    adapter=adapter.let.adapter,
                    capability=adapter.let.capability,
                    modes=[mode for mode in MODES if mode in adapter.let.modes],
                    hosts=list(adapter.let.hosts),
                    module=adapter.module,
                )
                for _, adapter in sorted(self.capabilities.adapters.items())
            ],
            uses=self.capabilities.matrix(),
        )

    def matrix(self) -> dict[str, str]:
        """Where data can go, one entry per requirement: `requester → need` to `adapter → hosts`, or the problem."""
        entries: dict[str, str] = {}
        for use in self.capabilities.matrix():
            hosts = ", ".join(use.hosts) or "hosts set by its configuration"
            entries[f"{use.requester} → {use.requirement}"] = (
                f"unmet: {use.problem}" if use.problem else f"{use.adapter} → {hosts}"
            )
        return entries

    def checks(self) -> list[Check]:
        """One check per declared requirement and one per binding to an adapter nobody provides."""
        checks = [
            Check(
                name=f"binding {capability}",
                status=Status.DEGRADED,
                detail=f"{capability} is bound to {adapter!r}, which no installed module provides",
                fix=self.capabilities.fix(capability),
            )
            for capability, adapter in self.capabilities.unknown_bindings()
        ]
        for use in self.capabilities.matrix():
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
