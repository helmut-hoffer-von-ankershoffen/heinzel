"""The time zone of the daypart (ADR 0060): `[org] timezone`, never from a project file, else the machine's."""

import pytest

from heinzel import Module, Theme
from heinzel.kernel import DEFAULT_THEME, user_config_dir
from heinzel.modules.system.theme import _service

pytestmark = pytest.mark.unit

LOCAL_TIMEZONE = _service.local_timezone


@pytest.fixture(autouse=True)
def machine(monkeypatch):
    monkeypatch.delenv("HZ_ORG__TIMEZONE", raising=False)
    monkeypatch.setattr(_service, "local_timezone", lambda: "Asia/Tokyo")


def user_file(text: str) -> None:
    user_config_dir().mkdir(parents=True, exist_ok=True)
    (user_config_dir() / "config.toml").write_text(text, encoding="utf-8")


def offering(name, priority):
    module = Module(name, "A synthetic theme provider.")
    module.theme(
        Theme.model_validate(
            DEFAULT_THEME.model_dump()
            | {
                "name": name,
                "title": name.title(),
                "default_priority": priority,
            }
        )
    )
    return module


def test_theme_provider_priority_selects_one_default_without_a_duplicate_fallback(make_kernel):
    service = make_kernel(builtins=True, alpha=offering("alpha", 50), beta=offering("beta", 100)).resolve(
        _service.ThemeService
    )
    listed = service.listing()
    assert listed.selected == "beta"
    assert [item.name for item in listed.themes] == ["alpha", "beta"]
    assert [item.name for item in listed.themes if item.selected] == ["beta"]
    assert not (user_config_dir() / "config.toml").exists()


def test_equal_default_priorities_are_stable_by_module_identity(make_kernel):
    service = make_kernel(builtins=True, beta=offering("beta", 100), alpha=offering("alpha", 100)).resolve(
        _service.ThemeService
    )
    assert service.selected() == "alpha"


@pytest.mark.parametrize("name", ["alpha", "default"])
def test_available_explicit_choices_win_over_provider_preferences(make_kernel, name):
    user_file(f'[system.theme]\nname = "{name}"\n')
    service = make_kernel(builtins=True, alpha=offering("alpha", 1), beta=offering("beta", 100)).resolve(
        _service.ThemeService
    )
    listed = service.listing()
    assert listed.selected == name
    assert name in [item.name for item in listed.themes]


@pytest.mark.parametrize("where", ["file", "environment"])
def test_missing_theme_uses_the_provider_recommendation_without_rewriting_the_choice(make_kernel, monkeypatch, where):
    if where == "file":
        user_file('[system.theme]\nname = "missing-brand"\n')
    else:
        monkeypatch.setenv(_service.VARIABLE, "missing-brand")
    service = make_kernel(builtins=True, alpha=offering("alpha", 100)).resolve(_service.ThemeService)
    listed = service.listing()
    assert listed.selected == "alpha"
    assert "missing-brand" in listed.note
    assert "Alpha is in effect" in listed.note
    assert [item.name for item in listed.themes] == ["alpha"]
    if where == "file":
        assert 'name = "missing-brand"' in (user_config_dir() / "config.toml").read_text()
    else:
        assert listed.next == [f"unset {_service.VARIABLE}"]


def test_no_recommended_provider_keeps_core_independently_usable(make_kernel):
    service = make_kernel(builtins=True, alpha=offering("alpha", None)).resolve(_service.ThemeService)
    listed = service.listing()
    assert listed.selected == "default"
    assert listed.themes[0].title == "Built-in fallback"


def test_disabled_provider_cannot_recommend_a_theme(make_kernel, settings):
    configured = settings.model_copy(update={"disabled_modules": frozenset({"alpha"})})
    service = make_kernel(
        builtins=True, settings_=configured, alpha=offering("alpha", 100), beta=offering("beta", 1)
    ).resolve(_service.ThemeService)
    assert service.selected() == "beta"


def test_provider_removal_recovers_to_core_without_a_saved_provider_name(make_kernel):
    provider = make_kernel(builtins=True, alpha=offering("alpha", 100)).resolve(_service.ThemeService)
    assert provider.selected() == "alpha"
    assert make_kernel(builtins=True).resolve(_service.ThemeService).selected() == "default"


def test_the_user_file_decides_the_zone():
    user_file('[org]\ntimezone = "Europe/Lisbon"\n')
    assert _service._zone()[1] == "Europe/Lisbon"


def test_the_environment_wins_over_the_files(monkeypatch):
    user_file('[org]\ntimezone = "Europe/Lisbon"\n')
    monkeypatch.setenv("HZ_ORG__TIMEZONE", "America/Chicago")
    assert _service._zone()[1] == "America/Chicago"


def test_a_project_file_cannot_set_the_zone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hz.toml").write_text('[org]\ntimezone = "Pacific/Auckland"\n', encoding="utf-8")
    assert _service._zone()[1] == "Asia/Tokyo"


@pytest.mark.parametrize("zone", ["Mars/Olympus", "posixrules", "../etc/passwd", 42])
def test_a_zone_that_is_no_iana_name_falls_back_to_the_machines(zone, monkeypatch):
    monkeypatch.setenv("HZ_ORG__TIMEZONE", str(zone))
    assert _service._zone()[1] == "Asia/Tokyo"


def test_a_broken_configuration_file_falls_back_to_the_machines():
    user_file("[org\n")
    assert _service._zone()[1] == "Asia/Tokyo"


def test_without_any_zone_it_is_utc(monkeypatch):
    monkeypatch.setattr(_service, "local_timezone", lambda: None)
    assert _service._zone()[1] == "UTC"


def test_the_machines_zone_is_where_localtime_points(tmp_path, monkeypatch):
    zoneinfo = tmp_path / "zoneinfo" / "Europe"
    zoneinfo.mkdir(parents=True)
    (zoneinfo / "Berlin").write_bytes(b"")
    (tmp_path / "localtime").symlink_to(zoneinfo / "Berlin")
    monkeypatch.setattr(_service, "LOCALTIME", tmp_path / "localtime")
    assert LOCAL_TIMEZONE() == "Europe/Berlin"
    monkeypatch.setattr(_service, "LOCALTIME", tmp_path / "missing")
    assert LOCAL_TIMEZONE() is None
