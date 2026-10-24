"""`hz calendar` through the CLI, MCP's exposure and the web head, with a test-only calendar that reads and writes."""

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from heinzel import (
    Config,
    Event,
    EventCancellation,
    EventChange,
    EventDraft,
    FreeBusy,
    HeinzelError,
    Kernel,
    Module,
    NoticeKind,
    Notifications,
    Participant,
    TimeSpan,
    Window,
)
from heinzel.heads import api, mcp, web
from heinzel.heads._exposure import MCP_HTTP, exposed
from heinzel.heads.cli import build
from heinzel.modules.calendar import remind
from heinzel.modules.system.notify._watch import remind as tick_remind

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
ADA = Participant(name="Ada", email="ada@example.com")
GRACE = Participant(name="Grace", email="grace@example.com")
ORG = {"timezone": "UTC", "working_hours": [9, 17]}


class Diary:
    """A calendar in memory: a standup now, a 1:1 you were invited to soon, and every write it was asked for."""

    created: ClassVar[list[EventDraft]] = []
    changed: ClassVar[list[tuple[str, EventChange]]] = []
    cancelled: ClassVar[list[str]] = []
    answered: ClassVar[list[tuple[str, str]]] = []

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC).replace(second=0, microsecond=0)

    def events(self, window: Window) -> list[Event]:
        now = self.now()
        found = [
            Event(
                id="standup",
                title="Standup",
                start=now - timedelta(minutes=5),
                end=now + timedelta(minutes=12),
                organizer=ADA,
                attendees=[ADA, GRACE],
                organized_by_user=True,
                conference_url="https://meet.google.com/abc-defg-hij",
            ),
            Event(
                id="one-on-one",
                title="1:1",
                start=now + timedelta(minutes=25),
                end=now + timedelta(minutes=55),
                organizer=GRACE,
                attendees=[GRACE, ADA],
                response="pending",
            ),
        ]
        return [e for e in found if e.start < window.until and e.end > window.since]

    def free_busy(self, people: str, window: Window) -> list[FreeBusy]:
        busy = [TimeSpan(start=e.start, end=e.end) for e in self.events(window)]
        return [
            FreeBusy(who=Participant(), busy=busy),
            *(FreeBusy(who=Participant(email=p), known=False) for p in people.split(",") if p),
        ]

    def create_event(self, event: EventDraft) -> Event:
        self.created.append(event)
        return Event(id="new", title=event.title, start=event.start, end=event.end, organized_by_user=True)

    def update_event(self, event: str, change: EventChange) -> Event:
        self.changed.append((event, change))
        return next(
            e
            for e in self.events(Window(since=self.now() - timedelta(days=1), until=self.now() + timedelta(days=1)))
            if e.id == event
        )

    def cancel_event(self, event: str, notify: bool) -> EventCancellation:
        self.cancelled.append(event)
        return EventCancellation(id=event, notified=notify)

    def respond(self, event: str, response: str) -> Event:
        self.answered.append((event, response))
        return Event(id=event, title="1:1", start=self.now(), end=self.now(), response=response)  # type: ignore[arg-type]


def with_diary(make_kernel, **calendar: object) -> Kernel:
    for writes in (Diary.created, Diary.changed, Diary.cancelled, Diary.answered):
        writes.clear()
    diary = Module("diary", "A calendar for tests.")
    diary.provides("calendar", modes={"read", "write"})(Diary)
    config = Config(files=(), data={"capabilities": {"calendar": "diary"}, "org": ORG, "calendar": calendar})
    return make_kernel(builtins=True, diary=diary, config=config)


@pytest.fixture
def kernel(make_kernel) -> Kernel:
    return with_diary(make_kernel)


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def test_hz_calendar_lists_today_with_your_answer_and_the_join_link(kernel):
    code, today = hz(kernel, "calendar")
    assert code == 0, today
    standup = next(e for e in today["events"] if e["id"] == "standup")  # the 1:1 may be tomorrow, near midnight
    assert standup["conference_url"] == "https://meet.google.com/abc-defg-hij"
    code, week = hz(kernel, "calendar", "week")
    assert code == 0
    assert (datetime.fromisoformat(week["until"]) - datetime.fromisoformat(week["since"])).days == 7


def test_now_counts_down_to_the_end_of_the_meeting_and_to_the_next(kernel):
    code, countdown = hz(kernel, "calendar", "now")
    assert code == 0, countdown
    assert countdown["line"].startswith("Standup · ends in ")
    assert "next: 1:1 in " in countdown["line"]


