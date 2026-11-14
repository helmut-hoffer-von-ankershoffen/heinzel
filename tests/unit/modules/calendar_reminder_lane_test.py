"""The daemon's calendar reminder lane with fake clocks, fences and pipes (ADR 0147)."""

import json
import os
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from heinzel import Event, HeinzelError
from heinzel.kernel import ReminderLaneState
from heinzel.kernel._calendar_reminders import agenda_plan
from heinzel.modules.org import _reminder_lane as lane
from heinzel.modules.org import _reminder_service as supervisor
from heinzel.modules.org._agenda import Agenda
from heinzel.modules.system.notify import NotifyService, NotifySettings

pytestmark = pytest.mark.unit

BERLIN = timezone(timedelta(hours=1))


class Clock:
    """Wall and monotonic time that only the lane's waits move."""

    def __init__(self, at: datetime) -> None:
        self.at = at
        self.mono = 0.0

    def wall(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)
        self.mono += seconds


@dataclass
class Run:
    clock: Clock
    waits: list[float]
    beats: list[dict]
    reads: list[datetime]


def run(  # noqa: PLR0913 - the seams a test turns
    clock: Clock,
    service,
    agenda,
    *,
    done_after: float = 0.0,
    interval: int = 5,
    gate=lambda: False,
    stopped=lambda: False,
    jump=None,
    startup: float = 0.0,
):
    waits: list[float] = []
    beats: list[dict] = []
    reads: list[datetime] = []
    started = clock.mono

    def wait(seconds):
        waits.append(seconds)
        clock.advance(seconds)
        if jump is not None:
            jump(clock)
        return clock.mono - started >= done_after

    def read():
        reads.append(clock.at)
        return agenda(clock.at) if callable(agenda) else agenda

    hooks = lane.LaneHooks(read=read, guard=nullcontext, gate=gate, wait=wait, beat=beats.append, stopped=stopped)
    result = lane.run_lane(
        hooks,
        service,
        interval=interval,
        clock=lane.LaneClock(wall=clock.wall, monotonic=clock.monotonic, tzset=lambda: None),
        startup=startup,
        identity={"profile": "p"},
    )
    return result, Run(clock, waits, beats, reads)


def notify(settings, clock, *, org=None, calendar=True):
    service = NotifyService(
        settings,
        NotifySettings(calendar=calendar, native=False, working_hours=org is not None),
        org or {},
        clock=clock.wall,
    )
    service.calendar_valid = None
    return service


def meeting(start: datetime) -> Agenda:
    return Agenda(
        now=start,
        next=Event(id="m", title="Planning", start=start, end=start + timedelta(minutes=30)),
        calendar="fixture",
    )


@pytest.mark.parametrize(
    ("now", "minutes", "slot"),
    [
        (datetime(2026, 11, 13, 10, 2, tzinfo=BERLIN), 5, datetime(2026, 11, 13, 10, 5, tzinfo=BERLIN)),
        (datetime(2026, 11, 13, 10, 4, 52, tzinfo=BERLIN), 5, datetime(2026, 11, 13, 10, 5, tzinfo=BERLIN)),
        (datetime(2026, 11, 13, 10, 4, 53, tzinfo=BERLIN), 5, datetime(2026, 11, 13, 10, 10, tzinfo=BERLIN)),
        (datetime(2026, 11, 13, 10, 59, 58, tzinfo=BERLIN), 60, datetime(2026, 11, 13, 12, tzinfo=BERLIN)),
        (
            datetime(2026, 11, 13, 10, 7, tzinfo=ZoneInfo("Asia/Kathmandu")),
            10,
            datetime(2026, 11, 13, 10, 10, tzinfo=ZoneInfo("Asia/Kathmandu")),
        ),
    ],
)
def test_the_target_slot_is_the_first_local_slot_whose_end_window_is_still_ahead(now, minutes, slot):
    assert lane.target_slot(now, minutes) == slot
    assert lane.last_slot(now, minutes) <= now < slot


