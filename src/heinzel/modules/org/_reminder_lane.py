"""The daemon's calendar reminder lane: a child of a scheduled tick that ends only inside a slot window (ADR 0147).

The scheduled daemon tick starts `hz org calendar-reminder-lane` and hands it a pipe that closes when the tick's jobs
are done. The lane runs 0126's cycle (refresh at least every 30 seconds, wake at the next due instant, revalidate
before delivery) and ends only once the jobs are done and the wall clock is inside the end window `[s - m, s - m/2)`
of the next local slot `s`. Its last cycle delivers what falls due before the next lane can be running, early rather
than late.
"""

import json
import os
import select
import signal
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Literal, cast

from heinzel import HeinzelError, Kernel, NoticeKind, Notifications, __version__
from heinzel.kernel import (
    DaemonAttestation,
    Installation,
    Progress,
    installation_mutation_pending,
    private_lock,
)
from heinzel.kernel._calendar_reminders import MAX_POLL, Agenda, cycle
from heinzel.modules.org._reminder_service import Profile, lifecycle, migrate

ENVIRONMENT = "HZ_REMINDER_LANE"
"""The daemon tick's lane specification, as JSON; a lane started without it refuses to run."""
MARGIN = 15.0
"""Seconds `m` before a slot the end window opens; measured on macOS and Linux, a miss costs one dropped firing."""
READ_TIMEOUT = 8.0
"""Hard limit for one agenda read; a read starts only while more than this is left before the window end."""
LAST_READ_LEAD = 2 * READ_TIMEOUT + 2
"""Seconds before the window end the last cycle starts: room for its read and one retry when the fence is busy."""
STARTUP_CAP = 90.0
"""The most a lane's measured start-up time `H` counts for early delivery."""
OFFSET_CAP = 60.0
"""The most systemd's fixed per-machine timer delay counts; launchd has none."""
BEAT_AT_LEAST = 60.0
BEAT_AT_MOST = 30.0
FRESH = timedelta(minutes=2)
"""A lane heartbeat younger than this means a lane is running for its profile."""
LANE_FILE = "lane.json"


@dataclass(frozen=True)
class LaneSpec:
    """What the parent tick tells its lane: the pipe, the state directory, the interval and its crash history."""

    fd: int
    directory: Path
    interval: int
    spawned: float
    offset: float = 0.0
    crashes: int = 0
    restarts: int = 0
    overrun: str | None = None

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> "LaneSpec":
        """Read the lane specification the tick put into the environment.

        Raises:
            HeinzelError: If this process was not started by a scheduled daemon tick.
        """
        try:
            data = json.loads(environ[ENVIRONMENT])
            spec = cls(
                fd=int(data["fd"]),
                directory=Path(data["dir"]),
                interval=int(data["interval"]),
                spawned=float(data["spawned"]),
                offset=float(data.get("offset", 0.0)),
                crashes=int(data.get("crashes", 0)),
                restarts=int(data.get("restarts", 0)),
                overrun=data.get("overrun") if isinstance(data.get("overrun"), str) else None,
            )
        except (KeyError, ValueError, TypeError) as exc:
            msg = "Only a scheduled daemon tick starts the calendar reminder lane; turn on Calendar reminders instead."
            raise HeinzelError(msg) from exc
        if not spec.directory.is_absolute() or spec.interval < 1 or spec.fd < 0:
            msg = "The calendar reminder lane was started with an invalid specification."
            raise HeinzelError(msg)
        return spec

    def environment(self) -> str:
        """The specification as the tick passes it to the child."""
        return json.dumps(
            {
                "fd": self.fd,
                "dir": str(self.directory),
                "interval": self.interval,
                "spawned": self.spawned,
                "offset": self.offset,
                "crashes": self.crashes,
                "restarts": self.restarts,
                "overrun": self.overrun,
            },
            sort_keys=True,
        )


