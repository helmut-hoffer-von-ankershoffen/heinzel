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


def test_defer_ai_is_explicit_persistent_and_preserves_existing_selection(managed_profile):
    _kernel, directory, calls = managed_profile
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.toml").write_text('[capabilities]\nai_access="gateway"\n')
    answers = ScriptedPrompter([""])
    bootstrap._configure_ai(Path("/fixture/persistent/bin/hz"), {}, answers)
    config = Config.load(directory)
    assert config.data["capabilities"] == {"ai_access": "gateway", "agent_runner": "none"}
    assert [call[3:] for call in calls if call[:3] == ["system", "config", "set"]] == [
        ["capabilities.agent_runner", "none"]
    ]
    bootstrap._configure_ai(Path("/fixture/persistent/bin/hz"), {}, ScriptedPrompter([]))
    assert len([call for call in calls if call[:3] == ["system", "config", "set"]]) == 1


def test_declining_ai_deferral_never_silently_changes_bindings(managed_profile):
    _kernel, directory, calls = managed_profile
    bootstrap._configure_ai(Path("/fixture/persistent/bin/hz"), {}, ScriptedPrompter(["n"]))
    assert not (directory / "config.toml").exists()
    assert not any(call[:3] == ["system", "config", "set"] for call in calls)


def test_environment_ai_selection_is_preserved(managed_profile, monkeypatch):
    _kernel, directory, calls = managed_profile
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "gateway")
    monkeypatch.setenv("HZ_CAPABILITIES__AGENT_RUNNER", "command")
    bootstrap._configure_ai(Path("/fixture/persistent/bin/hz"), {}, ScriptedPrompter([]))
    assert not (directory / "config.toml").exists()
    assert not any(call[:3] == ["system", "config", "set"] for call in calls)


def test_noninteractive_missing_profile_is_explicitly_incomplete_without_prompts_or_writes(managed_profile):
    _kernel, _directory, calls = managed_profile
    with pytest.raises(HeinzelError, match="Setup is incomplete"):
        bootstrap._require_profile(Path("/fixture/persistent/bin/hz"), {})
    assert calls == [["org", "show"]]


def test_bare_noninteractive_first_use_stops_incomplete_before_opening(managed_profile, monkeypatch):
    _kernel, _directory, calls = managed_profile
    executable = Path("/fixture/persistent/bin/hz")
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(bootstrap, "installed_app", lambda: executable)
    monkeypatch.setattr(bootstrap, "_workspace_command", lambda *_args: ["ui", "open"])
    launched = []
    monkeypatch.setattr(bootstrap, "_launch_app", lambda *_args: launched.append(True))
    with pytest.raises(SystemExit) as result:
        bootstrap.main()
    assert result.value.code == 1
    assert not launched
    assert calls == [["org", "show"]]
