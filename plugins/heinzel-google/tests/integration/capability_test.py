"""The adapters bound by configuration, used by a requester through the CLI, with a fake `gws`. Offline."""

import json
from datetime import datetime, timedelta

import pytest
from heinzel_google import Process
from typer.testing import CliRunner

from heinzel import Calendar, Config, Document, Documents, Event, EventDraft, Kernel, Module, Window
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
BOUND = Config(files=(), data={"capabilities": {"documents": "google", "calendar": "google"}})
SINCE, UNTIL = "2026-10-19T00:00:00+00:00", "2026-10-26T00:00:00+00:00"
EVENTS = ("calendar", "events", "list")


def prep() -> Module:
    """A test-only requester, as a workflow plugin would declare it: capabilities, never a vendor."""
    module = Module("prep", "Test-only one-on-one preparation.", requires=["documents:read", "calendar:read"])

    @module.operation
    def notes(documents: Documents, ref: str) -> Document:
        """Read the notes."""
        return documents.read(ref)

    @module.operation
    def week(calendar: Calendar, since: str, until: str) -> list[Event]:
        """List the week's events."""
        return calendar.events(Window(since=datetime.fromisoformat(since), until=datetime.fromisoformat(until)))

    return module


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


@pytest.fixture
def workspace(fake_gws):
    fake_gws.doc(title="1:1 notes")
    fake_gws.json(
        EVENTS,
        {
            "timeZone": "UTC",
            "items": [
                {
                    "id": "e1",
                    "summary": "1:1",
                    "start": {"dateTime": "2026-10-20T10:00:00Z"},
                    "end": {"dateTime": "2026-10-20T10:30:00Z"},
                    "attendees": [{"email": "ada@example.com", "displayName": "Ada"}],
                    "location": "https://zoom.us/j/81234567890?pwd=abc",
                }
            ],
        },
    )
    return fake_gws


def test_a_requester_reads_a_doc_through_the_bound_adapter(google_kernel, workspace, gws_path):
    code, document = hz(google_kernel(BOUND, prep=prep()), "prep", "notes", DOC_ID)
    assert code == 0, document
    assert document == {
        "ref": DOC_ID,
        "title": "1:1 notes",
        "text": "Hello\n",
        "url": f"https://docs.google.com/document/d/{DOC_ID}/edit",
        "revision": "rev-7",
    }
    assert {r.executable for r in workspace.runs} == {gws_path}


def test_a_requester_reads_events_through_the_bound_adapter(google_kernel, workspace):
    code, events = hz(google_kernel(BOUND, prep=prep()), "prep", "week", SINCE, UNTIL)
    assert code == 0, events
    [event] = events
    assert (event["title"], event["conference_id"], event["conference_url"]) == (
        "1:1",
        "81234567890",
        "https://zoom.us/j/81234567890",
    )
    assert event["attendees"] == [{"name": "Ada", "email": "ada@example.com"}]


def test_not_signed_in_fails_with_the_exact_login_command(google_kernel, fake_gws):
    denied = {"error": {"code": 401, "message": "Access denied. No credentials provided.", "reason": "authError"}}
    fake_gws.on(("drive", "files", "get"), Process(2, json.dumps(denied).encode()))
    code, output = hz(google_kernel(BOUND, prep=prep()), "prep", "notes", DOC_ID)
    assert code != 0
    assert "gws auth login --scopes" in str(output)


def test_a_link_elsewhere_fails_without_running_gws(google_kernel, fake_gws):
    code, output = hz(google_kernel(BOUND, prep=prep()), "prep", "notes", "https://evil.example/document/d/x")
    assert code != 0
    assert "not a Google Docs, Sheets or Drive link" in str(output)
    assert fake_gws.runs == []


def test_doctor_reads_only_the_local_sign_in_state(google_kernel, fake_gws):
    fake_gws.sign_out()
    kernel = google_kernel(Config(files=(), data={"capabilities": {"calendar": "google"}}))
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    checks = {c["name"]: c for c in report["checks"]["google"]}
    assert checks["gws"]["status"] == "degraded"
    assert checks["gws"]["fix"] == "hz setup google"
    assert checks["calendar"]["detail"] == "served by google"
    assert checks["documents"]["detail"].startswith("not bound")
    assert [r.arguments for r in fake_gws.runs] == [["--version"]]