def test_without_a_calendar_now_says_how_to_connect_one(make_kernel):
    kernel = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"calendar": "none"}}))
    code, countdown = hz(kernel, "calendar", "now")
    assert code == 0
    assert countdown["line"] == "No calendar connected"
    assert countdown["fix"] is not None


def test_a_new_event_is_only_shown_until_it_is_confirmed(kernel):
    code, shown = hz(kernel, "calendar", "new", "Plan", "tomorrow 10:00", "--people", "grace@example.com")
    assert code == 0, shown
    assert shown["done"] is False
    assert shown["summary"].startswith("Would create 'Plan' on ")
    assert "inviting grace@example.com" in shown["summary"]
    assert shown["confirm"].endswith("--confirm")
    assert Diary.created == []
    code, done = hz(kernel, "calendar", "new", "Plan", "tomorrow 10:00", "--confirm")
    assert code == 0, done
    assert done["done"] is True
    (draft,) = Diary.created
    assert (draft.title, draft.end - draft.start) == ("Plan", timedelta(minutes=30))


def test_the_confirm_command_names_the_time_it_showed_not_the_words_typed(kernel):
    code, shown = hz(kernel, "calendar", "new", "Plan", "tomorrow 10:00", "--video", "--no-notify")
    assert code == 0, shown
    tomorrow = (Diary.now() + timedelta(days=1)).date().isoformat()
    assert f"Plan {tomorrow}T10:00+00:00 --minutes 30 --video --no-notify --confirm" in shown["confirm"]


def test_without_a_calendar_a_write_says_how_to_connect_one_before_showing_anything(make_kernel):
    kernel = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"calendar": "none"}}))
    code, output = hz(kernel, "calendar", "new", "Plan", "10:00")
    assert code != 0
    assert "Nothing writes your calendar" in str(output)


def test_a_bad_address_or_time_is_refused_before_anything_is_written(kernel):
    code, output = hz(kernel, "calendar", "new", "Plan", "soon", "--confirm")
    assert code != 0
    assert "no time hz reads" in str(output)
    code, output = hz(kernel, "calendar", "new", "Plan", "10:00", "--people", "Grace <g@x.org>", "--confirm")
    assert code != 0
    assert "is not an email address" in str(output)
    assert Diary.created == []


def test_moving_cancelling_and_answering_are_shown_then_done(kernel):
    code, shown = hz(kernel, "calendar", "move", "standup", "tomorrow 9:30")
    assert (code, shown["done"], Diary.changed) == (0, False, [])
    code, _ = hz(kernel, "calendar", "move", "standup", "tomorrow 9:30", "--confirm")
    assert code == 0
    ((moved, change),) = Diary.changed
    assert moved == "standup"
    assert change.start is not None
    assert (change.start.hour, change.start.minute) == (9, 30)
    code, shown = hz(kernel, "calendar", "cancel", "standup")
    assert "telling 1 guest(s)" in shown["summary"]
    assert Diary.cancelled == []
    hz(kernel, "calendar", "cancel", "standup", "--confirm")
    assert Diary.cancelled == ["standup"]
    code, shown = hz(kernel, "calendar", "respond", "one-on-one", "declined")
    assert "telling Grace" in shown["summary"]
    hz(kernel, "calendar", "respond", "one-on-one", "declined", "--confirm")
    assert Diary.answered == [("one-on-one", "declined")]


def test_someone_elses_event_cant_be_moved_and_your_own_cant_be_answered(kernel):
    code, output = hz(kernel, "calendar", "move", "one-on-one", "10:00", "--confirm")
    assert code != 0
    assert "You don't organize '1:1'" in str(output)
    code, output = hz(kernel, "calendar", "respond", "standup", "accepted", "--confirm")
    assert code != 0
    assert (Diary.changed, Diary.answered) == ([], [])


def test_free_time_lies_between_meetings_and_hidden_calendars_are_named(kernel):
    code, found = hz(kernel, "calendar", "free", "--on", "2099-01-05", "--people", "zoe@example.com")
    assert code == 0, found
    assert found["unknown"] == ["zoe@example.com"]
    # Nothing in the diary that far ahead: the whole working day, in the profile's zone (UTC), 9 to 17.
    assert [(s["start"], s["end"]) for s in found["slots"]] == [("2099-01-05T09:00:00Z", "2099-01-05T17:00:00Z")]


def test_search_and_show_find_events_by_words_and_id(kernel):
    code, found = hz(kernel, "calendar", "search", "grace")
    assert code == 0
    assert [e["id"] for e in found["events"]] == ["standup", "one-on-one"]
    code, shown = hz(kernel, "calendar", "show", "one-on-one")
    assert (code, shown["event"]["title"]) == (0, "1:1")
    code, output = hz(kernel, "calendar", "show", "nope")
    assert code != 0
    assert "No event 'nope'" in str(output)


