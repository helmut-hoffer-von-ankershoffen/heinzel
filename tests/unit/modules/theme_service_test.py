"""The time zone of the daypart (ADR 0060): `[org] timezone`, never from a project file, else the machine's."""

import pytest

from heinzel.kernel import user_config_dir
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
