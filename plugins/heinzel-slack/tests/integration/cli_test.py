"""Heinzelmännchen's CLI head with this plugin discovered through its real entry point. Offline."""

import json

import pytest
from typer.testing import CliRunner

from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

runner = CliRunner()


def invoke(kernel, *args: str):
    result = runner.invoke(build(kernel), list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_plugin_module_is_discovered_and_its_adapter_accepted(boot):
    kernel = boot()
    assert [r.qualname for r in kernel.modules if r.origin.distribution == "heinzel-slack"] == ["slack"]
    assert "slack" not in kernel.errors


def test_info(boot):
    about = json.loads(invoke(boot(), "slack", "info", "--json"))
    assert about["plugin"] == "heinzel-slack"
    assert about["modules"] == ["slack"]
    assert about["api_url"] == "https://slack.com/api"
    assert about["write_scopes"] == ["chat:write"]


def test_contributes_to_system_info(boot):
    sections = json.loads(invoke(boot(), "system", "info", "--json"))["sections"]
    assert sections["slack"]["plugin"] == "heinzel-slack"


def test_plugin_can_be_disabled(boot):
    kernel = boot(disabled_modules=frozenset({"slack"}))
    assert not [r for r in kernel.modules if r.qualname.startswith("slack")]


def test_settings_come_from_the_environment(boot, monkeypatch):
    monkeypatch.setenv("HZ_SLACK__TOKEN_KIND", "bot")
    assert json.loads(invoke(boot(), "slack", "info", "--json"))["token_kind"] == "bot"


def test_the_kernel_provides_the_real_transport_unless_a_test_binds_a_fake(boot):
    from heinzel_slack import Transport

    assert type(boot().resolve(Transport)) is Transport
