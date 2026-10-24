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
    assert [r.qualname for r in kernel.modules if r.origin.distribution == "heinzel-zoom"] == ["zoom"]
    assert "zoom" not in kernel.errors


def test_info(boot):
    about = json.loads(invoke(boot(), "zoom", "info", "--json"))
    assert about["plugin"] == "heinzel-zoom"
    assert about["modules"] == ["zoom"]
    assert about["query"] == "from:zoom.us"
    assert "summary emails" in about["source"]


def test_contributes_to_system_info(boot):
    sections = json.loads(invoke(boot(), "system", "info", "--json"))["sections"]
    assert sections["zoom"]["plugin"] == "heinzel-zoom"


def test_plugin_can_be_disabled(boot):
    kernel = boot(disabled_modules=frozenset({"zoom"}))
    assert not [r for r in kernel.modules if r.qualname.startswith("zoom")]


def test_the_query_is_configurable_through_the_environment(boot, monkeypatch):
    monkeypatch.setenv("HZ_ZOOM__QUERY", "from:zoom.us label:Meetings")
    assert json.loads(invoke(boot(), "zoom", "info", "--json"))["query"] == "from:zoom.us label:Meetings"
