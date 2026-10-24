import tomllib

import pytest
from pydantic import Field

from heinzel import Config, HeinzelError, Module, ModuleSettings
from heinzel.modules.system.config import ConfigService

pytestmark = pytest.mark.unit


class ExampleSettings(ModuleSettings):
    """Settings of the example module."""

    greeting: str = "Hello"
    nickname: str | None = None
    zones: list[str] = Field(default_factory=lambda: ["Europe/Berlin", "Asia/Tokyo"])
    loud: bool = False
    retries: int = 3


@pytest.fixture
def kernel(make_kernel):
    module = Module("example", "x")
    module.settings(ExampleSettings)
    return make_kernel(example=module, config=Config(files=(), data={"example": {"greeting": "Hi"}}))


def test_report_lists_every_setting_with_its_environment_variable(kernel):
    settings = {(s.section, s.key): s for s in ConfigService(kernel).report().settings}
    assert settings["hz", "log_level"].env == "HZ_LOG_LEVEL"
    assert settings["example", "greeting"].value == "Hi"
    assert settings["example", "greeting"].env == "HZ_EXAMPLE__GREETING"


def test_init_writes_valid_toml_with_every_setting_commented(kernel, tmp_path):
    path = ConfigService(kernel).init(tmp_path / "nested" / "config.toml")
    text = path.read_text(encoding="utf-8")
    assert '# greeting = "Hi"' in text
    assert "[example]" in text
    uncommented = "\n".join(line.removeprefix("# ") if " = " in line else line for line in text.splitlines())
    parsed = tomllib.loads(uncommented)
    assert parsed["hz"]["plugins_enabled"] is True
    assert parsed["example"]["greeting"] == "Hi"
    assert "# nickname: not set by default" in text


def test_init_refuses_to_overwrite_without_force(kernel, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(HeinzelError, match="already exists"):
        ConfigService(kernel).init(path)
    ConfigService(kernel).init(path, force=True)
    assert path.read_text(encoding="utf-8") != "keep"


def test_get_set_and_unset(kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    service = ConfigService(kernel)
    assert service.get("example.greeting").value == "Hi"

    path = service.set("example.greeting", "Moin")
    service.set("hz.disabled_modules", '["a", "b"]')
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed == {"example": {"greeting": "Moin"}, "hz": {"disabled_modules": ["a", "b"]}}

    path.write_text("# keep me\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    service.unset("example.greeting")
    assert path.read_text(encoding="utf-8").startswith("# keep me")
    assert "greeting" not in path.read_text(encoding="utf-8")


def test_set_validates_and_rejects_unknown_keys(kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    service = ConfigService(kernel)
    with pytest.raises(HeinzelError, match=r"Invalid value for hz\.log_level"):
        service.set("hz.log_level", "LOUD")
    with pytest.raises(HeinzelError, match=r"Unknown setting 'hz\.nope'"):
        service.set("hz.nope", "1")
    with pytest.raises(HeinzelError, match="is not set"):
        service.unset("hz.log_level")


def test_project_scope_writes_hz_toml(kernel, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ConfigService(kernel).set("hz.log_level", "ERROR", scope="project") == tmp_path / "hz.toml"


@pytest.mark.parametrize(
    ("key", "value", "example"),
    [
        (
            "example.zones",
            "Europe/Berlin,Asia/Tokyo",
            """hz system config set example.zones '["Europe/Berlin", "Asia/Tokyo"]'""",
        ),
        ("example.loud", "maybe", "hz system config set example.loud false"),
        ("example.retries", "many", "hz system config set example.retries 3"),
        ("hz.log_level", "LOUD", "hz system config set hz.log_level DEBUG"),
    ],
)
def test_an_invalid_value_shows_a_command_line_for_the_same_key(kernel, key, value, example):
    with pytest.raises(HeinzelError) as caught:
        ConfigService(kernel).set(key, value)
    assert str(caught.value).endswith(f"For example:\n  {example}")


def test_an_empty_list_setting_still_gets_an_example(make_kernel):
    class Lists(ModuleSettings):
        """Settings with an empty list."""

        names: list[str] = Field(default_factory=list)

    module = Module("lists", "x")
    module.settings(Lists)
    with pytest.raises(HeinzelError, match=r"""set lists\.names '\["first", "second"\]'"""):
        ConfigService(make_kernel(lists=module)).set("lists.names", "a,b")
