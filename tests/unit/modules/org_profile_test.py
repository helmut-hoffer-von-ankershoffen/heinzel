"""The identity step: what it finds, what it still asks, and what it writes, with scripted answers."""

from pathlib import Path

import pytest

from heinzel import ConfigEditor, HeinzelError
from heinzel.kernel import Config, ScriptedPrompter
from heinzel.modules.org import OrgSettings, ProfileService
from heinzel.modules.org import _discovery as discovery
from heinzel.modules.org._discovery import Discovered, Login
from heinzel.modules.org._profile import ask_profile, ask_timezone

pytestmark = pytest.mark.unit

CONFIG = Path("/home/mira/.config/hz/config.toml")
EVERYTHING = Discovered(
    github=Login(id="Mira-Example", name="Mira from GitHub"),
    google=Login(id="mira@example.com", name="Mira Example"),
    timezone="Europe/Berlin",
    coding_agent="claude-code",
    sources={
        "github": "your gh login",
        "google": "your gws login",
        "timezone": "this machine's clock",
        "coding_agent": "claude on your PATH",
    },
)


class RecordingEditor(ConfigEditor):
    """Keeps what would be written; `unset` of a key that isn't there fails like the real one."""

    def __init__(self, present: tuple[str, ...] = ()) -> None:
        self.written: dict[str, object] = {}
        self.unset_keys: list[str] = []
        self.present = set(present)

    def write(self, key: str, value: object, scope: str = "user") -> Path:  # type: ignore[override]
        assert scope == "user"
        self.written[key] = value
        return CONFIG

    def unset(self, key: str, scope: str = "user") -> Path:  # type: ignore[override]
        if key not in self.present:
            msg = f"{key} is not set in {CONFIG}"
            raise HeinzelError(msg)
        self.unset_keys.append(key)
        return CONFIG


def configured(**values: object) -> OrgSettings:
    """Settings as read from a file, so `model_fields_set` says what is set."""
    return Config(files=(), data={"org": values}).settings(OrgSettings, "org")


@pytest.fixture
def found(monkeypatch):
    def set_(discovered: Discovered) -> None:
        monkeypatch.setattr(discovery, "discover", lambda: discovered)

    return set_


def test_when_everything_is_found_only_role_and_working_hours_are_asked(found):
    found(EVERYTHING)
    prompter = ScriptedPrompter(["3", ""])
    editor = RecordingEditor()
    assert ask_profile(OrgSettings(), prompter, editor) == CONFIG
    assert editor.written == {
        "org.handle": "mira-example",
        "org.role": "engineering-manager",
        "org.timezone": "Europe/Berlin",
        "org.working_hours": [9, 18],
        "org.github": "Mira-Example",
        "org.coding_agent": "claude-code",
        "org.name": "Mira Example",
        "org.email": "mira@example.com",
    }
    assert "  Google login  mira@example.com (your gws login)" in prompter.transcript
    assert not any(line.startswith(("Handle", "Time zone", "GitHub login")) for line in prompter.transcript)


def test_nothing_is_asked_that_is_configured_or_found(found):
    found(EVERYTHING)
    prompter = ScriptedPrompter([])  # every question would exhaust the script
    editor = RecordingEditor()
    ask_profile(configured(role="engineer", working_hours="08-17"), prompter, editor)
    assert editor.written["org.role"] == "engineer"
    assert editor.written["org.working_hours"] == [8, 17]


def test_the_github_name_is_used_when_there_is_no_google_login(found):
    found(Discovered(github=Login(id="theo-x", name="Theo"), sources={"github": "your gh login"}))
    editor = RecordingEditor()
    ask_profile(OrgSettings(), ScriptedPrompter(["1", "lisbon", ""]), editor)
    assert editor.written["org.name"] == "Theo"
    assert "org.email" not in editor.written


def test_without_any_login_the_handle_and_more_are_asked():
    prompter = ScriptedPrompter(["", "Mira", "mira", "engineer", "lisbon", "", "mira_x", ""])
    editor = RecordingEditor()
    ask_profile(OrgSettings(), prompter, editor)
    assert editor.written == {
        "org.handle": "mira",
        "org.role": "engineer",
        "org.timezone": "Europe/Lisbon",
        "org.working_hours": [9, 18],
        "org.coding_agent": "none",
    }
    transcript = "\n".join(prompter.transcript)
    assert "Nothing to find about you here (no gh or gws login)" in transcript
    assert "Please answer; there is no default." in transcript
    assert "'Mira' is not a handle" in transcript
    assert "'mira_x' is not a GitHub login" in transcript


def test_a_login_that_isnt_a_handle_is_not_suggested(found):
    found(Discovered(github=Login(id="a" * 39 + "-"), sources={"github": "your gh login"}))
    editor = RecordingEditor()
    ask_profile(OrgSettings(), ScriptedPrompter(["mira", "1", "Europe/Berlin", ""]), editor)
    assert editor.written["org.handle"] == "mira"


def test_changing_a_profile_asks_everything_with_what_is_configured(found):
    found(EVERYTHING)
    current = configured(
        handle="theo",
        role="engineer",
        timezone="Asia/Tokyo",
        working_hours=[10, 19],
        github="theo-x",
        coding_agent="command",
    )
    prompter = ScriptedPrompter(["", "", "", "", "none", ""])
    editor = RecordingEditor(present=("org.github",))
    ask_profile(current, prompter, editor)
    assert editor.written == {
        "org.handle": "theo",
        "org.role": "engineer",
        "org.timezone": "Asia/Tokyo",
        "org.working_hours": [10, 19],
        "org.coding_agent": "command",
        "org.name": "Mira Example",
        "org.email": "mira@example.com",
    }
    assert editor.unset_keys == ["org.github"]
    assert not any(line.startswith("Found about you") for line in prompter.transcript)


def test_dropping_a_github_login_set_elsewhere_still_succeeds():
    current = configured(handle="theo", role="engineer", timezone="Asia/Tokyo", github="theo-x")
    editor = RecordingEditor()
    ask_profile(current, ScriptedPrompter(["", "", "", "", "none", ""]), editor)
    assert editor.unset_keys == []


def test_working_hours_are_asked_again_until_they_are_a_working_day(found):
    found(EVERYTHING)
    prompter = ScriptedPrompter(["1", "18-9", "9-17"])
    editor = RecordingEditor()
    ask_profile(OrgSettings(), prompter, editor)
    assert editor.written["org.working_hours"] == [9, 17]
    assert any("not 18-09" in line for line in prompter.transcript)


def test_a_time_zone_is_found_by_part_of_its_name():
    prompter = ScriptedPrompter(["atlantis", "america", "new york"])
    assert ask_timezone(prompter, None) == "America/New_York"
    assert "No time zone matches 'atlantis'" in prompter.transcript[1]
    assert "time zones match 'america'; type more of the name." in prompter.transcript[3]


def test_a_time_zone_among_a_few_matches_is_chosen():
    prompter = ScriptedPrompter(["indiana/v", "2"])
    assert ask_timezone(prompter, None) == "America/Indiana/Vincennes"


def test_the_service_requires_a_complete_profile():
    with pytest.raises(HeinzelError, match=r"missing org.role, org.timezone; run `hz setup`"):
        ProfileService(OrgSettings(handle="mira")).require()
    complete = OrgSettings(handle="mira", role="engineer", timezone="Europe/Berlin")
    assert ProfileService(complete).require().handle == "mira"