def test_reads_are_tools_and_web_endpoints_but_never_rest_and_writes_skip_mcp_over_http(kernel):
    names = {head: {item.name for item in exposed(kernel, head)} for head in ("mcp", MCP_HTTP, web.HEAD, api.HEAD)}
    reads = {"calendar", "calendar_week", "calendar_show", "calendar_search", "calendar_free", "calendar_now"}
    writes = {"calendar_new", "calendar_move", "calendar_cancel", "calendar_respond"}
    assert reads | writes <= names["mcp"]
    assert reads | writes <= names[web.HEAD]
    assert reads <= names[MCP_HTTP]
    assert not writes & names[MCP_HTTP]
    assert not (reads | writes) & names[api.HEAD]
    tools = {tool.name: tool for tool in mcp.build(kernel)._tool_manager.list_tools()}
    assert tools["calendar_new"].annotations is not None
    assert tools["calendar_new"].annotations.read_only_hint is False


def test_the_web_head_serves_the_countdown_behind_its_token(kernel):
    client = TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url="http://127.0.0.1:8000")
    assert client.get("/api/calendar/now").status_code == 401
    countdown = client.get("/api/calendar/now", headers=AUTH)
    assert countdown.status_code == 200
    assert countdown.json()["current"]["id"] == "standup"


class Inbox(Notifications):
    """Notifications in memory."""

    def __init__(self) -> None:
        self.emitted: list[dict[str, object]] = []

    def emit(self, kind, title, body="", *, route, command, key, urgent=False):  # noqa: PLR0913 - the base's shape
        self.emitted.append({"title": title, "body": body, "route": route, "key": key, "urgent": urgent})


def test_the_reminder_job_tells_you_of_the_meeting_that_starts_soon_with_its_link(make_kernel):
    inbox = Inbox()
    kernel = with_diary(make_kernel, remind_minutes=30)  # the 1:1 starts in 25
    kernel.bind(Notifications, inbox)
    assert kernel.call(remind).reminded == 1
    assert kernel.call(remind).reminded == 1  # again at the next tick: the same key, which notify drops as a repeat
    first, again = inbox.emitted
    assert first["key"] == again["key"]
    assert first["urgent"] is True  # about a moment: not held until working hours
    assert first["body"] == ""  # no join link and no location
    notice = first
    assert str(notice["title"]).startswith("In ")
    assert str(notice["title"]).endswith(" min: 1:1")
    assert notice["route"] == "/calendar?event=one-on-one"


def test_the_reminder_job_does_nothing_when_turned_off(make_kernel):
    inbox = Inbox()
    kernel = with_diary(make_kernel, remind_minutes=0)
    kernel.bind(Notifications, inbox)
    assert kernel.call(remind).model_dump() == {"reminded": 0, "skipped": "reminders are off"}
    assert inbox.emitted == []


def test_the_reminder_does_nothing_without_a_calendar(make_kernel):
    inbox = Inbox()
    kernel = make_kernel(builtins=True, config=Config(files=(), data={"capabilities": {"calendar": "none"}}))
    kernel.bind(Notifications, inbox)
    assert kernel.call(remind).model_dump() == {"reminded": 0, "skipped": "no calendar"}
    assert inbox.emitted == []


def test_a_failing_calendar_never_breaks_the_countdown(kernel, monkeypatch):
    def broken(self, window):
        msg = "Your diary sign-in expired; run `diary login`."
        raise HeinzelError(msg)

    monkeypatch.setattr(Diary, "events", broken)
    code, countdown = hz(kernel, "calendar", "now")
    assert code == 0
    assert countdown["problem"] == "Your diary sign-in expired; run `diary login`."


def test_a_binding_that_cant_be_used_says_how_to_repair_it(make_kernel):
    config = Config(files=(), data={"capabilities": {"calendar": "nowhere"}})
    code, countdown = hz(make_kernel(builtins=True, config=config), "calendar", "now")
    assert code == 0
    assert countdown["line"] == "Calendar unavailable"
    assert "nowhere" in countdown["problem"]


def test_the_notify_tick_runs_the_reminder_only_while_meeting_notices_are_on(make_kernel):
    inbox = Inbox()
    kernel = with_diary(make_kernel, remind_minutes=30)
    kernel.bind(Notifications, inbox)
    assert tick_remind(kernel, set()) == 0
    assert inbox.emitted == []
    assert tick_remind(kernel, {NoticeKind.MEETING}) == 1
    assert [notice["route"] for notice in inbox.emitted] == ["/calendar?event=one-on-one"]