def target_slot(now: datetime, minutes: int, margin: float = MARGIN) -> datetime:
    """The first local slot `s` whose end window has not passed yet: `now < s - m/2`.

    Slots are whole multiples of `minutes` in the wall time of `now`'s zone, so zones with :30 and :45 offsets end at
    their own local slots. A lane that is still running jobs at a window, or wakes past one, targets the next slot.
    """
    slot = now.replace(minute=now.minute // minutes * minutes, second=0, microsecond=0)
    while now >= slot - timedelta(seconds=margin / 2):
        slot += timedelta(minutes=minutes)
    return slot


@dataclass(frozen=True)
class LaneClock:
    """The lane's view of time; tests replace every part."""

    wall: Callable[[], datetime] = lambda: datetime.now(UTC).astimezone()
    monotonic: Callable[[], float] = time.monotonic
    tzset: Callable[[], None] = time.tzset


@dataclass
class LaneHooks:
    """How the lane reads, fences, waits and reports; tests replace every part."""

    read: Callable[[], Agenda]
    guard: Callable[[], AbstractContextManager[None]]
    gate: Callable[[], bool]
    wait: Callable[[float], bool]
    """Sleep up to the seconds given; True once the tick's jobs are done (returns at once then)."""
    beat: Callable[[dict[str, str | int | None]], None]
    stopped: Callable[[], bool] = lambda: False


@dataclass
class _Beats:
    hooks: LaneHooks
    clock: LaneClock
    static: dict[str, str | int | None] = field(default_factory=dict[str, str | int | None])
    last: dict[str, str | int | None] | None = None
    written: float | None = None

    def __call__(self, slot: datetime, result: str, *, force: bool = False) -> None:
        state: dict[str, str | int | None] = {
            **self.static,
            "slot": slot.isoformat(),
            "window_start": (slot - timedelta(seconds=MARGIN)).isoformat(),
            "window_end": (slot - timedelta(seconds=MARGIN / 2)).isoformat(),
            "result": result,
        }
        tick = self.clock.monotonic()
        since = None if self.written is None else tick - self.written
        changed = state != self.last
        if not force and since is not None and (since < BEAT_AT_MOST or (not changed and since < BEAT_AT_LEAST)):
            return
        self.last, self.written = state, tick
        self.hooks.beat(state | {"at": self.clock.wall().astimezone(UTC).isoformat()})


def run_lane(  # noqa: PLR0913 - the lane's seams, all by keyword
    hooks: LaneHooks,
    notifications: Notifications,
    *,
    interval: int,
    clock: LaneClock | None = None,
    startup: float = 0.0,
    offset: float = 0.0,
    identity: Mapping[str, str | int | None] | None = None,
) -> str:
    """Run reminder cycles until the slot window after the tick's jobs; the reason it ended.

    Returns `window` at a slot window, `disabled` when Calendar reminders were turned off, `gate` when maintenance
    is pending (an update waits for the lane) or the daemon was paused, or `stopped` after SIGTERM. `offset` is how
    long after its slot the next tick may start (systemd's timer delay): the last cycle delivers that much further
    ahead.
    """
    clock = clock or LaneClock()
    beats = _Beats(hooks, clock, dict(identity or {}))
    head = min(max(startup, 0.0), STARTUP_CAP) + min(max(offset, 0.0), OFFSET_CAP)
    done = hooks.wait(0.0)
    while True:
        clock.tzset()
        now = clock.wall()
        slot = target_slot(now, interval)
        end = slot - timedelta(seconds=MARGIN / 2)
        # The last cycle reads while more than the read timeout is left before the window end, then the lane waits
        # for the window and ends inside it.
        last_read = end - timedelta(seconds=LAST_READ_LEAD)
        if hooks.stopped() or hooks.gate():
            ended = "stopped" if hooks.stopped() else "gate"
            beats(slot, ended, force=True)
            return ended
        if done and now >= last_read:
            return _last_cycle(
                hooks, notifications, clock, beats, slot, lookahead=end + timedelta(seconds=MARGIN + head)
            )
        result, delay = _one_cycle(hooks, notifications, now, timedelta(0), clock)
        if result == "disabled":
            beats(slot, result, force=True)
            return result
        beats(slot, result)
        pause = min(delay.total_seconds(), MAX_POLL.total_seconds())
        remaining = (last_read - clock.wall()).total_seconds()
        if remaining > 0:
            pause = min(pause, remaining)
        done = hooks.wait(max(0.01, pause)) or done


def _last_cycle(  # noqa: PLR0913 - the lane's state at its end
    hooks: LaneHooks,
    notifications: Notifications,
    clock: LaneClock,
    beats: "_Beats",
    slot: datetime,
    *,
    lookahead: datetime,
) -> str:
    """Deliver what falls due before the next lane can run, wait for the end window, end inside it (point 5)."""
    start, end = slot - timedelta(seconds=MARGIN), slot - timedelta(seconds=MARGIN / 2)
    result = "window"
    for _attempt in range(2):  # a busy fence gets one retry, so early delivery is not lost to it
        now = clock.wall()
        if (end - now).total_seconds() <= READ_TIMEOUT or hooks.gate() or hooks.stopped():
            break
        result, _ = _one_cycle(hooks, notifications, now, lookahead - now, clock)
        if result != "gate":
            break
        hooks.wait(1.0)
    ended = "disabled" if result == "disabled" else "window"
    rest = (start - clock.wall()).total_seconds()
    if ended == "window" and rest > 0:
        hooks.wait(rest)
    if hooks.stopped() or hooks.gate():
        ended = "stopped" if hooks.stopped() else "gate"
    beats(slot, ended, force=True)
    return ended


def _one_cycle(
    hooks: LaneHooks, notifications: Notifications, now: datetime, lookahead: timedelta, clock: LaneClock
) -> tuple[str, timedelta]:
    enabled = notifications.enabled(NoticeKind.CALENDAR)
    problem: list[str] = []

    def read() -> Agenda:
        try:
            agenda = _bounded(hooks.read, READ_TIMEOUT)
        except Exception:
            problem.append("provider-unavailable")
            raise
        if agenda.problem is not None or agenda.calendar is None:
            problem.append("provider-unavailable")
        return agenda

    try:
        with hooks.guard():
            delay = cycle(read, notifications, now, enabled=enabled, clock=clock.wall, lookahead=lookahead)
    except HeinzelError:
        return "gate", MAX_POLL
    if not enabled:
        return "disabled", delay
    return (problem[0] if problem else "ok"), delay


def _bounded(read: Callable[[], Agenda], seconds: float) -> Agenda:
    """Read with a hard timeout; a hung provider is left behind on a daemon thread.

    Raises:
        TimeoutError: If the read did not finish in time.
    """
    result: list[Agenda] = []
    failure: list[BaseException] = []

    def target() -> None:
        try:
            result.append(read())
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            failure.append(exc)

    thread = threading.Thread(target=target, name="hz-calendar-read", daemon=True)
    thread.start()
    thread.join(seconds)
    if failure:
        raise failure[0]
    if not result:
        msg = "The calendar read did not finish in time."
        raise TimeoutError(msg)
    return result[0]


def pipe_wait(fd: int, stopped: Callable[[], bool] = lambda: False) -> Callable[[float], bool]:
    """Wait on the tick's pipe: readable at end of file once the parent closed it after its jobs.

    Waits in slices of at most a second, so SIGTERM ends the lane within about a second.
    """
    closed = False

    def wait(seconds: float) -> bool:
        nonlocal closed
        deadline = time.monotonic() + seconds
        while True:  # polls at least once, so a zero wait still sees a pipe the tick already closed
            left = max(deadline - time.monotonic(), 0.0)
            if closed:
                time.sleep(min(left, 1.0))
            else:
                readable, _, _ = select.select([fd], [], [], min(left, 1.0))
                if readable and not os.read(fd, 1):
                    closed = True
                    return True
            if stopped() or deadline - time.monotonic() <= 0:
                return closed

    return wait


def stop_flag() -> Callable[[], bool]:
    """Turn SIGTERM into a clean end at the next check, so an update never waits for a whole cycle."""
    stopped = threading.Event()

    def handle(_signum: int, _frame: FrameType | None) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, handle)
    return stopped.is_set


