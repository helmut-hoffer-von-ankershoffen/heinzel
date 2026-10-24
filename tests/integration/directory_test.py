"""The `directory` capability through the CLI: the core's `one-on-ones` guesses and the setup seam (ADR 0024, 0028).

The calendar and the directory of record are test-only adapters; the `one-on-ones` adapter is the core's own.
"""

import json
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from heinzel import (
    Config,
    ConfirmedLines,
    Directory,
    Event,
    Kernel,
    Module,
    Participant,
    Person,
    Relation,
    ReportingDraft,
    Window,
    draft_reporting_lines,
)
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

MIRA = Participant(name="Mira", email="mira@example.com")
JONAS = Participant(name="Jonas", email="jonas@example.com")
ADA = Participant(name="Ada", email="ada@example.com")


class Agenda:
    """A calendar for tests: Mira's weekly 1:1 with Jonas (she organizes) and Ada's with Mira (Ada organizes)."""

    windows: list[Window] = []  # noqa: RUF012 - shared on purpose: the kernel builds the adapter; cleared per test

    def events(self, window: Window) -> list[Event]:
        self.windows.append(window)
        start = window.until - timedelta(days=28)
        return [
            *(
                Event(
                    id=f"j{week}",
                    title="Mira / Jonas",
                    start=start + timedelta(weeks=week),
                    end=start + timedelta(weeks=week, minutes=30),
                    organizer=MIRA,
                    attendees=[MIRA, JONAS],
                )
                for week in range(4)
            ),
            *(
                Event(
                    id=f"a{week}",
                    title="Ada <> Mira",
                    start=start + timedelta(weeks=2 * week, hours=2),
                    end=start + timedelta(weeks=2 * week, hours=3),
                    organizer=ADA,
                    attendees=[ADA, MIRA],
                )
                for week in range(3)
            ),
        ]


class Records:
    """A directory of record for tests, as an HR system plugin would bring: it knows Mira's manager, not her reports."""

    def person(self, person: str) -> Person | None:
        if person != "mira":
            return None
        return Person(who=MIRA, login="mira", title="Engineering manager", manager=Relation(who=ADA, login="ada"))

    def reports(self, person: str) -> list[Person]:
        return []


def roster() -> Module:
    """What the roster code does: declare `directory:read` and call the seam."""
    module = Module("roster", "Drafts a team's roster.", requires=["directory:read"])

    @module.operation
    def draft(directory: Directory, person: str, manager: str | None = None) -> ReportingDraft:
        """Draft a person's reporting lines."""
        return draft_reporting_lines(directory, person, ConfirmedLines(manager=manager) if manager else None)

    return module


def modules(*, records: bool = False) -> dict[str, Module]:
    agenda = Module("agenda", "Test-only calendar.")
    agenda.provides("calendar", modes={"read"}, hosts=["calendar.invalid"])(Agenda)
    found = {"agenda": agenda, "roster": roster()}
    if records:
        hr = Module("records", "Test-only directory of record.")
        hr.provides("directory", modes={"read"}, hosts=["hr.invalid"])(Records)
        found["records"] = hr
    return found


@pytest.fixture(autouse=True)
def _fresh_agenda():
    Agenda.windows.clear()


def hz(kernel: Kernel, *args: str) -> tuple[int, dict | list | str]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    try:
        return result.exit_code, json.loads(result.output)
    except json.JSONDecodeError:
        return result.exit_code, result.output


def bound(**capabilities: str) -> Config:
    return Config(files=(), data={"capabilities": capabilities})


