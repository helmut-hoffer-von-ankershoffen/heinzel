"""`hz system config show` says where each value comes from and what its default is (the web app's Settings screen)."""

from typing import ClassVar

import pytest
from pydantic import Field

from heinzel import Config, Module, ModuleSettings
from heinzel.kernel import ConfigFile
from heinzel.modules.system.config import ConfigService

pytestmark = pytest.mark.unit


class ExampleSettings(ModuleSettings):
    """Settings of the example module."""

    greeting: str = "Hello"
    nickname: str | None = None
    zones: list[str] = Field(default_factory=lambda: ["Europe/Berlin", "Asia/Tokyo"])
    loud: bool = False
    retries: int = 3


class GuardedSettings(ModuleSettings):
    """Settings a project file may not set."""

    command: str = "safe"
    ignored_in_project: ClassVar[frozenset[str]] = frozenset({"command"})


def layered(tmp_path, section: str, user: dict[str, object], project: dict[str, object]) -> Config:
    files = (ConfigFile("user", tmp_path / "user.toml"), ConfigFile("project", tmp_path / "hz.toml"))
    return Config(
        files=files,
        data={section: user | project},
        layers=tuple(zip(files, ({section: user}, {section: project}), strict=True)),
    )


def test_each_value_names_the_layer_it_comes_from_and_its_default(make_kernel, monkeypatch, tmp_path):
    module = Module("example", "x")
    module.settings(ExampleSettings)
    config = layered(tmp_path, "example", {"greeting": "Hi", "loud": True}, {"loud": False})
    monkeypatch.setenv("HZ_EXAMPLE__RETRIES", "5")
    settings = {
        s.key: s
        for s in ConfigService(make_kernel(example=module, config=config)).report().settings
        if s.section == "example"
    }
    assert (settings["greeting"].source, settings["greeting"].default) == ("user", "Hello")
    assert settings["loud"].source == "project"  # the last file that sets it wins
    assert (settings["retries"].source, settings["retries"].value) == ("env", 5)
    assert (settings["nickname"].source, settings["nickname"].default) == ("default", None)
    assert settings["zones"].default == ["Europe/Berlin", "Asia/Tokyo"]  # a factory's default, computed


def test_a_value_a_project_file_may_not_set_comes_from_the_layer_below(make_kernel, tmp_path):
    module = Module("guarded", "x")
    module.settings(GuardedSettings)
    config = layered(tmp_path, "guarded", {"command": "mine"}, {"command": "theirs"})
    settings = {s.key: s for s in ConfigService(make_kernel(guarded=module, config=config)).report().settings}
    assert (settings["command"].value, settings["command"].source) == ("mine", "user")


def test_the_report_reads_the_files_afresh_so_a_change_shows_at_once(make_kernel, tmp_path):
    module = Module("example", "x")
    module.settings(ExampleSettings)
    project = tmp_path / "hz.toml"
    project.write_text('[example]\ngreeting = "Hi"\n', encoding="utf-8")
    service = ConfigService(make_kernel(example=module, config=Config.load(tmp_path)))
    project.write_text('[example]\ngreeting = "Moin"\nretries = "many"\n', encoding="utf-8")
    settings = {s.key: s for s in service.report().settings if s.section == "example"}
    assert settings["greeting"].value == "Hi"  # a value the settings refuse keeps what was booted
    project.write_text('[example]\ngreeting = "Moin"\n', encoding="utf-8")
    settings = {s.key: s for s in service.report().settings if s.section == "example"}
    assert (settings["greeting"].value, settings["greeting"].source) == ("Moin", "project")
