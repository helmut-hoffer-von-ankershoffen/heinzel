"""The ACME end-to-end script keeps its profile isolated and judges the install set."""

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("acme_setup_e2e", ROOT / "scripts/acme_setup_e2e.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_environment_puts_every_directory_under_the_throwaway_home(script, tmp_path):
    env = script.isolated_env(tmp_path, {"HZ_UI_PORT": "8737", "UV_TOOL_DIR": "/live", "PATH": "/bin"})
    assert "HZ_UI_PORT" not in env
    assert env["PATH"] == "/bin"
    for key in (
        "HOME",
        "HZ_CONFIG_HOME",
        "HZ_DATA_DIR",
        "HZ_STATE_DIR",
        "HZ_LOG_DIR",
        "UV_TOOL_DIR",
        "UV_TOOL_BIN_DIR",
    ):
        assert env[key].startswith(str(tmp_path))


def test_a_live_profile_path_is_refused(script):
    with pytest.raises(script.SetupError, match="live hz profile"):
        script.isolated_env(Path("/Users/x/Library/Application Support/hz"))


def test_missing_names_what_acme_did_not_bring(script):
    listing = [
        {"distribution": "heinzel-acme", "version": "0.3.0"},
        {"distribution": "heinzel-github", "version": "0.5.0"},
    ]
    assert script.missing(listing) == ["heinzel-atlassian"]
    assert script.missing({"plugins": [*listing, {"distribution": "heinzel-atlassian", "version": "0.4.0"}]}) == []


def written(**bindings):
    return [
        {"capability": capability, "adapter": adapter, "state": "written", "reason": "", "by": "acme"}
        for capability, adapter in bindings.items()
    ]


def test_wrong_bindings_names_each_capability_acme_did_not_bind(script):
    change = {"bindings": written(**{**script.ACME_BINDINGS, "mail": "imap"})}
    assert script.wrong_bindings(change, script.ACME_BINDINGS) == ["mail: ('written', 'imap')"]
    assert script.wrong_bindings({"bindings": written(**script.ACME_BINDINGS)}, script.ACME_BINDINGS) == []


def test_the_persons_binding_must_be_kept_with_their_adapter_named(script):
    kept = {"capability": "tracker", "adapter": "atlassian", "state": "kept", "reason": "yours: github", "by": "acme"}
    assert script.wrong_bindings({"bindings": [kept]}, {"tracker": "atlassian"}, kept={"tracker": "github"}) == []
    overwritten = {"bindings": written(tracker="atlassian")}
    [problem] = script.wrong_bindings(overwritten, {"tracker": "atlassian"}, kept={"tracker": "github"})
    assert problem.startswith("tracker not kept as github")


def test_only_owner_only_rows_may_stay_open(script):
    checklist = {
        "rows": [
            {"connector": "github", "state": "not-connected", "line": "Not signed in", "owner_only": True},
            {"connector": "zoom", "state": "not-installed", "line": "Not installed", "owner_only": False},
            {"connector": "google", "state": "connected", "line": "Connected", "owner_only": False},
        ]
    }
    assert script.not_owner_only(checklist) == ["zoom: not-installed (Not installed)"]
    assert script.open_rows(checklist) == ["github", "zoom"]


def test_the_fake_clis_go_first_on_path_and_sign_in_into_the_throwaway_home(script, tmp_path):
    import subprocess

    env = script.with_fakes(script.isolated_env(tmp_path, {"PATH": "/usr/bin:/bin"}))
    assert env["PATH"].startswith(str(script.FAKE_CLIS))
    for command in script.FAKE_SIGN_INS:
        subprocess.run(
            [str(script.FAKE_CLIS / command[0]), *command[1:]], env=env, check=True, capture_output=True, timeout=30
        )
    assert (tmp_path / "config" / "gh" / "hosts.yml").read_text().startswith("github.com:")
    assert (tmp_path / ".config" / "gws" / "credentials.json").is_file()
    assert (tmp_path / ".fake-acli" / "login").is_file()
    version = subprocess.run(
        [str(script.FAKE_CLIS / "acli"), "--version"], env=env, check=True, capture_output=True, text=True, timeout=30
    )
    assert version.stdout.startswith("acli version 1.3.39")