def test_capability_list_shows_the_adapter_and_its_hosts(google_kernel):
    code, report = hz(google_kernel(BOUND), "system", "capability", "list")
    assert code == 0
    assert {c["name"]: c["bound"] for c in report["capabilities"]}["calendar"] == "google"
    ours = sorted((a["capability"], a["modes"], a["hosts"]) for a in report["adapters"] if a["adapter"] == "google")
    assert ours == [
        ("calendar", ["read", "write"], ["www.googleapis.com", "oauth2.googleapis.com"]),
        (
            "documents",
            ["read"],
            ["www.googleapis.com", "docs.googleapis.com", "sheets.googleapis.com", "oauth2.googleapis.com"],
        ),
        ("mail", ["read", "write"], ["gmail.googleapis.com", "oauth2.googleapis.com"]),
    ]


def test_a_project_file_can_neither_bind_nor_choose_the_program(google_kernel, workspace, tmp_path, monkeypatch):
    user = tmp_path / "user"
    user.mkdir()
    (user / "config.toml").write_text('[capabilities]\ndocuments = "google"\n', encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))
    monkeypatch.delenv("HZ_GOOGLE__GWS_PATH")
    project = tmp_path / "project"
    project.mkdir()
    evil = project / "evil-gws"
    (project / "hz.toml").write_text(
        f'[capabilities]\ndocuments = "evil"\n\n[google]\ngws_path = "{evil}"\ntimeout = 0.1\n', encoding="utf-8"
    )
    kernel = google_kernel(Config.load(project), prep=prep())
    hz(kernel, "prep", "notes", DOC_ID)
    assert all(r.executable != str(evil) for r in workspace.runs)
    assert {r.timeout for r in workspace.api_runs} <= {60.0}
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    names = [c["name"] for c in report["checks"]["system.doctor"]]
    assert "project setting google.gws_path" in names
    assert "project setting google.timeout" in names
    assert str(evil) not in json.dumps(report["checks"]["system.doctor"])


def test_drive_lists_recent_files(google_kernel, fake_gws):
    fake_gws.json(
        ("drive", "files", "list"),
        {
            "files": [
                {
                    "id": DOC_ID,
                    "name": "Notes",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-10-23T08:00:00Z",
                }
            ]
        },
    )
    code, files = hz(google_kernel(BOUND), "google", "files", "--days", "3", "--name", "Notes")
    assert code == 0, files
    assert files == [
        {
            "ref": f"https://docs.google.com/document/d/{DOC_ID}/edit",
            "name": "Notes",
            "kind": "document",
            "modified_at": "2026-10-23T08:00:00Z",
        }
    ]


def planner() -> Module:
    """A test-only requester that writes: a publish operation declares `calendar:write`, a read operation doesn't."""
    module = Module("planner", "Test-only planning.", requires=["calendar:read"])

    @module.operation(read_only=False, requires=["calendar:write"])
    def book(calendar: Calendar, title: str, start: str) -> Event:
        """Book an hour."""
        begin = datetime.fromisoformat(start)
        return calendar.create_event(EventDraft(title=title, start=begin, end=begin + timedelta(hours=1)))

    @module.operation
    def sneak(calendar: Calendar, title: str, start: str) -> Event:
        """Try to book an hour from a read operation."""
        begin = datetime.fromisoformat(start)
        return calendar.create_event(EventDraft(title=title, start=begin, end=begin + timedelta(hours=1)))

    return module


def test_only_a_publish_operation_that_declared_calendar_write_creates_an_event(google_kernel, fake_gws):
    fake_gws.json(
        ("calendar", "events", "insert"),
        {
            "id": "new1",
            "summary": "Focus",
            "start": {"dateTime": SINCE},
            "end": {"dateTime": "2026-10-19T01:00:00+00:00"},
            "organizer": {"self": True},
        },
    )
    kernel = google_kernel(BOUND, planner=planner())
    code, refused = hz(kernel, "planner", "sneak", "Focus", SINCE)
    assert code != 0
    assert "write" in str(refused)
    assert fake_gws.api_runs == []
    code, event = hz(kernel, "planner", "book", "Focus", SINCE)
    assert code == 0, event
    assert (event["id"], event["organized_by_user"]) == ("new1", True)
    (run,) = fake_gws.api_runs
    assert run.command == ("calendar", "events", "insert")