def test_by_default_reporting_lines_are_guessed_from_one_on_ones_and_marked(make_kernel):
    kernel = make_kernel(builtins=True, config=bound(calendar="agenda"), **modules())
    code, draft = hz(kernel, "roster", "draft", "Mira@Example.com")
    assert code == 0, draft
    assert draft["needs_confirmation"] is True
    assert draft["problem"] is None
    assert draft["manager"]["who"] == {"name": "Ada", "email": "ada@example.com"}
    assert draft["manager"]["guess"] is True
    assert draft["manager"]["evidence"] == ["a recurring 1:1 every two weeks in your calendar, organized by Ada"]
    (report,) = draft["reports"]
    assert report["who"]["name"] == "Jonas"
    assert report["guess"] is True
    assert report["evidence"] == ["a recurring 1:1 weekly in your calendar, organized by Mira"]
    (window,) = Agenda.windows
    assert window.until - window.since == timedelta(days=56)


def test_a_confirmed_manager_replaces_the_guess_and_is_named_from_the_directory(make_kernel):
    kernel = make_kernel(builtins=True, config=bound(calendar="agenda"), **modules())
    code, draft = hz(kernel, "roster", "draft", "mira@example.com", "--manager", "jonas@example.com")
    assert code == 0, draft
    assert draft["manager"] == {
        "who": {"name": "Jonas", "email": "jonas@example.com"},
        "login": None,
        "guess": False,
        "evidence": [],
    }
    assert draft["needs_confirmation"] is True  # the report is still a guess
    assert len(Agenda.windows) == 1  # Jonas was named from the draft, not by reading the calendar again


def test_without_a_calendar_the_draft_says_why_instead_of_failing(make_kernel):
    kernel = make_kernel(builtins=True, **modules())
    code, draft = hz(kernel, "roster", "draft", "mira@example.com", "--manager", "ada")
    assert code == 0, draft
    assert "calendar:read" in draft["problem"]
    assert draft["manager"] == {"who": {"name": None, "email": None}, "login": "ada", "guess": False, "evidence": []}
    assert draft["reports"] == []
    code, report = hz(kernel, "system", "doctor")
    checks = {c["name"]: c["status"] for c in report["checks"]["system.capability"]}
    assert checks["directory:read for roster"] == "up"
    assert checks["calendar:read for system.directory"] == "degraded"


def test_a_directory_of_record_wins_and_nothing_is_a_guess(make_kernel):
    kernel = make_kernel(builtins=True, config=bound(calendar="agenda", directory="records"), **modules(records=True))
    code, draft = hz(kernel, "roster", "draft", "mira", "--manager", "jonas@example.com")
    assert code == 0, draft
    assert draft["found"]["title"] == "Engineering manager"
    assert draft["manager"]["login"] == "ada"
    assert draft["needs_confirmation"] is False
    assert draft["conflicts"] == ["manager: the directory says Ada; you confirmed jonas@example.com"]
    assert Agenda.windows == []
    code, report = hz(kernel, "system", "capability", "list")
    assert [u for u in report["uses"] if u["requester"] == "system.directory"] == []


def test_doctor_names_the_binding_when_a_plugin_could_serve_the_directory(make_kernel):
    kernel = make_kernel(builtins=True, config=bound(calendar="agenda"), **modules(records=True))
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    checks = {c["name"]: c for c in report["checks"]["system.capability"]}
    assert checks["binding directory"]["status"] == "degraded"
    assert (
        checks["binding directory"]["detail"]
        == "directory is served by the default, one-on-ones; records could serve it"
    )
    assert checks["binding directory"]["fix"].endswith("hz system config set capabilities.directory records")
    kernel = make_kernel(
        builtins=True, config=bound(calendar="agenda", directory="one-on-ones"), **modules(records=True)
    )
    code, report = hz(kernel, "system", "doctor")
    assert "binding directory" not in {c["name"] for c in report["checks"]["system.capability"]}


def test_the_fallbacks_calendar_need_is_no_finding_while_nobody_uses_a_directory(make_kernel):
    kernel = make_kernel(builtins=True)
    code, report = hz(kernel, "system", "capability", "list")
    assert code == 0
    assert [u for u in report["uses"] if u["requester"] == "system.directory"] == []
    assert {a["adapter"]: a["module"] for a in report["adapters"]}["one-on-ones"] == "system.directory"
