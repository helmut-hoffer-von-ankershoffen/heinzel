"""Profile/manager protocol doubles: these never contact launchd, systemd or an external provider."""

import json
import plistlib
from pathlib import Path

import pytest

from heinzel import Config, HeinzelError
from heinzel.kernel import ConfigFile
from heinzel.modules.system.daemon._models import DaemonSettings
from heinzel.modules.system.daemon._ownership import (
    launchd_owned,
    profile_digest,
    profile_environment,
    tick_environment,
)
from heinzel.modules.system.daemon._units import launchd_plist, systemd_service

pytestmark = pytest.mark.unit


def test_selected_explicit_profile_and_effective_paths_are_persisted_without_credentials(
    settings, tmp_path, monkeypatch
):
    user, explicit = tmp_path / "config home/config.toml", tmp_path / "chosen.toml"
    config = Config(files=(ConfigFile("user", user), ConfigFile("explicit", explicit)), data={})
    monkeypatch.setenv("HZ_CREDENTIAL_EXAMPLE_TOKEN", "secret-never-in-a-unit")
    profile = profile_environment(settings, DaemonSettings(unit_dir=tmp_path / "units"), config)
    assert profile["HZ_CONFIG"] == str(explicit)
    assert profile["HZ_CONFIG_HOME"] == str(user.parent)
    for field in ("data_dir", "state_dir", "cache_dir", "log_dir"):
        assert profile[f"HZ_{field.upper()}"] == str(getattr(settings, field))
    executable = tmp_path / "bin/hz"
    launchd = plistlib.loads(launchd_plist(executable, 5, settings.log_dir, profile).encode())
    assert launchd["EnvironmentVariables"] == tick_environment(executable, profile)
    assert launchd["WorkingDirectory"] == profile["HOME"]
    systemd = systemd_service(executable, profile)
    assert "secret-never-in-a-unit" not in systemd
    assert f"Environment={json.dumps('HZ_CONFIG=' + str(explicit))}" in systemd
    assert profile_digest(profile) == profile_digest(dict(reversed(list(profile.items()))))
    assert profile_digest(profile) != profile_digest({**profile, "HZ_CONFIG": str(tmp_path / "another.toml")})


@pytest.mark.parametrize("path", ["relative", "/fixture/../other", "/fixture/line\nbreak", "/fixture/control\x00"])
def test_unsupported_profile_routing_refuses_before_any_unit_is_written(settings, tmp_path, path):
    selected = settings.model_copy(update={"state_dir": Path(path)})
    with pytest.raises(HeinzelError, match="absolute profile paths"):
        profile_environment(selected, DaemonSettings(unit_dir=tmp_path / "units"), Config.empty())
    assert not (tmp_path / "units").exists()


@pytest.mark.parametrize("drift", ["program", "argv", "profile", "extra-profile", "directory", "duplicate-program"])
def test_loaded_launchd_identity_includes_exact_argv_and_profile(settings, tmp_path, drift):
    executable, unit = tmp_path / "hz", tmp_path / "job.plist"
    profile = profile_environment(settings, DaemonSettings(unit_dir=tmp_path), Config.empty())
    environment = tick_environment(executable, profile)
    output = (
        f"path = {unit}\nprogram = {executable}\narguments = {{\n{executable}\nsystem\ndaemon\ntick\n}}\n"
        f"working directory = {profile['HOME']}\nenvironment = {{\n"
        + "".join(f"{key} => {value}\n" for key, value in environment.items())
        + "}\n"
    )
    assert launchd_owned(output, unit, executable, environment).registered
    if drift == "program":
        output = output.replace(f"program = {executable}", "program = /foreign")
    elif drift == "argv":
        output = output.replace("tick\n}", "tick\n--job=other:write\n}")
    elif drift == "profile":
        output = output.replace(f"HZ_STATE_DIR => {settings.state_dir}", "HZ_STATE_DIR => /foreign")
    elif drift == "extra-profile":
        output = output.replace("environment = {\n", "environment = {\nHZ_CONFIG => /foreign\n")
    elif drift == "directory":
        output = output.replace(f"working directory = {profile['HOME']}", "working directory = /foreign")
    else:
        output += f"program = {executable}\n"
    with pytest.raises(HeinzelError, match="identity or selected profile"):
        launchd_owned(output, unit, executable, environment)


@pytest.mark.parametrize("pid", ["-", "abc", "12345678901", "-1", "1\npid = 2"])
def test_malformed_loaded_launchd_pid_never_becomes_a_stopped_service(settings, tmp_path, pid):
    executable, unit = tmp_path / "hz", tmp_path / "job.plist"
    profile = profile_environment(settings, DaemonSettings(unit_dir=tmp_path), Config.empty())
    environment = tick_environment(executable, profile)
    output = (
        f"path = {unit}\nprogram = {executable}\narguments = {{\n{executable}\nsystem\ndaemon\ntick\n}}\n"
        f"working directory = {profile['HOME']}\nenvironment = {{\n"
        + "".join(f"{key} => {value}\n" for key, value in environment.items())
        + f"}}\npid = {pid}\n"
    )
    with pytest.raises(HeinzelError, match="identity or selected profile"):
        launchd_owned(output, unit, executable, environment)
