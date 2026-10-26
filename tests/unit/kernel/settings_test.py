import pytest
from pydantic import ValidationError

from heinzel import Config, Settings

pytestmark = pytest.mark.unit


def load() -> Settings:
    return Config.empty().settings(Settings, "hz")


def test_defaults_use_platform_directories(monkeypatch):
    monkeypatch.delenv("HZ_DATA_DIR")
    monkeypatch.delenv("HZ_CACHE_DIR")
    monkeypatch.delenv("HZ_STATE_DIR")
    monkeypatch.delenv("HZ_LOG_DIR")
    settings = load()
    assert settings.data_dir.name == "hz"
    assert settings.cache_dir.name == "hz"
    assert "hz" in settings.state_dir.parts
    assert "hz" in settings.log_dir.parts
    assert settings.log_level == "WARNING"
    assert settings.plugins_enabled is True
    assert settings.disabled_modules == frozenset()


def test_environment_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HZ_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("HZ_PLUGINS_ENABLED", "false")
    monkeypatch.setenv("HZ_DISABLED_MODULES", "example, system.changelog,")
    settings = load()
    assert settings.data_dir == tmp_path
    assert settings.log_level == "DEBUG"
    assert settings.plugins_enabled is False
    assert settings.disabled_modules == {"example", "system.changelog"}


def test_rejects_unknown_log_level(monkeypatch):
    monkeypatch.setenv("HZ_LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError, match="log_level"):
        load()


def test_settings_are_immutable():
    with pytest.raises(ValidationError):
        load().log_level = "DEBUG"  # type: ignore[misc]


def test_external_https_origins_are_canonical_and_user_only(monkeypatch):
    monkeypatch.setenv("HZ_UI_ALLOWED_ORIGINS", "https://Workstation.Example:443/,https://workstation.example:9443")
    settings = load()
    assert settings.ui_allowed_origins == {"https://workstation.example", "https://workstation.example:9443"}
    assert "ui_allowed_origins" in Settings.ignored_in_project


@pytest.mark.parametrize(
    "origin",
    [
        "http://workstation.example",
        "https://*.example",
        "https://user:secret@workstation.example",
        "https://workstation.example/path",
        "https://workstation.example?query=1",
        "https://workstation.example#part",
        "https://workstation.example\\bad",
        "https://workstation.example\n",
        "https://workstation.example\x01",
        "https://workstation.example:0",
        "https://workstation.example:65536",
        "https://",
        "https://.",
        "https://..",
        "https://-bad.example",
        "https://bad..example",
        "null",
        "https://workstation%2eexample",
    ],
)
def test_external_https_origins_refuse_unsafe_or_non_origin_values(monkeypatch, origin):
    monkeypatch.setenv("HZ_UI_ALLOWED_ORIGINS", origin)
    with pytest.raises(ValidationError, match="ui_allowed_origins"):
        load()
