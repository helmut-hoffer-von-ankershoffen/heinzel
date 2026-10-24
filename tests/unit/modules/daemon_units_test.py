"""The launchd and systemd units, compared with golden files in tests/fixtures/daemon/."""

import plistlib
from pathlib import Path

import pytest

from heinzel.modules.system.daemon._units import (
    check_interval,
    launchd_plist,
    on_calendar,
    systemd_service,
    systemd_timer,
)

pytestmark = pytest.mark.unit

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "daemon"


def test_launchd_plist_matches_the_golden_file():
    plist = launchd_plist(Path("/Users/ada/.local/bin/hz"), 5, Path("/Users/ada/Library/Logs/hz"))
    assert plist == (GOLDEN / "launchd.plist").read_text(encoding="utf-8")


def test_systemd_units_match_the_golden_files():
    assert systemd_service(Path("/home/ada/.local/bin/hz")) == (GOLDEN / "hz-daemon.service").read_text(
        encoding="utf-8"
    )
    assert systemd_timer(5) == (GOLDEN / "hz-daemon.timer").read_text(encoding="utf-8")


def test_launchd_fires_at_every_slot_of_the_hour():
    agent = plistlib.loads(launchd_plist(Path("/x/hz"), 15, Path("/logs")).encode())
    assert agent["StartCalendarInterval"] == [{"Minute": 0}, {"Minute": 15}, {"Minute": 30}, {"Minute": 45}]


def test_an_hourly_timer_fires_on_the_hour():
    assert on_calendar(60) == "*:00"
    assert on_calendar(10) == "*:0/10"


def test_paths_with_spaces_are_quoted_for_systemd():
    assert "ExecStart='/home/ada lovelace/bin/hz' system daemon tick" in systemd_service(
        Path("/home/ada lovelace/bin/hz")
    )


@pytest.mark.parametrize("minutes", [0, 7, 45, 90])
def test_intervals_must_divide_an_hour(minutes):
    with pytest.raises(ValueError, match="divide an hour"):
        check_interval(minutes)


def test_percent_signs_are_escaped_for_systemd():
    assert "ExecStart=/home/ada/100%%/bin/hz system daemon tick" in systemd_service(Path("/home/ada/100%/bin/hz"))


def test_the_tick_report_is_discarded_and_only_crashes_are_logged():
    agent = plistlib.loads(launchd_plist(Path("/x/hz"), 5, Path("/logs")).encode())
    assert (agent["StandardOutPath"], agent["StandardErrorPath"]) == ("/dev/null", "/logs/launchd.err.log")
    assert "StandardOutput=null" in systemd_service(Path("/x/hz"))
