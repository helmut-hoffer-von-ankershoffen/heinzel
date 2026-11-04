"""Calendar reminder boundary and identity planning."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from heinzel import Event
from heinzel.kernel._calendar_reminders import plan

pytestmark = pytest.mark.unit


def event(name, start, end, **fields):
    return Event(id=name, title=name, start=start, end=end, **fields)


def test_current_and_next_ignore_cancelled_all_day_declined_and_choose_overlaps():
    now = datetime(2026, 10, 21, 10, tzinfo=UTC)
    events = [
        event("earlier", now - timedelta(hours=1), now + timedelta(minutes=10)),
        event("current", now - timedelta(minutes=1), now + timedelta(minutes=7)),
        event("next", now + timedelta(minutes=8), now + timedelta(hours=1)),
        event("cancelled", now + timedelta(minutes=1), now + timedelta(hours=1), status="cancelled"),
        event("declined", now + timedelta(minutes=2), now + timedelta(hours=1), response="declined"),
        event("all day", now, now + timedelta(days=1), all_day=True),
    ]
    current, upcoming = plan(events, now)
    assert (current.title, current.kind, current.due, current.expires) == (
        "current",
        "end",
        now + timedelta(minutes=2),
        now + timedelta(minutes=7),
    )
    assert (upcoming.title, upcoming.kind, upcoming.due) == ("next", "start", now + timedelta(minutes=3))
    assert plan(list(reversed(events)), now) == [current, upcoming]


def test_moving_changes_key_and_daylight_saving_uses_actual_instant():
    zone = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 10, 25, 0, 55, tzinfo=UTC)
    start = datetime(2026, 10, 25, 2, 3, tzinfo=zone, fold=1)
    entry = event("meeting", start, start + timedelta(hours=1))
    original = plan([entry], now)[0]
    assert original.due == datetime(2026, 10, 25, 0, 58, tzinfo=UTC)
    moved = event("meeting", start + timedelta(minutes=1), start + timedelta(hours=1))
    assert plan([moved], now)[0].key != original.key
    extended = event("meeting", start, start + timedelta(hours=2))
    assert plan([extended], now)[0].key == original.key
    assert plan([entry.model_copy(update={"status": "cancelled"})], now) == []


def test_provider_refresh_consent_dedup_and_withdrawal(settings):
    from heinzel import NoticeKind
    from heinzel.kernel._calendar_reminders import agenda_plan, cycle
    from heinzel.modules.org._agenda import Agenda
    from heinzel.modules.system.notify import NotifyService, NotifySettings

    class Clock:
        now = datetime(2026, 10, 21, 10, tzinfo=UTC)

        def __call__(self):
            return self.now

    clock = Clock()
    start = clock.now + timedelta(minutes=6)
    meeting = event("meeting", start, start + timedelta(hours=1))
    agenda = Agenda(now=clock.now, next=meeting, calendar="fixture")
    reads = []

    def read():
        reads.append(1)
        return agenda

    service = NotifyService(settings, NotifySettings(calendar=True, native=False, working_hours=False), {}, clock=clock)
    service.calendar_valid = lambda now: {item.key for item in agenda_plan(read(), now)}
    assert cycle(read, service, clock.now, enabled=False) == timedelta(seconds=30)
    assert reads == []
    assert cycle(read, service, clock.now, enabled=True) == timedelta(seconds=30)
    clock.now += timedelta(minutes=1)
    cycle(read, service, clock.now, enabled=True)
    assert len(service.due()) == 1
    restarted = NotifyService(
        settings, NotifySettings(calendar=True, native=False, working_hours=False), {}, clock=clock
    )
    restarted.calendar_valid = service.calendar_valid
    cycle(read, restarted, clock.now, enabled=True)
    assert len(restarted.recent()) == 1
    assert restarted.recent()[0].kind is NoticeKind.CALENDAR
    agenda = Agenda(now=clock.now, calendar="fixture", next=meeting.model_copy(update={"status": "cancelled"}))
    assert restarted.due() == []
    cycle(read, restarted, clock.now, enabled=True)
    assert restarted.recent()[0].held == "withdrawn"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_service_install_and_uninstall_are_isolated_and_refuse_foreign_units(tmp_path, monkeypatch, platform):
    import subprocess
    from types import SimpleNamespace

    from heinzel import HeinzelError
    from heinzel.modules.org import _reminder_service as service

    path = service.path_for(platform, tmp_path)
    executable = tmp_path / "hz"
    commands = []
    monkeypatch.setattr(service, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(service, "path_for", lambda *_: path)
    monkeypatch.setattr(service, "_shim", lambda: executable)

    def run(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "state = running", "")

    monkeypatch.setattr(service, "_run", run)
    assert "installed" in service.install()
    assert path.read_text() == service.definition(platform, executable)
    assert "already running" in service.install()
    assert "removed" in service.uninstall()
    assert not path.exists()
    path.write_text("foreign unit")
    with pytest.raises(HeinzelError):
        service.uninstall()
    assert path.read_text() == "foreign unit"
    assert commands


def test_supervised_worker_waits_for_exact_deadline_and_rechecks_consent(settings):
    from heinzel import NoticeKind
    from heinzel.kernel._calendar_reminders import agenda_plan
    from heinzel.modules.org._agenda import Agenda
    from heinzel.modules.org._reminder_worker import run
    from heinzel.modules.system.notify import NotifyService, NotifySettings

    base = datetime(2026, 10, 21, 10, tzinfo=UTC)
    elapsed = 0.0
    reads = []
    start = base + timedelta(minutes=5, seconds=17)
    meeting = event("fixture", start, start + timedelta(minutes=30))
    service = NotifyService(
        settings,
        NotifySettings(calendar=True, native=False, working_hours=False),
        {},
        clock=lambda: base + timedelta(seconds=elapsed),
    )

    def read():
        reads.append(elapsed)
        return Agenda(now=base + timedelta(seconds=elapsed), next=meeting, calendar="fixture")

    service.calendar_valid = lambda now: {item.key for item in agenda_plan(read(), now)}

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    run(read, service, monotonic=lambda: elapsed, sleep=sleep, clock=lambda: base + timedelta(seconds=elapsed))
    [notice] = service.recent()
    assert notice.kind is NoticeKind.CALENDAR
    assert notice.at == base + timedelta(seconds=17)
    assert 17 in reads


def test_worker_hands_one_valid_reminder_to_the_native_notifier(settings, monkeypatch):
    from unittest.mock import Mock

    from heinzel.kernel._calendar_reminders import agenda_plan, cycle
    from heinzel.modules.org._agenda import Agenda
    from heinzel.modules.system.notify import NotifyService, NotifySettings
    from heinzel.modules.system.notify._native import Native

    now = datetime(2026, 10, 21, 10, tzinfo=UTC)
    meeting = event("fixture", now + timedelta(minutes=5), now + timedelta(minutes=30))
    agenda = Agenda(now=now, next=meeting, calendar="fixture")
    native = Mock(spec=Native)
    native.name = "fixture"
    native.clicks = False
    native.post.return_value = None
    service = NotifyService(
        settings, NotifySettings(calendar=True, working_hours=False), {}, clock=lambda: now, notifier=native
    )
    service.calendar_valid = lambda instant: {item.key for item in agenda_plan(agenda, instant)}
    monkeypatch.setattr(service, "_end_leftover_instances", lambda: None)
    cycle(lambda: agenda, service, now, enabled=True)
    cycle(lambda: agenda, service, now, enabled=True)
    assert native.post.call_count == 1
    assert native.post.call_args.args[0] == "Meeting starts soon"
