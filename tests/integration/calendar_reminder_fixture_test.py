"""The generic calendar provider, reminder worker and notification policy in one isolated fixture."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from heinzel import Config, Delivery, Event, Module, Notifications
from heinzel.heads import web
from heinzel.kernel._calendar_reminders import cycle
from heinzel.modules.org import agenda
from heinzel.modules.system.notify import NotifyService, NotifySettings
from heinzel.modules.system.notify._service import _calendar_keys

pytestmark = pytest.mark.integration


def test_back_to_back_meetings_share_a_deadline_and_recheck_a_rescheduled_next_meeting(
    make_kernel, settings, monkeypatch
):
    from heinzel.modules import org

    class Clock:
        now = datetime(2026, 11, 4, 10, 24, 59, tzinfo=UTC)

    clock = Clock()
    boundary = datetime(2026, 11, 4, 10, 30, tzinfo=UTC)

    class Calendar:
        moved = False

        def events(self, _window):
            next_start = boundary + timedelta(minutes=45 if Calendar.moved else 0)
            return [
                Event(id="current", title="Design review", start=boundary - timedelta(minutes=30), end=boundary),
                Event(id="next", title="Planning", start=next_start, end=next_start + timedelta(minutes=30)),
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
    monkeypatch.setattr(org, "local_now", lambda _settings: clock.now)
    client = TestClient(
        web.build(kernel, port=8000, token="fixture-token", static=None), base_url="http://127.0.0.1:8000"
    )
    assert client.get("/api/org/agenda").status_code == 401
    response = client.get("/api/org/agenda", headers={"Authorization": "Bearer fixture-token"})
    assert response.status_code == 200
    assert response.json()["current"]["id"] == "current"
    assert response.json()["next"]["id"] == "next"

    def service():
        notify = NotifyService(
            settings, NotifySettings(calendar=True, native=False, working_hours=False), {}, clock=lambda: clock.now
        )
        notify.calendar_valid = lambda instant: _calendar_keys(kernel, instant)
        return notify

    def read():
        return kernel.call(agenda)

    notify = service()
    cycle(read, notify, clock.now, enabled=True)
    assert notify.recent() == []
    clock.now += timedelta(seconds=1)
    cycle(read, notify, clock.now, enabled=True)
    assert {notice.title for notice in notify.due()} == {"Meeting ends soon", "Meeting starts soon"}
    assert {notice.at for notice in notify.recent()} == {boundary - timedelta(minutes=5)}

    restarted = service()
    cycle(read, restarted, clock.now, enabled=True)
    assert len(restarted.recent()) == 2
    end_notice = next(notice for notice in restarted.recent() if notice.title == "Meeting ends soon")
    start_notice = next(notice for notice in restarted.recent() if notice.title == "Meeting starts soon")
    Calendar.moved = True
    assert restarted.deliver([start_notice.id], Delivery.WEB) == []
    delivered = restarted.deliver([end_notice.id], Delivery.WEB)
    assert [notice.id for notice in delivered] == [end_notice.id]
    cycle(read, restarted, clock.now, enabled=True)
    assert next(notice for notice in restarted.recent() if notice.id == start_notice.id).held == "withdrawn"
    assert len(restarted.recent()) == 2


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
    notify.calendar_valid = lambda instant: _calendar_keys(kernel, instant)
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


def test_web_status_is_authenticated_and_reports_worker_state(make_kernel, settings, monkeypatch):
    from heinzel.modules import org

    kernel = make_kernel(builtins=True)
    kernel.bind(Notifications, NotifyService(settings, NotifySettings(calendar=True), {}, notifier=None))
    monkeypatch.setattr(org, "reminder_service_status", lambda: "not installed")
    client = TestClient(
        web.build(kernel, port=8000, token="fixture-token", static=None), base_url="http://127.0.0.1:8000"
    )
    assert client.get("/api/org/calendar-reminders-status").status_code == 401
    response = client.get("/api/org/calendar-reminders-status", headers={"Authorization": "Bearer fixture-token"})
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "service": "not installed"}


def test_web_setup_requires_authentication_and_confirmation_and_is_not_public(make_kernel, monkeypatch):
    from unittest.mock import Mock

    from heinzel.heads import api
    from heinzel.modules import org

    kernel = make_kernel(builtins=True)
    install = Mock(return_value="installed")
    monkeypatch.setattr(org, "install_reminders", install)
    client = TestClient(
        web.build(kernel, port=8000, token="fixture-token", static=None), base_url="http://127.0.0.1:8000"
    )
    path = "/api/org/calendar-reminders-install"
    assert client.post(path + "?confirm=true").status_code == 401
    headers = {"Authorization": "Bearer fixture-token"}
    assert client.post(path, headers=headers).status_code == 400
    install.assert_not_called()
    assert client.post(path + "?confirm=true", headers=headers).status_code == 200
    install.assert_called_once()
    public = TestClient(api.build(kernel), base_url="http://127.0.0.1")
    assert public.post("/org/calendar-reminders-install?confirm=true").status_code == 404
