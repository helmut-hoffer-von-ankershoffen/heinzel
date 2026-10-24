"""The org module's settings and profile model: what a profile may hold, and what it is missing."""

import pytest
from pydantic import ValidationError

from heinzel.modules.org import ROLES, OrgSettings, Profile
from heinzel.modules.org._models import format_hours, parse_hours

pytestmark = pytest.mark.unit


def test_the_core_roles_are_the_seven_of_adr_0033():
    assert ROLES == (
        "engineer",
        "principal",
        "engineering-manager",
        "engineering-leadership",
        "product-manager",
        "product-leadership",
        "program-manager",
    )


def test_a_complete_profile_names_handle_role_and_time_zone():
    settings = OrgSettings(handle="mira", role="engineering-manager", timezone="Europe/Berlin", github="Mira-Example")
    profile = Profile.of(settings)
    assert profile.complete
    assert profile.missing == []
    assert profile.working_hours == "09-18"


def test_a_profile_says_what_is_missing_and_what_to_run():
    profile = Profile.of(OrgSettings(handle="mira"))
    assert not profile.complete
    assert profile.missing == ["org.role", "org.timezone"]
    assert profile.fix == "hz setup"


@pytest.mark.parametrize("value", ["08-17", " 8 - 17 ", [8, 17], (8, 17)])
def test_working_hours_are_read_as_text_or_a_pair(value):
    assert OrgSettings(working_hours=value).working_hours == (8, 17)


@pytest.mark.parametrize("value", ["17-08", "9 to 5", [9], ["9", "17"], "00-25", 9])
def test_working_hours_must_be_a_working_day(value):
    with pytest.raises(ValidationError, match="working hours"):
        OrgSettings(working_hours=value)


def test_hours_round_trip_as_two_digits():
    assert format_hours(parse_hours("8-17")) == "08-17"


@pytest.mark.parametrize(
    ("field", "value", "problem"),
    [
        ("handle", "Mira", "is not a handle"),
        ("handle", "-mira", "is not a handle"),
        ("handle", "mira--x", "is not a handle"),
        ("handle", "m" * 40, "is not a handle"),
        ("role", "ceo", "is not a role; one of: engineer"),
        ("timezone", "Mars/Olympus", "is not an IANA time zone"),
        ("github", "mira_example", "is not a GitHub login"),
    ],
)
def test_invalid_profile_values_are_refused_with_what_is_allowed(field, value, problem):
    with pytest.raises(ValidationError, match=problem):
        OrgSettings(**{field: value})


def test_the_coding_agent_is_one_of_three():
    assert OrgSettings(coding_agent="command").coding_agent == "command"
    with pytest.raises(ValidationError):
        OrgSettings(coding_agent="vim")


def test_a_project_file_cant_set_who_you_are():
    assert set(OrgSettings.model_fields) <= OrgSettings.ignored_in_project
