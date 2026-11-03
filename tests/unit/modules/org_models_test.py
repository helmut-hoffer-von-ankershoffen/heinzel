"""The org module's settings and profile model: what a profile may hold, and what it is missing."""

import pytest
from pydantic import ValidationError

from heinzel.modules.org import ROLES, OrgSettings, Profile
from heinzel.modules.org._models import ROLE_LABELS, format_hours, parse_hours

pytestmark = pytest.mark.unit


def test_the_core_roles_are_four_in_this_order_with_these_labels():
    assert ROLES == ("engineer", "engineering-manager", "product-manager", "program-manager")
    assert [ROLE_LABELS[role] for role in ROLES] == [
        "IC - Engineer",
        "EM - Engineering Manager",
        "PM - Product Manager",
        "TPM - Program Manager",
    ]
    assert OrgSettings.model_json_schema()["properties"]["role"]["anyOf"][0]["enum"] == list(ROLES)


@pytest.mark.parametrize("old", ["principal", "engineering-leadership", "product-leadership"])
def test_a_saved_removed_role_loads_as_not_set_and_asks_for_a_new_one(old):
    settings = OrgSettings(handle="mira", role=old, timezone="Europe/Berlin")
    assert settings.role is None
    assert settings.retired_role == old
    profile = Profile.of(settings)
    assert (profile.role, profile.retired_role, profile.complete, profile.missing) == (None, old, False, ["org.role"])


def test_a_made_up_role_is_still_an_error():
    with pytest.raises(ValueError, match="is not a role"):
        OrgSettings(role="ceo")


def test_the_retired_role_is_not_a_setting_to_edit():
    assert "retired_role" not in OrgSettings.model_json_schema()["properties"]


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


@pytest.mark.parametrize("value", ["08-17", " 8 - 17 ", "[8, 17]", [8, 17], (8, 17)])
def test_working_hours_are_read_as_text_or_a_pair(value):
    assert OrgSettings(working_hours=value).working_hours == (8, 17)


@pytest.mark.parametrize("value", ["17-08", "9 to 5", "[8, 17", [9], ["9", "17"], [True, 17], "00-25", 9])
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


@pytest.mark.parametrize("old", ["principal", "engineering-leadership", "product-leadership"])
def test_setting_a_removed_role_is_refused_and_names_the_four_that_are_offered(old, make_kernel):
    from heinzel import Config, HeinzelError
    from heinzel.modules.system.config import ConfigService

    kernel = make_kernel(builtins=True, config=Config(files=(), data={}))
    service = ConfigService(kernel)
    with pytest.raises(
        HeinzelError,
        match=r"no longer offered; one of: engineer, engineering-manager, product-manager, program-manager",
    ):
        service._write("org.role", old)
