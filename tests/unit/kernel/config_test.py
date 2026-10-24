from pathlib import Path

import pytest

from heinzel import Config, HeinzelError, ModuleSettings, Settings
from heinzel.kernel import ConfigFile, env_prefix
from heinzel.kernel._config import config_files, user_config_dir

pytestmark = pytest.mark.unit


class ExampleSettings(ModuleSettings):
    greeting: str = "Hello"
    times: int = 1


def config(data: dict) -> Config:
    return Config(files=(), data=data)


@pytest.mark.parametrize(
    ("section", "prefix"),
    [
        ("hz", "HZ_"),
        ("example", "HZ_EXAMPLE__"),
        ("system.changelog", "HZ_SYSTEM_CHANGELOG__"),
        ("a-b", "HZ_A_B__"),
    ],
)
def test_env_prefix(section, prefix):
    assert env_prefix(section) == prefix


def test_section_follows_dotted_names():
    data = {"system": {"level": 1, "changelog": {"x": 2}}, "flat": "not a table"}
    assert config(data).section("system.changelog") == {"x": 2}
    assert config(data).section("missing.deeper") == {}
    assert config(data).section("flat") == {}


def test_module_settings_layering(monkeypatch):
    cfg = config({"example": {"greeting": "Hi", "times": 2, "unknown": True}})
    assert cfg.settings(ExampleSettings, "example") == ExampleSettings(greeting="Hi", times=2)

    monkeypatch.setenv("HZ_EXAMPLE__TIMES", "5")
    settings = cfg.settings(ExampleSettings, "example")
    assert (settings.greeting, settings.times) == ("Hi", 5)
    assert isinstance(settings, ExampleSettings)


def test_kernel_settings_come_from_the_hz_table(monkeypatch):
    assert config({"hz": {"log_level": "ERROR"}}).settings(Settings, "hz").log_level == "ERROR"
    monkeypatch.setenv("HZ_LOG_LEVEL", "INFO")
    assert config({"hz": {"log_level": "ERROR"}}).settings(Settings, "hz").log_level == "INFO"


def test_user_config_dir_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path / "hz-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert user_config_dir() == tmp_path / "hz-home"
    monkeypatch.delenv("HZ_CONFIG_HOME")
    assert user_config_dir() == tmp_path / "xdg" / "hz"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert user_config_dir().name == "hz"


def test_config_files_in_precedence_order(monkeypatch, tmp_path):
    project = tmp_path / "repo"
    (project / "deep" / "er").mkdir(parents=True)
    (project / "hz.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG", str(tmp_path / "explicit.toml"))

    files = config_files(project / "deep" / "er")

    assert [f.scope for f in files] == ["site", "user", "project", "explicit"]
    assert files[2] == ConfigFile("project", project / "hz.toml")
    assert files[2].exists
    assert not files[3].exists


def test_load_merges_files_deeply(monkeypatch, tmp_path):
    user = Path(str(tmp_path / "user"))
    user.mkdir()
    (user / "config.toml").write_text(
        '[hz]\nlog_level = "INFO"\n[example]\ngreeting = "Hi"\ntimes = 2\n', encoding="utf-8"
    )
    (tmp_path / "hz.toml").write_text("[example]\ntimes = 3\n", encoding="utf-8")
    monkeypatch.setenv("HZ_CONFIG_HOME", str(user))

    loaded = Config.load(tmp_path)

    assert loaded.section("example") == {"greeting": "Hi", "times": 3}
    assert loaded.section("hz") == {"log_level": "INFO"}


def test_invalid_toml_is_a_heinzel_error(monkeypatch, tmp_path):
    (tmp_path / "hz.toml").write_text("not = [valid", encoding="utf-8")
    with pytest.raises(HeinzelError, match="not valid TOML"):
        Config.load(tmp_path)
