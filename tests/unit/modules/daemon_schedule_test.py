from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from heinzel.modules.system.daemon._schedule import latest, local_zone, next_slot, problem, slots

pytestmark = pytest.mark.unit

BERLIN = ZoneInfo("Europe/Berlin")
SUMMER = timezone(timedelta(hours=2))  # what a production clock reports in summer: an offset, no DST rules


def at(hour: int, minute: int = 0, day: int = 5) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize("schedule", ["0 9 * * MON", "*/5 * * * *", "@daily"])
def test_accepts_five_fields_and_aliases(schedule):
    assert problem(schedule) is None


@pytest.mark.parametrize("schedule", ["0 9 * *", "0 0 9 * * MON", "every monday", "61 * * * *", "@reboot"])
def test_rejects_other_expressions(schedule):
    assert "invalid schedule" in (problem(schedule) or "")


def test_rejects_schedules_that_never_fire():
    assert problem("0 0 31 2 *") == "invalid schedule '0 0 31 2 *': it never fires"


def test_slots_exclude_the_start_and_include_the_end():
    assert slots("0 * * * *", at(9), at(12), UTC) == [at(10), at(11), at(12)]


def test_slots_stop_at_the_limit_oldest_first():
    assert slots("0 * * * *", at(0), at(23), UTC, limit=2) == [at(1), at(2)]


def test_slots_keep_the_wall_clock_time_across_the_change_to_winter_time():
    after = datetime(2026, 10, 24, 9, tzinfo=SUMMER)  # as stored in a receipt
    until = datetime(2026, 10, 27, 10, tzinfo=timezone(timedelta(hours=1)))
    found = slots("0 9 * * *", after, until, BERLIN)
    assert [s.isoformat() for s in found] == [
        "2026-10-25T09:00:00+01:00",
        "2026-10-26T09:00:00+01:00",
        "2026-10-27T09:00:00+01:00",
    ]
    assert latest("0 9 * * *", after, until, BERLIN) == found[-1]


def test_an_hourly_job_runs_once_per_wall_clock_hour_when_the_clocks_go_back():
    found = slots(
        "0 * * * *", datetime(2026, 10, 25, 1, tzinfo=BERLIN), datetime(2026, 10, 25, 4, tzinfo=BERLIN), BERLIN
    )
    assert [s.isoformat() for s in found] == [
        "2026-10-25T02:00:00+02:00",
        "2026-10-25T03:00:00+01:00",
        "2026-10-25T04:00:00+01:00",
    ]


def test_a_missing_time_runs_shifted_by_the_gap_when_the_clocks_go_forward():
    after, until = datetime(2027, 3, 27, 12, tzinfo=BERLIN), datetime(2027, 3, 28, 12, tzinfo=BERLIN)
    assert [s.isoformat() for s in slots("30 2 * * *", after, until, BERLIN)] == ["2027-03-28T03:30:00+02:00"]
    assert latest("30 2 * * *", after, datetime(2027, 3, 28, 3, 15, tzinfo=BERLIN), BERLIN) is None
    assert next_slot("30 2 * * *", after, BERLIN).isoformat() == "2027-03-28T03:30:00+02:00"


def test_every_minute_across_the_gap_never_goes_backwards():
    after, until = datetime(2027, 3, 28, 1, 58, tzinfo=BERLIN), datetime(2027, 3, 28, 4, 1, tzinfo=BERLIN)
    found = slots("* * * * *", after, until, BERLIN, limit=1000)
    assert found == sorted(set(found))


def test_latest_includes_a_slot_on_the_end():
    assert latest("0 * * * *", at(9), at(12), UTC) == at(12)
    assert latest("0 * * * *", at(9), at(12, 30), UTC) == at(12)


def test_latest_is_none_without_a_slot_in_the_window():
    assert latest("0 * * * *", at(9), at(9, 30), UTC) is None


def test_next_slot():
    assert next_slot("0 9 * * MON", at(10), UTC) == at(9, day=12)


def test_local_zone_prefers_tz(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert local_zone() == ZoneInfo("Asia/Tokyo")
    monkeypatch.setenv("TZ", ":Europe/Berlin")
    assert local_zone() == BERLIN


def test_local_zone_reads_a_tz_file(monkeypatch):
    monkeypatch.setenv("TZ", str(_tzfile("Europe/Berlin")))
    assert local_zone().utcoffset(datetime(2026, 7, 1)) == timedelta(hours=2)  # noqa: DTZ001 - wall clock


def test_local_zone_follows_the_etc_localtime_link(monkeypatch, tmp_path):
    monkeypatch.delenv("TZ", raising=False)
    target = tmp_path / "usr" / "share" / "zoneinfo" / "America" / "New_York"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"")
    link = tmp_path / "localtime"
    link.symlink_to(target)
    assert local_zone(link) == ZoneInfo("America/New_York")


def test_local_zone_reads_a_copied_localtime_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TZ", raising=False)
    copy = tmp_path / "localtime"
    source = _tzfile("Europe/Berlin")
    assert source is not None
    copy.write_bytes(source.read_bytes())
    assert local_zone(copy).utcoffset(datetime(2026, 1, 1)) == timedelta(hours=1)  # noqa: DTZ001 - wall clock


def test_local_zone_falls_back_to_the_current_offset(monkeypatch, tmp_path):
    monkeypatch.setenv("TZ", "Not/AZone")
    zone = local_zone(tmp_path / "missing")
    assert zone.utcoffset(datetime.now(UTC)) is not None


def _tzfile(key: str):
    import zoneinfo
    from pathlib import Path

    for root in zoneinfo.TZPATH:
        if (Path(root) / key).is_file():
            return Path(root) / key
    import tzdata  # the wheel's copy, on systems without a zoneinfo directory

    return Path(tzdata.__file__).parent / "zoneinfo" / key
