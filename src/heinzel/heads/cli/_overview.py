"""Local read-only start screen; extension contributions are inert cached records (ADR 0119)."""

import time
from contextlib import suppress
from itertools import islice

from rich.console import Console
from rich.text import Text

from heinzel.heads.cli._render import TerminalSafe
from heinzel.kernel import (
    CachedOverviewLet,
    DaemonOverview,
    HeinzelError,
    Kernel,
    LocalObservation,
    Settings,
    WebOverview,
)
from heinzel.kernel._local_overview import MAX_RECORDS, cached_observation

BUDGET = 0.25


def show(kernel: Kernel, console: Console) -> None:
    """Share a 250 ms observation budget; boot time and child cleanup are outside that budget."""
    deadline = time.monotonic() + BUDGET
    observations: list[tuple[str, LocalObservation]] = []
    contracts: tuple[tuple[str, type[DaemonOverview | WebOverview], str], ...] = (
        ("Scheduler", DaemonOverview, "system.daemon"),
        ("Web server", WebOverview, "ui"),
    )
    for label, contract, owner in contracts:
        observation = LocalObservation("unknown", "local observer unavailable")
        if time.monotonic() < deadline:
            with suppress(HeinzelError, OSError, ValueError):
                observation = kernel.resolve_builtin_service(contract, owner).observe(deadline)
        observations.append((label, observation))
    sharing = list(islice(kernel.lets(CachedOverviewLet), MAX_RECORDS))
    if sharing:
        state_dir = kernel.resolve(Settings).state_dir
        for record, let in sharing:
            filename = f"{record.qualname}.{let.name}.json"
            observations.append((f"Sharing ({record.qualname})", cached_observation(state_dir, filename, deadline)))
    else:
        observations.append(("Sharing", LocalObservation("unknown", "no local observations declared")))
    for label, observation in observations:
        line = f"{label}: {observation.state}"
        if observation.detail:
            line += f" · {observation.detail}"
        console.print(TerminalSafe(Text(line)))
