"""The CLI presents effective configuration without Settings-only section titles."""

from typing import ClassVar

import pytest
from rich.console import Console

from heinzel import Config, Module, ModuleSettings
from heinzel.modules.system.config import ConfigService
from heinzel.modules.system.config._render import render_report

pytestmark = pytest.mark.unit


class PluginSettings(ModuleSettings):
    """Settings contributed by a plugin."""

    section_title: ClassVar[str | None] = "[example]"

    enabled: bool = True


def test_cli_does_not_render_a_settings_section_marker_as_a_heading(make_kernel):
    module = Module("example", "An example plugin.")
    module.settings(PluginSettings)
    report = ConfigService(make_kernel(example=module, config=Config(files=(), data={}))).report()
    console = Console(width=200, record=True, color_system=None)

    console.print(render_report(report))
    output = console.export_text()

    assert "Settings in effect" in output
    assert "example" in output
    assert "[example]" not in output
