"""First-install questions use actual health requirements and the supported configuration service."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import Config, HeinzelError
from heinzel.heads import bootstrap
from heinzel.heads.cli import build
from heinzel.kernel import ScriptedPrompter
from heinzel.modules.org import OrgSettings
from heinzel.modules.team import TeamSettings

pytestmark = pytest.mark.integration


@pytest.fixture
def managed_profile(monkeypatch, make_kernel, tmp_path):
    directory = tmp_path / "configuration"
    monkeypatch.setenv("HZ_CONFIG_HOME", str(directory))
    kernel = make_kernel(builtins=True, config=Config.load(tmp_path))
    calls = []

    def managed(_executable, _environment, arguments):
        # Each managed subprocess reads fresh configuration; use the same real head/service boundary here.
        refreshed = make_kernel(builtins=True, config=Config.load(tmp_path))
        result = CliRunner().invoke(build(refreshed), [*arguments, "--json"])
        assert result.exit_code == 0, result.output
        calls.append(arguments)
        return json.loads(result.stdout)

    monkeypatch.setattr(bootstrap, "_managed_json", managed)
    return kernel, directory, calls


def test_first_profile_asks_name_team_and_only_the_actual_missing_required_fields(managed_profile):
    _kernel, directory, calls = managed_profile
    answers = ScriptedPrompter(["Mira", "Payments", "mira", "engineer", "Europe/Berlin"])
    bootstrap._complete_profile(Path("/fixture/persistent/bin/hz"), {}, answers)
    config = Config.load(directory)
    person = config.settings(OrgSettings, "org")
    team = config.settings(TeamSettings, "team")
    assert person.name == "Mira"
    assert person.handle == "mira"
    assert person.role == "engineer"
    assert person.timezone == "Europe/Berlin"
    assert person.email is None
    assert team.name == "Payments"
    assert [call[3] for call in calls if call[:3] == ["system", "config", "set"]] == [
        "org.name",
        "team.name",
        "org.handle",
        "org.role",
        "org.timezone",
    ]


def test_existing_profile_values_are_respected_without_questions_or_writes(managed_profile):
    _kernel, directory, calls = managed_profile
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text(
        '[org]\nname="Mira"\nhandle="mira"\nrole="engineer"\ntimezone="Europe/Berlin"\n[team]\nname="Payments"\n'
    )
    before = (directory / "config.toml").read_bytes()
    bootstrap._complete_profile(Path("/fixture/persistent/bin/hz"), {}, ScriptedPrompter([]))
    assert (directory / "config.toml").read_bytes() == before
    assert not any(call[:3] == ["system", "config", "set"] for call in calls)


def test_a_partial_profile_asks_only_missing_fields_and_never_invents_optional_credentials(managed_profile):
    _kernel, directory, calls = managed_profile
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text('[org]\nname="Mira"\nhandle="mira"\ntimezone="Europe/Berlin"\n')
    bootstrap._complete_profile(Path("/fixture/persistent/bin/hz"), {}, ScriptedPrompter(["Payments", "engineer"]))
    assert [call[3] for call in calls if call[:3] == ["system", "config", "set"]] == ["team.name", "org.role"]
    assert "credential" not in json.dumps(calls)


@pytest.mark.parametrize("status", ["degraded", "down", "unknown", None])
def test_setup_cannot_be_completed_with_non_green_health(monkeypatch, status):
    monkeypatch.setattr(bootstrap, "_managed_json", lambda *_args: {"status": status})
    with pytest.raises(HeinzelError, match="Setup is not complete"):
        bootstrap._verify_setup(Path("/fixture/persistent/bin/hz"), {})
