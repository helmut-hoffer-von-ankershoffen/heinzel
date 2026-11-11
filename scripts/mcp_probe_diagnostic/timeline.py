"""Validate private marker bytes and return only fixed labels and relative timings."""

import re
from pathlib import Path

MAX_BYTES = 8192
MAX_EVENTS = 32
BASE = ("launch.before", "launch.after", "communicate.before", "communicate.after", "child.entry", "child.import_done")
PAIRS = (
    "client.enter",
    "transport.enter",
    "session.enter",
    "init",
    "list",
    "session.exit",
    "transport.exit",
    "client.exit",
)
ALLOWED = frozenset(
    (
        *BASE,
        "observe.exit",
        "client.401",
        "fake.server.401_sent",
        *(f"{p}.{s}" for p in PAIRS for s in ("start", "ok", "error")),
    )
)


def parse_timeline(path: Path, *, fake: bool = False) -> tuple[str, list[tuple[str, int]]]:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Reject unexpected, duplicate, oversized, or inconsistent marker sequences."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        return "unavailable", []
    if len(raw) > MAX_BYTES:
        return "invalid", []
    events: dict[str, int] = {}
    for line in raw.splitlines():
        match = re.fullmatch(rb"([a-z0-9_.]+) ([0-9]{1,20})", line)
        if not match:
            return "invalid", []
        label = match[1].decode("ascii")
        if label not in ALLOWED or label in events:
            return "invalid", []
        events[label] = int(match[2])
    if len(events) > MAX_EVENTS:
        return "invalid", []
    if "launch.before" not in events:
        return "incomplete", []
    base = events["launch.before"]
    if any(stamp < base for stamp in events.values()):
        return "invalid", []
    order = (
        ("launch.before", "launch.after"),
        ("communicate.before", "communicate.after"),
        ("child.entry", "child.import_done"),
        ("client.enter.ok", "transport.enter.start"),
        ("transport.enter.ok", "session.enter.start"),
        ("session.enter.ok", "init.start"),
        ("init.ok", "list.start"),
        ("session.exit.start", "transport.exit.start"),
        ("transport.exit.start", "client.exit.start"),
    )
    if any(first in events and second in events and events[first] > events[second] for first, second in order):
        return "invalid", []
    if "client.401" in events:
        if "client.enter.ok" in events and events["client.401"] < events["client.enter.ok"]:
            return "invalid", []
        if "client.exit.start" in events and events["client.401"] > events["client.exit.start"]:
            return "invalid", []
        if "observe.exit" in events and events["client.401"] > events["observe.exit"]:
            return "invalid", []
    for operation in ("init", "list"):
        for outcome in ("ok", "error"):
            terminal = f"{operation}.{outcome}"
            if (
                terminal in events
                and "session.exit.start" in events
                and events[terminal] > events["session.exit.start"]
            ):
                return "invalid", []
    if fake and "client.401" in events:
        if "init.start" in events and events["client.401"] < events["init.start"]:
            return "invalid", []
        if "init.error" in events and events["client.401"] > events["init.error"]:
            return "invalid", []
    if "init.error" in events and any(name.startswith("list.") for name in events):
        return "invalid", []
    if "list.start" in events and "init.ok" not in events:
        return "invalid", []
    for inner, outer in (("session", "transport"), ("transport", "client")):
        for outcome in ("ok", "error"):
            end, outer_start = f"{inner}.exit.{outcome}", f"{outer}.exit.start"
            if end in events and outer_start in events and events[end] > events[outer_start]:
                return "invalid", []
    for phase in PAIRS:
        start, ok, error = (f"{phase}.{suffix}" for suffix in ("start", "ok", "error"))
        if ok in events and error in events:
            return "invalid", []
        end = ok if ok in events else error if error in events else None
        if end and (start not in events or events[end] < events[start]):
            return "invalid", []
        if end and "observe.exit" in events and events[end] > events["observe.exit"]:
            return "invalid", []
    values = [(name, (stamp - base) // 1_000_000) for name, stamp in sorted(events.items(), key=lambda e: e[1])]
    required = {*BASE, "observe.exit", "client.enter.start"}
    for phase in PAIRS:
        if f"{phase}.start" in events and not ({f"{phase}.ok", f"{phase}.error"} & events.keys()):
            return "incomplete", values
    for context in ("client", "transport", "session"):
        if f"{context}.enter.ok" in events:
            required.add(f"{context}.exit.start")
            if f"{context}.exit.start" in events and events[f"{context}.exit.start"] < events[f"{context}.enter.ok"]:
                return "invalid", []
    if "client.enter.ok" in events:
        required.add("transport.enter.start")
    if "transport.enter.ok" in events:
        required.add("session.enter.start")
    if "session.enter.ok" in events:
        required.add("init.start")
    if "init.ok" in events:
        required.add("list.start")
    if fake:
        required.update(("client.401", "fake.server.401_sent", "init.start", "init.error"))
    return ("complete" if required <= events.keys() else "incomplete"), values


def valid_phase(line: str, case: str, phase: str) -> bool:
    """Match a phase receipt to its expected fixed case and phase."""
    if case not in ("true", "false", "fake") or phase not in ("setup", "call", "teardown"):
        return False
    return (
        re.fullmatch(
            rf"VNXD case={case} phase={phase} outcome=(?:passed|failed|skipped) "
            r"kind=(?:none|timeout|assertion|exception|unknown) duration_ms=[0-9]+",
            line,
        )
        is not None
    )


def fake_control_complete(code: int, status: str, phases: dict[str, str]) -> bool:
    """A control needs all three identity-matched phase receipts and a complete 401 timeline."""
    return (
        code == 0
        and status == "complete"
        and set(phases) == {"setup", "call", "teardown"}
        and all(
            valid_phase(line, "fake", phase) and f"phase={phase} outcome=passed kind=none " in line
            for phase, line in phases.items()
        )
    )
