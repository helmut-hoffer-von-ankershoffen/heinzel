"""The generic calendar provider, reminder worker and notification policy in one isolated fixture."""

from datetime import UTC, datetime, timedelta

import pytest

from heinzel import Config, Delivery, Event, Module
from heinzel.kernel._calendar_reminders import agenda_plan, cycle
from heinzel.modules.org import agenda
from heinzel.modules.system.notify import NotifyService, NotifySettings

pytestmark = pytest.mark.integration


def test_bound_provider_restarts_and_cancellation_suppresses_delivery(make_kernel, settings):
    now = datetime.now(UTC)
    start = now + timedelta(minutes=4)

    class Calendar:
        cancelled = False
        reads = 0

        def events(self, _window):
            Calendar.reads += 1
            return [
                Event(
                    id="fixture",
                    title="Fixture",
                    start=start,
                    end=start + timedelta(hours=1),
                    status="cancelled" if Calendar.cancelled else "confirmed",
                )
            ]

        def free_busy(self, people, window):
            return []

    module = Module("fixturecalendar", "Test calendar")
    module.provides("calendar", modes={"read"})(Calendar)
    kernel = make_kernel(
        builtins=True,
        fixturecalendar=module,
        config=Config(files=(), data={"capabilities": {"calendar": "fixturecalendar"}}),
    )

    def read():
        return kernel.call(agenda)

    notify = NotifyService(
        settings, NotifySettings(calendar=True, native=False, working_hours=False), {}, clock=lambda: now
    )
    notify.calendar_valid = lambda instant: {item.key for item in agenda_plan(read(), instant)}
    cycle(read, notify, now, enabled=False)
    assert Calendar.reads == 0
    cycle(read, notify, now, enabled=True)
    [pending] = notify.due()
    assert pending.title == "Meeting starts soon"
    restarted = NotifyService(
        settings, NotifySettings(calendar=True, native=False, working_hours=False), {}, clock=lambda: now
    )
    restarted.calendar_valid = notify.calendar_valid
    cycle(read, restarted, now, enabled=True)
    assert len(restarted.recent()) == 1
    Calendar.cancelled = True
    assert restarted.deliver([pending.id], Delivery.WEB) == []
    cycle(read, restarted, now, enabled=True)
    assert restarted.recent()[0].held == "withdrawn"
