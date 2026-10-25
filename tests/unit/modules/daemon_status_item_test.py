"""The daemon in the status bar (ADR 0056): each state, its tone and the buttons; installing stays the person's act."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import LiveTime, RunCommand, RunOperation, __version__
from heinzel.modules.system.daemon._models import DaemonStatus
from heinzel.modules.system.daemon._status_item import status_item

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 26, 10, 0, tzinfo=UTC)


def daemon(**fields: object) -> DaemonStatus:
    values: dict[str, object] = {
        "platform": "darwin",
        "installed": True,
        "loaded": True,
        "paused": False,
        "interval_minutes": 5,
        "executable": Path("/usr/local/bin/hz"),
        "last_tick": NOW - timedelta(minutes=2),
        "jobs": 3,
        "failing": [],
        "version": __version__,
    }
    return DaemonStatus.model_validate(values | fields)


def labels(item) -> list[str]:
    return [action.label for action in item.actions]


def test_running_says_when_it_ticks_next():
    item = status_item(daemon(), NOW)
    assert (item.text, item.tone, labels(item)) == ("Daemon running · next tick", "ok", ["Logs", "Pause", "Uninstall"])
    assert item.live == LiveTime(at=NOW + timedelta(minutes=3))
    assert [line.text for line in item.detail] == ["Jobs: 3", f"Daemon version {__version__}", "Last tick 2 min ago"]


def test_paused_warns_and_offers_resume():
    item = status_item(daemon(paused=True), NOW)
    assert (item.text, item.tone, labels(item)) == ("Daemon paused", "warn", ["Resume", "Logs", "Uninstall"])
    resume = item.actions[0].effect
    assert isinstance(resume, RunOperation)
    assert resume.operation == "resume"


def test_not_installed_offers_install_as_a_command_the_person_runs():
    item = status_item(daemon(installed=False, loaded=False, last_tick=None), NOW)
    assert (item.text, item.tone, labels(item)) == ("Daemon not installed", "neutral", ["Install"])
    install = item.actions[0].effect
    assert isinstance(install, RunCommand)
    assert install.command == "hz system daemon install"
    assert install.runs is False


@pytest.mark.parametrize(
    ("fields", "text"),
    [
        ({"failing": ["weekly-report", "standup"]}, "Daemon failing: 2 of 3 jobs"),
        ({"last_tick": NOW - timedelta(minutes=11)}, "Daemon not ticking"),
        ({"loaded": False}, "Daemon not loaded"),
    ],
)
def test_problems_are_bad_and_offer_the_logs(fields, text):
    item = status_item(daemon(**fields), NOW)
    assert (item.text, item.tone, labels(item)) == (text, "bad", ["Logs", "Pause", "Uninstall"])


def test_failing_jobs_are_listed():
    item = status_item(daemon(failing=["weekly-report"]), NOW)
    assert ("Failing: weekly-report", "bad") in [(line.text, line.tone) for line in item.detail]


def test_a_long_ago_tick_reads_in_hours_and_none_says_so():
    hours = status_item(daemon(last_tick=NOW - timedelta(hours=3), interval_minutes=None), NOW)
    assert "Last tick 3 h ago" in [line.text for line in hours.detail]
    assert hours.text == "Daemon running"
    never = status_item(daemon(last_tick=None), NOW)
    assert "No tick yet" in [line.text for line in never.detail]


def test_other_platforms_are_unsupported():
    item = status_item(daemon(platform="win32", installed=False), NOW)
    assert (item.text, item.actions) == ("Daemon unsupported here", [])


def test_the_version_the_daemon_started_with_is_shown():
    item = status_item(daemon(version=__version__), NOW)
    assert [line.text for line in item.detail][1] == f"Daemon version {__version__}"
    assert (item.text, item.tone, labels(item)) == ("Daemon running · next tick", "ok", ["Logs", "Pause", "Uninstall"])


def test_an_old_install_without_a_version_can_be_restarted_to_record_it():
    item = status_item(daemon(version=None), NOW)
    assert "Daemon version unknown · restart it to record its version" in [line.text for line in item.detail]
    assert labels(item)[0] == "Restart daemon"
    assert item.tone == "ok"


def test_a_daemon_from_an_older_hz_warns_and_offers_a_restart():
    item = status_item(daemon(version="0.0.1", version_mismatch=True), NOW)
    assert (item.text, item.tone, labels(item)) == (
        "Daemon needs a restart", "warn", ["Restart daemon", "Logs", "Pause", "Uninstall"],
    )  # fmt: skip
    assert f"Daemon runs hz 0.0.1, not the installed {__version__}: restart it" in [line.text for line in item.detail]
    restart = item.actions[0].effect
    assert isinstance(restart, RunOperation)
    assert restart.operation == "restart"
