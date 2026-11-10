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


def test_only_an_executable_named_hz_is_run(script, tmp_path):
    hz = tmp_path / "hz"
    hz.write_text("#!/bin/sh\n")
    hz.chmod(0o755)
    assert script.executable(str(hz)) == str(hz.resolve())
    other = tmp_path / "sh"
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    for path in (str(other), str(tmp_path / "missing" / "hz")):
        with pytest.raises(script.SetupError):
            script.executable(path)
    with pytest.raises(script.SetupError, match="never passes"):
        script.run_hz(str(hz.resolve()), {}, "system", "; rm -rf /")
