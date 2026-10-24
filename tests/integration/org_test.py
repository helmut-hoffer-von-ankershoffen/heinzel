"""The org module through the CLI head: `hz org show`, the profile check, and the identity step in `hz setup`."""

import tomllib

import pytest
from typer.testing import CliRunner

from heinzel import Prompter
from heinzel.heads.cli import _setup, build
from heinzel.kernel import ScriptedPrompter, user_config_dir
from heinzel.modules.org import _discovery as discovery
from heinzel.modules.org._discovery import Discovered, Login

pytestmark = pytest.mark.integration

runner = CliRunner()


def user_config() -> dict:
    return tomllib.loads((user_config_dir() / "config.toml").read_text(encoding="utf-8"))


def test_show_reports_an_empty_profile_and_what_to_run(hz):
    result = hz("org", "show")
    assert result.exit_code == 0
    assert result.json["complete"] is False
    assert result.json["missing"] == ["org.handle", "org.role", "org.timezone"]
    assert "Missing org.handle, org.role, org.timezone: run `hz setup`." in hz("org", "show", json=False).output


def test_show_reports_the_configured_profile(hz, monkeypatch):
    monkeypatch.setenv("HZ_ORG__HANDLE", "mira")
    monkeypatch.setenv("HZ_ORG__ROLE", "product-manager")
    monkeypatch.setenv("HZ_ORG__TIMEZONE", "Europe/Lisbon")
    monkeypatch.setenv("HZ_ORG__WORKING_HOURS", '"08-16"')
    assert hz("org", "show").json == {
        "handle": "mira",
        "role": "product-manager",
        "timezone": "Europe/Lisbon",
        "working_hours": "08-16",
        "github": None,
        "coding_agent": None,
        "name": None,
        "email": None,
        "complete": True,
        "missing": [],
        "fix": "hz setup",
    }
    doctor = hz("system", "doctor").json["checks"]["org"]
    assert doctor == [
        {"name": "profile", "status": "up", "detail": "mira, product-manager, Europe/Lisbon 08-16", "fix": ""}
    ]


def test_doctor_says_an_incomplete_profile_is_degraded(hz):
    check = hz("system", "doctor").json["checks"]["org"][0]
    assert check["status"] == "degraded"
    assert check["fix"] == "Run `hz setup`"


def test_an_invalid_profile_is_refused_with_the_fix(hz, monkeypatch):
    monkeypatch.setenv("HZ_ORG__ROLE", "ceo")
    result = hz("org", "show", json=False)
    assert result.exit_code == 1
    assert "'ceo' is not a role" in result.output


def test_a_project_file_cant_say_who_you_are(hz, monkeypatch, tmp_path):
    (tmp_path / "hz.toml").write_text('[org]\nhandle = "intruder"\nrole = "engineer"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert hz("org", "show").json["handle"] is None


def test_setup_finds_your_identity_and_asks_role_and_hours_once(make_kernel, monkeypatch, tmp_path):
    monkeypatch.setattr(_setup, "stdin_is_terminal", lambda: True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        discovery,
        "discover",
        lambda: Discovered(
            github=Login(id="Mira-Example"),
            google=Login(id="mira@example.com", name="Mira Example"),
            timezone="Europe/Berlin",
            sources={"github": "your gh login", "google": "your gws login", "timezone": "this machine's clock"},
        ),
    )
    kernel = make_kernel(builtins=True)
    prompter = ScriptedPrompter(["engineering-manager", "08-17"])
    kernel.bind(Prompter, prompter)
    result = runner.invoke(build(kernel), ["setup"])
    assert result.exit_code == 0, result.output
    assert user_config() == {
        "org": {
            "handle": "mira-example",
            "role": "engineering-manager",
            "timezone": "Europe/Berlin",
            "working_hours": [8, 17],
            "github": "Mira-Example",
            "coding_agent": "none",
            "name": "Mira Example",
            "email": "mira@example.com",
        }
    }
    assert "  GitHub login  Mira-Example (your gh login)" in prompter.transcript
    assert "mira-example," in result.output  # doctor's profile check, on the configuration just written