def write_beat(directory: Path) -> Callable[[dict[str, str | int | None]], None]:
    """Write the lane heartbeat atomically, private to the user."""

    def beat(state: dict[str, str | int | None]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{LANE_FILE}.{os.getpid()}"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
        temporary.replace(directory / LANE_FILE)

    return beat


def paused(directory: Path) -> bool:
    """Whether the daemon of this lane is paused: pausing the daemon also pauses its reminders (ADR 0147, point 11)."""
    return (directory / "paused").exists()


def read_beat(directory: Path) -> dict[str, object]:
    """The last lane heartbeat in a daemon state directory, or empty."""
    try:
        data = json.loads((directory / LANE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cast("dict[str, object]", data) if isinstance(data, dict) else {}


def fresh_lane(directory: Path, identity: str, now: datetime) -> bool:
    """Whether a lane wrote a heartbeat for this exact profile within the last two minutes (ADR 0147, point 9)."""
    beat = read_beat(directory)
    try:
        at = datetime.fromisoformat(str(beat["at"]))
    except (KeyError, ValueError):
        return False
    if at.tzinfo is None:
        return False
    return (
        beat.get("profile") == identity
        and beat.get("result") not in {"stopped", "disabled"}
        and (timedelta(0) <= now - at < FRESH)
    )


def lane_identity(spec: LaneSpec, profile: str) -> dict[str, str | int | None]:
    """What every heartbeat of this lane carries."""
    return {
        "pid": os.getpid(),
        "version": __version__,
        "profile": profile,
        "crashes": spec.crashes,
        "restarts": spec.restarts,
        "overrun": spec.overrun,
    }


def main(kernel: Kernel, read: Callable[[], Agenda]) -> str:
    """The lane process: one per profile, migration first, then cycles until the slot window.

    Raises:
        HeinzelError: If no scheduled daemon tick started this process.
    """
    spec = LaneSpec.from_environment()
    startup = time.time() - spec.spawned
    selected = Profile.selected()
    with ExitStack() as stack:
        try:
            stack.enter_context(private_lock(spec.directory / "lane.lock"))
        except HeinzelError:
            return "duplicate"
        stopped = stop_flag()
        migrate()
        notifications = kernel.resolve(Notifications)
        prefix = Installation(Progress()).prefix
        state = Path(selected.environment["HZ_STATE_DIR"])
        hooks = LaneHooks(
            read=read,
            guard=lambda: lifecycle(selected),
            gate=lambda: installation_mutation_pending(prefix, state) or paused(spec.directory),
            wait=pipe_wait(spec.fd, stopped),
            beat=write_beat(spec.directory),
            stopped=stopped,
        )
        return run_lane(
            hooks,
            notifications,
            interval=spec.interval,
            startup=startup,
            offset=spec.offset,
            identity=lane_identity(spec, selected.identity),
        )


RUNNING = frozenset({"ok", "provider-unavailable", "window"})
"""Heartbeat results of a lane that is still running its cycles."""

ReminderState = Literal["off", "on", "starting", "install", "elsewhere", "resume", "provider", "overdue"]


def last_slot(now: datetime, minutes: int) -> datetime:
    """The latest local slot at or before `now`."""
    return now.replace(minute=now.minute // minutes * minutes, second=0, microsecond=0)


def reminder_state(enabled: bool, daemon: DaemonAttestation | None, now: datetime) -> ReminderState:  # noqa: PLR0911 - one answer per state
    """The one state Calendar shows: what keeps reminders from running here, or that they run (ADR 0147, point 13)."""
    if not enabled:
        return "off"
    if daemon is None:
        return "install"
    status = daemon.status()
    if not status.installed:
        return "install"
    if status.installed_version is None:
        return "elsewhere"
    if getattr(status, "paused", False):
        return "resume"
    lane = daemon.lane()
    fresh = lane.beat_at is not None and timedelta(0) <= now - lane.beat_at < FRESH
    # A lane that ended (reminders were off, the daemon paused, maintenance, SIGTERM) runs nothing until the next tick.
    running = fresh and lane.result in RUNNING
    if running and lane.result == "provider-unavailable":
        return "provider"
    interval = getattr(status, "interval_minutes", None)
    slot = last_slot(now.astimezone(), interval) if isinstance(interval, int) and interval > 0 else None
    if lane.started and not fresh and (slot is None or now - slot > FRESH):
        return "overdue"
    return "on" if running else "starting"
