import tomllib
from pathlib import Path

import pytest

from heinzel import Config, HeinzelError, ModuleSettings, Settings
from heinzel.kernel import ConfigEditor, ConfigFile, env_prefix, toml_value
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


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        (True, "true"),
        (3, "3"),
        ("Grüße", '"Grüße"'),
        (["a", 1], '["a", 1]'),
        ((9, 18), "[9, 18]"),
        ({}, "{}"),
        ({"name": "Asia/Tokyo", "hours": [10, 19]}, '{ name = "Asia/Tokyo", hours = [10, 19] }'),
        ({"Europe/Berlin": 1, "": 2}, '{ "Europe/Berlin" = 1, "" = 2 }'),
    ],
)
def test_toml_value_writes_literals_tomllib_reads_back(value, literal):
    assert toml_value(value) == literal
    assert tomllib.loads(f"v = {literal}")["v"] == (list(value) if isinstance(value, tuple) else value)


def test_the_config_editor_is_an_interface_system_config_implements():
    with pytest.raises(NotImplementedError):
        ConfigEditor().write("clock.zones", [])
    with pytest.raises(NotImplementedError):
        ConfigEditor().unset("clock.zones")


def test_a_bare_carriage_return_is_invalid_toml_not_a_newline(tmp_path):
    """TOML allows CRLF but not a lone CR; reading in text mode would translate it into a valid newline."""
    (tmp_path / "hz.toml").write_bytes(b"a = 1\rb = 2\n")
    with pytest.raises(HeinzelError, match="not valid TOML"):
        Config.load(tmp_path)


def test_crlf_line_endings_load(tmp_path):
    (tmp_path / "hz.toml").write_bytes(b"a = 1\r\nb = 2\r\n")
    assert Config.load(tmp_path).data == {"a": 1, "b": 2}


def test_reread_reads_the_same_files_again(tmp_path):
    (tmp_path / "hz.toml").write_text('[example]\ngreeting = "Hi"\n', encoding="utf-8")
    booted = Config.load(tmp_path)
    (tmp_path / "hz.toml").write_text('[example]\ngreeting = "Moin"\n', encoding="utf-8")
    assert booted.settings(ExampleSettings, "example").greeting == "Hi"
    assert booted.reread().settings(ExampleSettings, "example").greeting == "Moin"


def test_reread_keeps_a_configuration_built_in_code():
    built = Config(files=(ConfigFile("user", Path("/nowhere/hz.toml")),), data={"example": {"times": 3}})
    assert built.reread() is built
    assert config({"example": {"times": 2}}).reread().settings(ExampleSettings, "example").times == 2
