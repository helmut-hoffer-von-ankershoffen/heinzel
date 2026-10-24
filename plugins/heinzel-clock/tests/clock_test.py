import tomllib

import pytest
from heinzel_clock import ClockSettings, Zone, search
from pydantic import ValidationError

from heinzel.kernel import user_config_dir

pytestmark = pytest.mark.integration

AT = "2026-10-23T12:00:00Z"


def test_shows_each_zone_with_its_working_hours(hz):
    assert hz("clock", "--at", AT).json == [
        {"zone": "Europe/Berlin", "time": "Fri 14:00", "hours": [9, 18], "working": True, "primary": True},
        {"zone": "America/New_York", "time": "Fri 08:00", "hours": [9, 18], "working": False, "primary": False},
        {"zone": "America/Los_Angeles", "time": "Fri 05:00", "hours": [9, 18], "working": False, "primary": False},
    ]


def test_the_first_zone_is_yours_and_the_table_says_so(hz, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", '["Asia/Tokyo", "Europe/Berlin"]')
    assert [zone["primary"] for zone in hz("clock", "--at", AT).json] == [True, False]
    assert "Asia/Tokyo (yours)" in hz("clock", "--at", AT, json=False).output


def test_nobody_is_working_at_the_weekend(hz):
    assert not any(zone["working"] for zone in hz("clock", "--at", "2026-10-24T14:00:00Z").json)


def test_each_zone_has_its_own_hours(hz, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", '[{"name": "America/New_York", "hours": [7, 15]}, "Asia/Tokyo"]')
    assert hz("clock", "--at", AT).json == [
        {"zone": "America/New_York", "time": "Fri 08:00", "hours": [7, 15], "working": True, "primary": True},
        {"zone": "Asia/Tokyo", "time": "Fri 21:00", "hours": [9, 18], "working": False, "primary": False},
    ]


def test_hours_must_be_a_working_day():
    with pytest.raises(ValidationError, match=r"hours of Asia/Tokyo must be \[start, end\]"):
        Zone(name="Asia/Tokyo", hours=(19, 10))


def test_the_old_shape_is_read_with_a_deprecation_warning(hz, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", '["Asia/Tokyo"]')
    monkeypatch.setenv("HZ_CLOCK__WORKING_HOURS", "[8, 22]")
    with pytest.warns(DeprecationWarning, match="clock.working_hours is deprecated"):
        assert hz("clock", "--at", AT).json == [
            {"zone": "Asia/Tokyo", "time": "Fri 21:00", "hours": [8, 22], "working": True, "primary": True}
        ]
    doctor = hz("system", "doctor").json  # the settings were read, and warned about, once
    assert doctor["checks"]["clock"][1]["name"] == "working_hours"
    assert doctor["checks"]["clock"][1]["status"] == "degraded"


def test_old_working_hours_apply_to_the_default_zones():
    with pytest.warns(DeprecationWarning, match="working_hours is deprecated"):
        settings = ClockSettings(working_hours=(8, 16))
    assert {zone.hours for zone in settings.zones} == {(8, 16)}


def test_unknown_zones_are_reported(hz, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", '["Mars/Olympus"]')
    assert hz("clock").exit_code == 1
    doctor = hz("system", "doctor")
    assert doctor.exit_code == 1
    assert doctor.json["checks"]["clock"][0]["detail"] == "unknown: Mars/Olympus"


def test_search_ignores_case_and_matches_spaces_as_underscores():
    assert search("new york") == ["America/New_York"]
    assert "Asia/Tokyo" in search("TOKYO")
    assert search("Atlantis") == []


def config() -> dict:
    return tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))


def test_setup_picks_zones_by_search_and_hours_per_zone(hz, hz_prompter):
    hz_prompter.answers += [
        "1",  # keep Europe/Berlin
        "atlantis",  # nothing matches
        "america",  # too many matches
        "york",  # one match: America/New_York
        "kolkata",
        "",  # done
        "",  # Berlin 9
        "17",  # Berlin until 17
        "8",  # New York from 8
        "",  # New York until 18
        "x",  # not a number
        "10",  # Kolkata from 10
        "10",  # can't end when it starts
        "19",
        "",  # save
    ]
    result = hz("setup", "clock", json=False)
    assert result.exit_code == 0, result.output
    assert config() == {
        "clock": {
            "zones": [
                {"name": "Europe/Berlin", "hours": [9, 17]},
                {"name": "America/New_York", "hours": [8, 18]},
                {"name": "Asia/Kolkata", "hours": [10, 19]},
            ]
        }
    }
    transcript = "\n".join(hz_prompter.transcript)
    assert "No time zone matches 'atlantis'" in transcript
    assert "time zones match 'america'; type more of the name." in transcript
    assert "Please enter a whole number from 11 to 24." in transcript
    assert "  Asia/Kolkata 10-19" in transcript


def test_setup_chooses_among_a_few_matches_and_skips_duplicates(hz, hz_prompter):
    hz_prompter.answers += ["", "berlin", "indiana/v", "2", "", *[""] * 8, ""]  # 4 zones: start and end hour each
    result = hz("setup", "clock", json=False)
    assert result.exit_code == 0, result.output
    assert "Europe/Berlin is already in the list." in hz_prompter.transcript
    assert "  1) America/Indiana/Vevay" in hz_prompter.transcript
    assert [zone["name"] for zone in config()["clock"]["zones"]][-1] == "America/Indiana/Vincennes"


def test_setup_needs_one_zone_and_saves_nothing_when_declined(hz, hz_prompter):
    hz_prompter.answers += ["none", "", "tokyo", "", "", "", "n"]  # keep none, then add one
    result = hz("setup", "clock", json=False)
    assert result.exit_code == 0, result.output
    assert "The team needs at least one time zone." in hz_prompter.transcript
    assert "Nothing was saved." in hz_prompter.transcript
    assert not (user_config_dir() / "config.toml").exists()


def test_setup_replaces_the_old_shape(hz, hz_prompter, monkeypatch):
    (user_config_dir() / "config.toml").write_text(
        '[clock]\nzones = ["Asia/Tokyo"]\nworking_hours = [8, 22]\n', encoding="utf-8"
    )
    monkeypatch.setenv("HZ_CLOCK__ZONES", '["Asia/Tokyo"]')
    monkeypatch.setenv("HZ_CLOCK__WORKING_HOURS", "[8, 22]")
    hz_prompter.answers += ["y", "", "", "", "", ""]  # change it, keep Tokyo, add none, keep 8-22, save
    with pytest.warns(DeprecationWarning, match="working_hours is deprecated"):
        result = hz("setup", "clock", json=False)
    assert result.exit_code == 0, result.output
    assert config() == {"clock": {"zones": [{"name": "Asia/Tokyo", "hours": [8, 22]}]}}


def test_text_output_shows_the_working_hours(hz):
    output = hz("clock", "--at", AT, json=False).output
    assert "Europe/Berlin" in output
    assert "09-18" in output


def test_setup_succeeds_when_old_working_hours_come_only_from_the_environment(hz, hz_prompter, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", '["Asia/Tokyo"]')
    monkeypatch.setenv("HZ_CLOCK__WORKING_HOURS", "[8, 22]")
    hz_prompter.answers += ["y", "", "", "", "", ""]
    with pytest.warns(DeprecationWarning, match="working_hours is deprecated"):
        result = hz("setup", "clock", json=False)
    assert result.exit_code == 0, result.output
    assert config() == {"clock": {"zones": [{"name": "Asia/Tokyo", "hours": [8, 22]}]}}


def test_setup_without_zones_asks_only_to_add_some(hz, hz_prompter, monkeypatch):
    monkeypatch.setenv("HZ_CLOCK__ZONES", "[]")
    hz_prompter.answers += ["y", "", "tokyo", "", "", "", ""]
    result = hz("setup", "clock", json=False)
    assert result.exit_code == 0, result.output
    assert not [line for line in hz_prompter.transcript if line.startswith("Time zones to keep")]
    assert "The team needs at least one time zone." in hz_prompter.transcript


def test_the_zones_schema_tells_a_form_each_name_is_a_time_zone_and_each_hour_an_hour_of_the_day(hz):
    schema = hz("system", "config", "get", "clock.zones").json["json_schema"]
    zone = schema["items"]
    assert zone["properties"]["name"]["format"] == "iana-timezone"
    assert zone["properties"]["hours"]["prefixItems"] == [{"type": "integer", "minimum": 0, "maximum": 24}] * 2
    assert zone["properties"]["hours"]["default"] == [9, 18]
    assert "$ref" not in str(schema)


def test_an_hour_past_midnight_is_refused():
    with pytest.raises(ValidationError, match="less than or equal to 24"):
        Zone(name="Asia/Tokyo", hours=(9, 25))
