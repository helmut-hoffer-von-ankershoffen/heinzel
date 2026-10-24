"""The graph service: reads the team's promises and telemetry through the kernel and builds the graph (ADR 0053)."""

from collections.abc import Callable
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from heinzel import HeinzelError, TeamPromises, TelemetryLog
from heinzel.kernel import Kernel, parse_duration
from heinzel.modules.graph._build import Builder, Inputs, cut
from heinzel.modules.graph._models import Filters, GraphSettings, State, TeamGraphDocument

LOCALTIME = Path("/etc/localtime")


def system_zone(localtime: Path = LOCALTIME) -> tuple[tzinfo, str]:
    """The system's time zone with its rules, by the zone file /etc/localtime points at; UTC when it names none."""
    resolved = localtime.resolve()
    if "zoneinfo" in resolved.parts:
        key = "/".join(resolved.parts[resolved.parts.index("zoneinfo") + 1 :])
        try:
            return ZoneInfo(key), key
        except (ValueError, ZoneInfoNotFoundError):
            pass
    return UTC, "UTC"


class GraphService:
    """Builds the team graph afresh on every call, from what the kernel's `TeamPromises` and `TelemetryLog` say."""

    def __init__(
        self,
        kernel: Kernel,
        settings: GraphSettings,
        clock: Callable[[], datetime] | None = None,
        zone: Callable[[], tuple[tzinfo, str]] = system_zone,
    ) -> None:
        """Read through `kernel`; tests pass the clock and the zone."""
        self._kernel = kernel
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._system_zone = zone

    def _zone(self) -> tuple[tzinfo, str]:
        name = self._settings.time_zone
        if name is None:
            return self._system_zone()
        try:
            return ZoneInfo(name), name
        except (ValueError, ZoneInfoNotFoundError) as exc:
            msg = (
                f"[graph] time_zone {name!r} isn't an IANA time zone; "
                "e.g. hz system config set graph.time_zone Europe/Lisbon"
            )
            raise HeinzelError(msg) from exc

    def graph(
        self,
        *,
        artifact: str | None = None,
        heinzel: str | None = None,
        state: State | None = None,
        at: datetime | None = None,
    ) -> TeamGraphDocument:
        """The team graph as of `at` (now by default), cut by the filters.

        Raises:
            HeinzelError: If `at` has no offset, the time zone is unknown, or a filter names no node in the graph.
        """
        if at is not None and at.tzinfo is None:
            msg = f"--at {at.isoformat()} needs an offset, e.g. {at.isoformat()}+00:00"
            raise HeinzelError(msg)
        promises = self._kernel.resolve(TeamPromises).promises() if self._kernel.provides(TeamPromises) else ()
        read = self._kernel.resolve(TelemetryLog).entries() if self._kernel.provides(TelemetryLog) else None
        zone, zone_name = self._zone()
        inputs = Inputs(
            promises=promises,
            entries=read.entries if read else (),
            problems=read.problems if read else (),
            as_of=(at or self._clock()).astimezone(UTC),
            zone=zone,
            zone_name=zone_name,
            offline_after=parse_duration(self._settings.offline_after),
            weeks=self._settings.weeks,
        )
        document = Builder(inputs).build()
        for wanted, kind in ((artifact, "artifact"), (heinzel, "heinzel")):
            node = document.graph.nodes.get(wanted or "")
            if wanted is not None and (node is None or node.metadata.kind != kind):
                known = ", ".join(n for n, v in document.graph.nodes.items() if v.metadata.kind == kind) or "none yet"
                msg = f"No {kind} {wanted!r} in your team's graph; the {kind}s: {known}"
                raise HeinzelError(msg)
        return cut(document, Filters(artifact=artifact, heinzel=heinzel, state=state))


class HzGraph:
    """The core's `team-graph` adapter: what `hz graph --json` shows, for a contract (the graph keeper) to read."""

    def __init__(self, service: GraphService) -> None:
        """Build through `service`."""
        self._service = service

    def snapshot(self) -> dict[str, Any]:
        """The current team graph as a JSON Graph Format document."""
        return self._service.graph().model_dump(mode="json")