def test_the_lane_ends_inside_the_end_window_once_the_jobs_are_done(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    result, seen = run(clock, notify(settings, clock), meeting(clock.at + timedelta(hours=3)), done_after=60)
    slot = datetime(2026, 11, 13, 10, 5, tzinfo=BERLIN)
    assert result == "window"
    assert slot - timedelta(seconds=15) <= clock.at < slot - timedelta(seconds=7.5)
    assert max(seen.waits) <= 30
    assert seen.beats[-1]["result"] == "window"
    assert seen.beats[-1]["slot"] == slot.isoformat()


def test_jobs_running_past_the_window_move_the_lane_to_the_next_slot(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    result, _ = run(clock, notify(settings, clock), meeting(clock.at + timedelta(hours=3)), done_after=7 * 60)
    assert result == "window"
    assert (
        datetime(2026, 11, 13, 10, 9, 45, tzinfo=BERLIN) <= clock.at < datetime(2026, 11, 13, 10, 9, 52, tzinfo=BERLIN)
    )


def test_a_sleep_of_an_hour_is_noticed_within_one_cycle_and_the_reminder_still_comes(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 1, tzinfo=BERLIN))
    start = datetime(2026, 11, 13, 11, 10, tzinfo=BERLIN)
    slept = []

    def sleep_once(c):
        if not slept:
            slept.append(c.at)
            c.at += timedelta(hours=1, minutes=5, seconds=30)  # wall time moves, monotonic does not

    service = notify(settings, clock)
    result, seen = run(clock, service, meeting(start), done_after=0, jump=sleep_once)
    assert result == "window"
    [notice] = service.recent()
    assert notice.title == "Meeting starts soon"
    assert notice.at <= start - timedelta(minutes=2)
    assert seen.reads[1] - slept[0] > timedelta(hours=1)


def test_the_last_cycle_delivers_a_slot_boundary_reminder_early_never_late(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 20, 5, tzinfo=BERLIN))
    start = datetime(2026, 11, 13, 10, 30, tzinfo=BERLIN)  # due 10:25, exactly the next slot
    service = notify(settings, clock)
    result, _ = run(clock, service, meeting(start), done_after=0, startup=40)
    assert result == "window"
    [notice] = service.recent()
    due = start - timedelta(minutes=5)
    assert notice.at < due
    assert due - notice.at <= timedelta(seconds=lane.MARGIN + lane.STARTUP_CAP)


def test_an_early_reminder_never_pops_up_before_working_hours(settings):
    clock = Clock(datetime(2026, 11, 13, 6, 55, 10, tzinfo=UTC))
    start = datetime(2026, 11, 13, 7, 5, tzinfo=UTC)
    service = notify(settings, clock, org={"working_hours": "07-22", "timezone": "UTC"})
    result, _ = run(clock, service, meeting(start), done_after=0, startup=30)
    assert result == "window"
    [notice] = service.recent()
    assert notice.at < datetime(2026, 11, 13, 7, tzinfo=UTC)
    assert notice.due == datetime(2026, 11, 13, 7, tzinfo=UTC)
    service.calendar_valid = lambda now: {
        item.key
        for item in agenda_plan(meeting(start), now)
        if item.due <= now + service.calendar_lookahead and now < item.expires
    }
    assert service.due(clock.at) == []
    clock.at = datetime(2026, 11, 13, 7, 0, 5, tzinfo=UTC)
    assert [item.id for item in service.due(clock.at)] == [notice.id]


def test_no_read_starts_when_too_little_time_is_left_before_the_window_end(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 4, 47, tzinfo=BERLIN))
    result, seen = run(clock, notify(settings, clock), meeting(clock.at + timedelta(hours=1)), done_after=0)
    assert result == "window"
    assert seen.reads == []


@pytest.mark.parametrize(("gate", "stopped", "result"), [(True, False, "gate"), (False, True, "stopped")])
def test_maintenance_or_sigterm_ends_the_lane_at_once(settings, gate, stopped, result):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    ended, seen = run(
        clock,
        notify(settings, clock),
        meeting(clock.at),
        done_after=999,
        gate=lambda: gate,
        stopped=lambda: stopped,
    )
    assert ended == result
    assert seen.reads == []
    assert seen.beats[-1]["result"] == result


