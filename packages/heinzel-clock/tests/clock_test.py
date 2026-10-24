import pytest

pytestmark = pytest.mark.integration

AT = "2026-10-23T12:00:00Z"


def test_shows_each_zone_with_working_hours(hz):
    assert hz("clock", "--at", AT).json == [
        {"zone": "Europe/Berlin", "time": "Fri 14:00", "working": True},
        {"zone": "America/New_York", "time": "Fri 08:00", "working": False},
        {"zone": "America/Los_Angeles", "time": "Fri 05:00", "working": False},
    ]


def test_zones_are_configurable(hz, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", '["Asia/Tokyo"]')
    assert hz("clock", "--at", AT).json == [{"zone": "Asia/Tokyo", "time": "Fri 21:00", "working": False}]


def test_unknown_zones_are_reported(hz, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", '["Mars/Olympus"]')
    assert hz("clock").exit_code == 1
    doctor = hz("system", "doctor")
    assert doctor.exit_code == 1
    assert doctor.json["checks"]["clock"][0]["detail"] == "unknown: Mars/Olympus"
