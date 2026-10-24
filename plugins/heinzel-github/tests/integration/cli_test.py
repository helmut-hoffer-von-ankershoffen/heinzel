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


def test_plugin_module_is_discovered_and_its_adapters_accepted(boot):
    kernel = boot()
    assert [r.qualname for r in kernel.modules if r.origin.distribution == "heinzel-github"] == ["github"]
    assert "github" not in kernel.errors


def test_info(boot):
    about = json.loads(invoke(boot(), "github", "info", "--json"))
    assert about["plugin"] == "heinzel-github"
    assert about["modules"] == ["github"]
    assert about["api_url"] == "https://api.github.com"


def test_contributes_to_system_info(boot):
    sections = json.loads(invoke(boot(), "system", "info", "--json"))["sections"]
    assert sections["github"]["plugin"] == "heinzel-github"


def test_plugin_can_be_disabled(boot):
    kernel = boot(disabled_modules=frozenset({"github"}))
    assert not [r for r in kernel.modules if r.qualname.startswith("github")]


def test_the_api_url_is_configurable_through_the_environment(boot, monkeypatch):
    monkeypatch.setenv("HZ_GITHUB__API_URL", "https://ghe.example.com/api/v3")
    assert json.loads(invoke(boot(), "github", "info", "--json"))["api_url"] == "https://ghe.example.com/api/v3"


def test_the_kernel_provides_the_real_transport_unless_a_test_binds_a_fake(boot):
    from heinzel_github import Transport

    assert type(boot().resolve(Transport)) is Transport
