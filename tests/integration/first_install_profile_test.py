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
        calls.append(arguments)
        result = CliRunner().invoke(build(refreshed), [*arguments, "--json"])
        if result.exit_code:
            msg = "Managed command refused invalid configuration"
            raise HeinzelError(msg)
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
    assert calls == [["org", "show"], ["system", "config", "show"]]


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
    assert calls == [["org", "show"], ["system", "config", "show"]]


@pytest.mark.parametrize(
    ("name", "team"),
    [
        (None, "Payments"),
        ("Mira", None),
        ("", "Payments"),
        ("Mira", ""),
        ("  ", "Payments"),
        ("Mira", "\t "),
    ],
)
def test_noninteractive_health_complete_profile_still_requires_real_name_and_team(
    managed_profile, monkeypatch, name, team
):
    _kernel, directory, calls = managed_profile
    directory.mkdir(parents=True, exist_ok=True)
    text = '[org]\nhandle="mira"\nrole="engineer"\ntimezone="Europe/Berlin"\n'
    if name is not None:
        text += f"name={json.dumps(name)}\n"
    if team is not None:
        text += f"[team]\nname={json.dumps(team)}\n"
    configuration = directory / "config.toml"
    configuration.write_text(text)
    before = configuration.read_bytes()
    executable = Path("/fixture/persistent/bin/hz")
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(bootstrap, "installed_app", lambda: executable)
    monkeypatch.setattr(bootstrap, "_workspace_command", lambda *_args: ["ui", "open"])
    opened, mutations = [], []
    monkeypatch.setattr(bootstrap, "_launch_app", lambda *_args: opened.append(True))
    monkeypatch.setattr(bootstrap, "_install_services", lambda *_args: mutations.append(True))
    monkeypatch.setattr(bootstrap, "_complete_profile", lambda *_args: pytest.fail("Noninteractive profile prompt"))
    with pytest.raises(SystemExit) as result:
        bootstrap.main()
    assert result.value.code == 1
    assert opened == mutations == []
    assert configuration.read_bytes() == before
    assert calls == [["org", "show"], ["system", "config", "show"]]


def test_complete_noninteractive_profile_is_preserved_and_can_open_without_prompt(managed_profile, monkeypatch):
    _kernel, directory, calls = managed_profile
    directory.mkdir(parents=True, exist_ok=True)
    configuration = directory / "config.toml"
    configuration.write_text(
        '[org]\nname="Mira"\nhandle="mira"\nrole="engineer"\ntimezone="Europe/Berlin"\n[team]\nname="Payments"\n'
    )
    before = configuration.read_bytes()
    executable = Path("/fixture/persistent/bin/hz")
    monkeypatch.setattr(bootstrap.sys, "argv", ["heinzel"])
    monkeypatch.setattr(bootstrap.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(bootstrap, "installed_app", lambda: executable)
    monkeypatch.setattr(bootstrap, "_workspace_command", lambda *_args: ["ui", "open"])
    opened = []
    monkeypatch.setattr(bootstrap, "_launch_app", lambda *_args: opened.append(True))
    monkeypatch.setattr(bootstrap, "_complete_profile", lambda *_args: pytest.fail("Noninteractive profile prompt"))
    bootstrap.main()
    assert opened == [True]
    assert configuration.read_bytes() == before
    assert calls == [["org", "show"], ["system", "config", "show"]]


def test_explicit_environment_profile_completes_noninteractive_requirements_without_writes(
    managed_profile, monkeypatch
):
    _kernel, directory, calls = managed_profile
    for key, value in {
        "HZ_ORG__NAME": "Mira Example",
        "HZ_ORG__HANDLE": "mira-example",
        "HZ_ORG__ROLE": "engineer",
        "HZ_ORG__TIMEZONE": "Europe/Berlin",
        "HZ_TEAM__NAME": "Example Team",
    }.items():
        monkeypatch.setenv(key, value)
    bootstrap._require_profile(Path("/fixture/persistent/bin/hz"), {})
    assert not (directory / "config.toml").exists()
    assert calls == [["org", "show"], ["system", "config", "show"]]


@pytest.mark.parametrize(
    "problem", ["duplicate", "blank-source", "missing-value", "invalid-value", "invalid-schema", "missing-field"]
)
@pytest.mark.parametrize("interactive", [False, True])
def test_both_profile_paths_refuse_ambiguous_or_invalid_rows_before_questions_or_writes(
    monkeypatch, problem, interactive
):
    settings = [
        {"section": "org", "key": "name", "source": "default", "value": "Mira", "json_schema": {"type": "string"}},
        {"section": "team", "key": "name", "source": "default", "value": "Payments", "json_schema": {"type": "string"}},
    ]
    if problem == "duplicate":
        settings.append(dict(settings[0]))
    elif problem == "blank-source":
        settings[0]["source"] = " "
    elif problem == "missing-value":
        del settings[0]["value"]
    elif problem == "invalid-value":
        settings[0]["value"] = True
    elif problem == "invalid-schema":
        settings[0]["value"] = None
        settings[1]["json_schema"] = {"enum": [True]}
    else:
        settings.pop()
    calls = []

    def managed(_executable, _environment, arguments):
        calls.append(arguments)
        return {"missing": []} if arguments == ["org", "show"] else {"settings": settings}

    monkeypatch.setattr(bootstrap, "_managed_json", managed)

    def check():
        if interactive:
            bootstrap._complete_profile(Path("/fixture/persistent/bin/hz"), {}, ScriptedPrompter([]))
        else:
            bootstrap._require_profile(Path("/fixture/persistent/bin/hz"), {})

    with pytest.raises(HeinzelError, match=r"could not be verified|is unavailable|is invalid"):
        check()
    assert calls == [["org", "show"], ["system", "config", "show"]]