def test_turning_reminders_off_withdraws_pending_ones_and_ends_the_lane(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    service = notify(settings, clock, calendar=False)
    ended, seen = run(clock, service, meeting(clock.at), done_after=999)
    assert ended == "disabled"
    assert seen.reads == []
    assert seen.beats[-1]["result"] == "disabled"


def test_a_busy_fence_skips_the_cycle_and_a_failing_provider_is_reported(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    service = notify(settings, clock)
    busy = lane.LaneHooks(
        read=lambda: meeting(clock.at),
        guard=Mock(side_effect=HeinzelError("busy")),
        gate=lambda: False,
        wait=lambda _s: True,
        beat=lambda _b: None,
    )
    assert lane._one_cycle(busy, service, clock.at, timedelta(0), lane.LaneClock(wall=clock.wall)) == (
        "gate",
        lane.MAX_POLL,
    )
    broken = Agenda(now=clock.at, problem="Connect a calendar", fix="hz system config")
    failing = lane.LaneHooks(
        read=lambda: broken, guard=nullcontext, gate=lambda: False, wait=lambda _s: True, beat=lambda _b: None
    )
    result, _ = lane._one_cycle(failing, service, clock.at, timedelta(0), lane.LaneClock(wall=clock.wall))
    assert result == "provider-unavailable"


def test_a_hung_read_times_out(monkeypatch, settings):
    release = threading.Event()
    monkeypatch.setattr(lane, "READ_TIMEOUT", 0.05)
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    hung = lane.LaneHooks(
        read=lambda: release.wait(5) and meeting(clock.at),
        guard=nullcontext,
        gate=lambda: False,
        wait=lambda _s: True,
        beat=lambda _b: None,
    )
    try:
        result, delay = lane._one_cycle(hung, notify(settings, clock), clock.at, timedelta(0), lane.LaneClock())
    finally:
        release.set()
    assert (result, delay) == ("provider-unavailable", lane.MAX_POLL)
    with pytest.raises(ValueError, match="boom"):
        lane._bounded(Mock(side_effect=ValueError("boom")), 1)


def test_heartbeats_change_at_most_every_30_seconds_and_come_at_least_every_minute(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    _, seen = run(clock, notify(settings, clock), meeting(clock.at + timedelta(hours=3)), done_after=60)
    stamps = [datetime.fromisoformat(beat["at"]) for beat in seen.beats]
    gaps = [later - earlier for earlier, later in pairwise(stamps)]
    assert all(gap <= timedelta(seconds=61) for gap in gaps)
    assert all(gap >= timedelta(seconds=30) for gap in gaps[:-1])
    assert {beat["profile"] for beat in seen.beats} == {"p"}


def test_the_lane_spec_round_trips_and_a_hand_started_lane_refuses():
    spec = lane.LaneSpec(fd=5, directory=lane.Path("/state/daemon"), interval=5, spawned=1.5, crashes=1)
    assert lane.LaneSpec.from_environment({lane.ENVIRONMENT: spec.environment()}) == spec
    with pytest.raises(HeinzelError, match="Only a scheduled daemon tick"):
        lane.LaneSpec.from_environment({})
    with pytest.raises(HeinzelError, match="invalid"):
        lane.LaneSpec.from_environment(
            {lane.ENVIRONMENT: json.dumps({"fd": 5, "dir": "x", "interval": 5, "spawned": 0})}
        )


def test_the_pipe_wait_returns_once_the_tick_closes_its_end():
    reader, writer = os.pipe()
    wait = lane.pipe_wait(reader)
    try:
        assert wait(0.01) is False
        os.close(writer)
        assert wait(1) is True
        assert wait(0.01) is True
    finally:
        os.close(reader)


def test_heartbeats_are_written_atomically_and_freshness_needs_this_profile(tmp_path):
    now = datetime(2026, 11, 13, 10, tzinfo=UTC)
    beat = lane.write_beat(tmp_path)
    beat({"profile": "p", "result": "ok", "at": now.isoformat()})
    assert lane.read_beat(tmp_path)["result"] == "ok"
    assert lane.fresh_lane(tmp_path, "p", now + timedelta(seconds=90))
    assert not lane.fresh_lane(tmp_path, "q", now)
    assert not lane.fresh_lane(tmp_path, "p", now + timedelta(minutes=3))
    beat({"profile": "p", "result": "stopped", "at": now.isoformat()})
    assert not lane.fresh_lane(tmp_path, "p", now)
    (tmp_path / lane.LANE_FILE).write_text("[]")
    assert lane.read_beat(tmp_path) == {}
    assert not lane.fresh_lane(tmp_path, "p", now)


def daemon(*, installed=True, recorded="0.9.42", paused=False, lane_state=None, interval=5):
    status = SimpleNamespace(installed=installed, installed_version=recorded, paused=paused, interval_minutes=interval)
    return SimpleNamespace(status=lambda: status, lane=lambda: lane_state or ReminderLaneState())


@pytest.mark.parametrize(
    ("enabled", "attestation", "state"),
    [
        (False, None, "off"),
        (True, None, "install"),
        (True, daemon(installed=False), "install"),
        (True, daemon(recorded=None), "elsewhere"),
        (True, daemon(paused=True), "resume"),
        (True, daemon(lane_state=ReminderLaneState()), "starting"),
        (True, daemon(lane_state=ReminderLaneState(started=True)), "overdue"),
        (
            True,
            daemon(
                lane_state=ReminderLaneState(
                    started=True, beat_at=datetime(2026, 11, 13, 10, 3, tzinfo=UTC), result="ok"
                )
            ),
            "on",
        ),
        (
            True,
            daemon(
                lane_state=ReminderLaneState(
                    started=True, beat_at=datetime(2026, 11, 13, 10, 3, tzinfo=UTC), result="provider-unavailable"
                )
            ),
            "provider",
        ),
    ],
)
def test_calendar_shows_one_state_and_no_worker_state(enabled, attestation, state):
    assert lane.reminder_state(enabled, attestation, datetime(2026, 11, 13, 10, 3, 30, tzinfo=UTC)) == state


def test_a_lane_just_after_a_slot_is_not_overdue_yet():
    attestation = daemon(lane_state=ReminderLaneState(started=True))
    now = datetime(2026, 11, 13, 10, 1, tzinfo=UTC)
    assert lane.reminder_state(enabled=True, daemon=attestation, now=now) == "starting"


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    """A byte-matching 0126 worker unit of the selected profile in an isolated home."""
    path = tmp_path / "LaunchAgents/worker.plist"
    executable = tmp_path / "hz"
    monkeypatch.setattr(supervisor, "path_for", lambda *_: path)
    monkeypatch.setattr(supervisor, "_shim", lambda: executable)
    monkeypatch.setattr(supervisor, "lifecycle", lambda _profile: nullcontext())
    path.parent.mkdir()
    path.write_text(supervisor.definition(supervisor.sys.platform, executable))
    path.chmod(0o644)
    calls: list[tuple[str, bool]] = []

    def manager(command):
        calls.append((command[1] if command[0] == "/bin/launchctl" else command[2], path.exists()))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(supervisor, "_run", manager)
    return SimpleNamespace(path=path, calls=calls)


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_a_worker_whose_profile_has_a_lane_unlinks_its_unit_before_leaving_the_manager(legacy, monkeypatch, platform):
    monkeypatch.setattr(supervisor.sys, "platform", platform)
    legacy.path.write_text(supervisor.definition(platform, legacy.path.parent.parent / "hz"))
    assert "worker left" in supervisor.retire_for_lane(supervisor.Profile.selected())
    assert not legacy.path.exists()
    if platform == "darwin":
        assert legacy.calls == [("bootout", False)]
    else:
        assert legacy.calls == [("disable", True), ("daemon-reload", False), ("stop", False)]


def test_a_changed_worker_unit_is_never_retired(legacy):
    legacy.path.write_text("changed by hand")
    with pytest.raises(HeinzelError, match="left alone"):
        supervisor.retire_for_lane(supervisor.Profile.selected())
    assert legacy.path.exists()
    assert legacy.calls == []


def test_migration_removes_the_old_worker_and_leaves_changed_ones_for_doctor(legacy, monkeypatch):
    monkeypatch.setattr(supervisor, "_owned", lambda path, _content: path.lstat())
    monkeypatch.setattr(supervisor, "_absent", lambda *_: True)
    assert supervisor.leftover() == legacy.path
    assert supervisor.migrate() == "removed"
    assert not legacy.path.exists()
    assert supervisor.leftover() is None
    assert supervisor.migrate() == "absent"
    legacy.path.write_text("changed")
    monkeypatch.setattr(supervisor, "_owned", Mock(side_effect=HeinzelError("changed")))
    assert supervisor.migrate() == "left"
    assert legacy.path.exists()


def test_the_worker_retires_when_a_fresh_lane_owns_its_profile(legacy, monkeypatch, tmp_path):
    from heinzel.modules import org

    worker = Mock()
    retire = Mock(return_value="left")
    monkeypatch.setattr(org, "run_reminders", worker)
    monkeypatch.setattr(org, "retire_for_lane", retire)
    monkeypatch.setattr(org, "lifecycle", lambda _profile: nullcontext())
    monkeypatch.setattr(org, "verify_binding", lambda: None)
    selected = supervisor.Profile.selected()
    lanes = lane.Path(selected.environment["HZ_STATE_DIR"]) / "daemon"
    org.calendar_reminder_worker(Mock())
    worker.assert_called_once()
    lane.write_beat(lanes)({"profile": selected.identity, "result": "ok", "at": datetime.now(UTC).isoformat()})
    assert org.calendar_reminder_worker(Mock()) == "left"
    retire.assert_called_once()
    worker.assert_called_once()


def test_a_heartbeat_without_a_zone_is_never_fresh(tmp_path):
    lane.write_beat(tmp_path)({"profile": "p", "result": "ok", "at": "2026-11-13T10:00:00"})
    assert not lane.fresh_lane(tmp_path, "p", datetime(2026, 11, 13, 10, tzinfo=UTC))


def test_doctor_names_a_leftover_worker_with_its_removal(legacy):
    from heinzel.modules import org

    [check] = org._reminder_worker()
    assert str(legacy.path) in check.detail
    assert "calendar-reminders-uninstall --confirm" in (check.fix or "")
    legacy.path.unlink()
    assert org._reminder_worker() == []


def test_a_lane_without_a_tick_refuses_and_a_second_lane_leaves(tmp_path, monkeypatch):
    from heinzel.modules import org

    monkeypatch.delenv(lane.ENVIRONMENT, raising=False)
    with pytest.raises(HeinzelError, match="Only a scheduled daemon tick"):
        org.calendar_reminder_lane(Mock())
    spec = lane.LaneSpec(fd=0, directory=tmp_path, interval=5, spawned=0.0)
    monkeypatch.setenv(lane.ENVIRONMENT, spec.environment())
    with lane.private_lock(tmp_path / "lane.lock"):
        assert org.calendar_reminder_lane(Mock()) == "duplicate"


def test_the_lane_process_wires_the_profile_fence_gate_and_pipe(tmp_path, monkeypatch, settings):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    reader, writer = os.pipe()
    os.close(writer)
    spec = lane.LaneSpec(fd=reader, directory=tmp_path, interval=5, spawned=0.0)
    monkeypatch.setenv(lane.ENVIRONMENT, spec.environment())
    monkeypatch.setattr(lane, "migrate", Mock(return_value="absent"))
    monkeypatch.setattr(lane, "stop_flag", lambda: lambda: False)
    seen = {}

    def fake_run(hooks, notifications, **kwargs):
        seen.update(kwargs, gate=hooks.gate(), done=hooks.wait(0.0), guard=hooks.guard)
        hooks.beat({"profile": kwargs["identity"]["profile"], "result": "window", "at": clock.at.isoformat()})
        return "window"

    monkeypatch.setattr(lane, "run_lane", fake_run)
    kernel = Mock()
    kernel.resolve.return_value = notify(settings, clock)
    try:
        assert lane.main(kernel, lambda: meeting(clock.at)) == "window"
    finally:
        os.close(reader)
    lane.migrate.assert_called_once()
    assert (seen["interval"], seen["gate"], seen["done"]) == (5, False, True)
    assert seen["identity"]["profile"] == supervisor.Profile.selected().identity
    assert lane.read_beat(tmp_path)["result"] == "window"


def test_sigterm_sets_the_stop_flag():
    import signal

    previous = signal.getsignal(signal.SIGTERM)
    try:
        stopped = lane.stop_flag()
        assert stopped() is False
        os.kill(os.getpid(), signal.SIGTERM)
        assert stopped() is True
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_sigterm_cuts_a_wait_short_and_the_offset_widens_the_last_lookahead(settings):
    reader, writer = os.pipe()
    try:
        started = lane.time.monotonic()
        assert lane.pipe_wait(reader, lambda: True)(5) is False
        assert lane.time.monotonic() - started < 2  # one slice at most
    finally:
        os.close(reader)
        os.close(writer)
    spec = lane.LaneSpec(fd=5, directory=lane.Path("/state/daemon"), interval=5, spawned=1.5, offset=42.0)
    assert lane.LaneSpec.from_environment({lane.ENVIRONMENT: spec.environment()}).offset == 42.0
    clock = Clock(datetime(2026, 11, 13, 10, 20, 5, tzinfo=BERLIN))
    start = datetime(2026, 11, 13, 10, 31, tzinfo=BERLIN)  # due 10:26, after a next tick 50 s past its slot
    near = notify(settings, clock)
    run(clock, near, meeting(start), done_after=0, startup=10)
    assert near.recent() == []
    clock = Clock(datetime(2026, 11, 13, 10, 20, 5, tzinfo=BERLIN))
    far = notify(settings, clock)
    original = lane.run_lane

    def with_offset(hooks, notifications, **kwargs):
        return original(hooks, notifications, **kwargs, offset=50.0)

    lane.run_lane = with_offset
    try:
        run(clock, far, meeting(start), done_after=0, startup=10)
    finally:
        lane.run_lane = original
    assert [notice.title for notice in far.recent()] == ["Meeting starts soon"]


def test_pausing_the_daemon_during_a_lane_means_no_reminder_after_the_pause(settings, tmp_path):
    clock = Clock(datetime(2026, 11, 13, 10, 0, 10, tzinfo=BERLIN))
    service = notify(settings, clock)
    start = clock.at + timedelta(minutes=7)  # its reminder falls due two minutes into the lane
    paused_at = clock.at + timedelta(minutes=1)

    def gate():
        if clock.at >= paused_at:
            (tmp_path / "paused").write_text("")  # what `hz system daemon pause` writes
        return lane.paused(tmp_path)

    result, seen = run(clock, service, meeting(start), done_after=999, gate=gate)
    assert result == "gate"
    assert seen.beats[-1]["result"] == "gate"
    assert clock.at < start - timedelta(minutes=5)
    assert service.recent() == []


def test_a_busy_fence_in_the_last_cycle_gets_one_retry(settings):
    clock = Clock(datetime(2026, 11, 13, 10, 20, 5, tzinfo=BERLIN))
    service = notify(settings, clock)
    start = datetime(2026, 11, 13, 10, 30, tzinfo=BERLIN)  # due 10:25, the next slot
    last_read = datetime(2026, 11, 13, 10, 24, 20, tzinfo=BERLIN)
    busy = [True]

    def guard():
        if clock.at >= last_read and busy:
            busy.pop()
            msg = "busy"
            raise HeinzelError(msg)
        return nullcontext()

    hooks = lane.LaneHooks(
        read=lambda: meeting(start),
        guard=guard,
        gate=lambda: False,
        wait=lambda seconds: clock.advance(seconds) or True,
        beat=lambda _b: None,
    )
    clocks = lane.LaneClock(wall=clock.wall, monotonic=clock.monotonic, tzset=lambda: None)
    assert lane.run_lane(hooks, service, interval=5, clock=clocks, startup=10) == "window"
    assert [notice.title for notice in service.recent()] == ["Meeting starts soon"]
    assert busy == []


@pytest.mark.parametrize("result", ["disabled", "stopped", "gate"])
def test_a_fresh_heartbeat_of_a_lane_that_ended_is_not_running(result):
    beat = datetime(2026, 11, 13, 10, 3, tzinfo=UTC)
    attestation = daemon(lane_state=ReminderLaneState(started=True, beat_at=beat, result=result))
    assert lane.reminder_state(enabled=True, daemon=attestation, now=beat + timedelta(seconds=30)) == "starting"
