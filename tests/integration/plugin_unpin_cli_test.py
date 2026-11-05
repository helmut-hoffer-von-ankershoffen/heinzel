"""The real CLI preview binds the confirmed plugin unpin without reinstalling it."""

import json

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build
from heinzel.modules.system.plugin._service import PluginService
from tests.unit.modules.plugin_service_test import isolated_python, tool_env
from tests.unit.modules.plugin_unpin_test import NAME, pinned

__all__ = ["isolated_python", "pinned", "tool_env"]
pytestmark = pytest.mark.integration


def test_cli_shows_copyable_exact_confirmation_and_keeps_installed_version(make_kernel, pinned):
    service, installation, receipt, _, _ = pinned
    kernel = make_kernel(builtins=True)
    kernel._instances[PluginService] = service
    app = build(kernel)
    runner = CliRunner()
    command = ["system", "plugin", "unpin", NAME]
    before = receipt.read_bytes()
    preview = runner.invoke(app, [*command, "--json"])
    assert preview.exit_code == 0, preview.output
    displayed = json.loads(preview.output)
    assert not displayed["done"]
    assert displayed["version"] == "1.0.0"
    assert receipt.read_bytes() == before
    assert displayed["next"] == [f"hz system plugin unpin {NAME} --confirm {displayed['confirmation']}"]
    result = runner.invoke(app, [*command, "--confirm", displayed["confirmation"], "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["done"]
    assert installation.installed_versions([NAME]) == {NAME: "1.0.0"}
    assert NAME in installation.requirements()
