"""The daemon in the status bar (ADR 0056): each state in plain words, its tone and its buttons."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from heinzel import LiveTime, RunOperation, __version__
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
        "installed_version": __version__,
    }
    return DaemonStatus.model_validate(values | fields)


def labels(item) -> list[str]:
    return [action.label for action in item.actions]


def test_running_says_when_it_ticks_next():
    item = status_item(daemon(), NOW)
    assert (item.text, item.tone, labels(item)) == ("Daemon running · next check", "ok", ["Logs", "Pause", "Uninstall"])
    assert item.live == LiveTime(at=NOW + timedelta(minutes=3))
    assert [line.text for line in item.detail] == ["Last check 2 min ago", "Jobs: 3", f"Running hz {__version__}"]


def test_paused_warns_and_offers_resume():
    item = status_item(daemon(paused=True), NOW)
    assert (item.text, item.tone, labels(item)) == ("Daemon paused", "warn", ["Resume", "Logs", "Uninstall"])
    resume = item.actions[0].effect
    assert isinstance(resume, RunOperation)
    assert resume.operation == "resume"


def test_not_installed_offers_install_as_an_operation_the_person_confirms():
    item = status_item(daemon(installed=False, loaded=False, last_tick=None), NOW)
    assert (item.text, item.tone, labels(item)) == ("Daemon not installed", "neutral", ["Install"])
    install = item.actions[0].effect
    assert isinstance(install, RunOperation)
    assert install.operation == "install"
    assert item.detail[0].text == "Keeps your Heinzelmännchen working while the app is closed"


def test_uninstall_is_an_operation_too():
    item = status_item(daemon(), NOW)
    uninstall = next(action.effect for action in item.actions if action.label == "Uninstall")
    assert isinstance(uninstall, RunOperation)
    assert uninstall.operation == "uninstall"


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


def test_a_long_ago_tick_reads_in_hours():
    hours = status_item(daemon(last_tick=NOW - timedelta(hours=3), interval_minutes=None), NOW)
    assert "Last check 3 h ago" in [line.text for line in hours.detail]
    assert hours.text == "Daemon running"


def test_just_installed_says_it_is_starting_and_when_the_first_check_comes_without_asking_for_a_restart():
    item = status_item(daemon(last_tick=None, version=None, installed_at=NOW - timedelta(minutes=1), jobs=2), NOW)
    assert (item.text, item.tone, labels(item)) == (
        "Daemon starting · first check within 5 min",
        "neutral",
        ["Logs", "Pause", "Uninstall"],
    )
    assert [line.text for line in item.detail] == ["Waiting for its first check", "Jobs: 2"]


def test_an_unknown_version_is_not_a_reason_to_restart():
    item = status_item(daemon(version=None), NOW)
    assert labels(item) == ["Logs", "Pause", "Uninstall"]
    assert (item.text, item.tone) == ("Daemon running · next check", "ok")
    assert not any("unknown" in line.text or "restart" in line.text for line in item.detail)


def test_no_first_check_after_two_intervals_is_not_ticking():
    item = status_item(daemon(last_tick=None, installed_at=NOW - timedelta(minutes=11)), NOW)
    assert (item.text, item.tone) == ("Daemon not ticking", "bad")


def test_other_platforms_are_unsupported():
    item = status_item(daemon(platform="win32", installed=False), NOW)
    assert (item.text, item.actions) == ("Daemon unsupported here", [])


def test_a_tick_from_an_older_hz_warns_and_offers_a_restart():
    item = status_item(daemon(version="0.0.1", installed_version="0.0.1", version_mismatch=True), NOW)
    assert (item.text, item.tone, labels(item)) == (
        "Daemon needs a restart · version mismatch",
        "warn",
        ["Restart daemon", "Logs", "Pause", "Uninstall"],
    )  # fmt: skip
    assert f"Installed for hz 0.0.1; the last check ran 0.0.1; this is {__version__}" in [
        line.text for line in item.detail
    ]
    restart = item.actions[0].effect
    assert isinstance(restart, RunOperation)
    assert restart.operation == "restart"
