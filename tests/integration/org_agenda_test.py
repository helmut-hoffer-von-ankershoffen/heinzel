"""`hz org agenda` through the web head (ADR 0039), the footer's source, with and without a calendar bound."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from heinzel import Config, Event, HeinzelError, Module, Window
from heinzel.heads import api, web
from heinzel.heads._exposure import exposed

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
BASE = "http://127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class Diary:
    """A test-only calendar adapter: a meeting that started ten minutes ago and one in an hour, or an error."""

    fail = False

    def events(self, window: Window) -> list[Event]:
        if Diary.fail:
            msg = "Your diary sign-in expired; run `diary login`."
            raise HeinzelError(msg)
        now = datetime.now(UTC)
        return [
            Event(id="1", title="Standup", start=now - timedelta(minutes=10), end=now + timedelta(minutes=5)),
            Event(id="2", title="Review", start=now + timedelta(hours=1), end=now + timedelta(hours=2)),
        ]


def diary() -> Module:
    module = Module("diary", "Provides a calendar for tests.")
    module.provides("calendar", modes={"read"})(Diary)
    return module


def client(make_kernel, **bindings: str) -> TestClient:
    kernel = make_kernel(builtins=True, diary=diary(), config=Config(files=(), data={"capabilities": bindings}))
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)


def test_the_web_head_exposes_the_agenda_and_the_rest_head_does_not(make_kernel):
    kernel = make_kernel(builtins=True)
    assert "org_agenda" in {item.name for item in exposed(kernel, web.HEAD)}
    assert "org_agenda" not in {item.name for item in exposed(kernel, api.HEAD)}
    assert TestClient(api.build(kernel, port=8000), base_url=BASE).get("/org/agenda").status_code == 404


def test_the_agenda_needs_the_session_token(make_kernel):
    assert client(make_kernel).get("/api/org/agenda").status_code == 401


def test_without_a_calendar_the_agenda_says_what_to_run(make_kernel):
    # `none`: the sole calendar adapter would serve it automatically (ADR 0055).
    agenda = client(make_kernel, calendar="none").get("/api/org/agenda", headers=AUTH)
    assert agenda.status_code == 200
    assert agenda.json() | {"now": None} == {
        "now": None,
        "current": None,
        "next": None,
        "calendar": None,
        "problem": "No calendar connected (calendar:read: no adapter bound).",
        "fix": "hz system config set capabilities.calendar diary",
    }


def test_with_a_calendar_the_agenda_has_the_meeting_now_and_the_next(make_kernel):
    agenda = client(make_kernel, calendar="diary").get("/api/org/agenda", headers=AUTH).json()
    assert agenda["calendar"] == "diary"
    assert agenda["problem"] is None
    assert agenda["current"]["title"] == "Standup"
    assert agenda["next"]["title"] == "Review"


def test_a_failing_calendar_degrades_to_its_message(make_kernel, monkeypatch):
    monkeypatch.setattr(Diary, "fail", True)
    agenda = client(make_kernel, calendar="diary").get("/api/org/agenda", headers=AUTH)
    assert agenda.status_code == 200
    assert agenda.json()["problem"] == "Your diary sign-in expired; run `diary login`."
