"""Heinzelmännchen's CLI head with this plugin discovered through its real entry point. Offline."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import Config, Kernel, Settings
from heinzel.heads.cli import build
from heinzel.kernel import discover

pytestmark = pytest.mark.integration

runner = CliRunner()


def invoke(kernel, *args: str):
    result = runner.invoke(build(kernel), list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_plugin_module_is_discovered_and_its_adapters_accepted(boot):
    kernel = boot()
    assert [r.qualname for r in kernel.modules if r.origin.distribution == "heinzel-atlassian"] == ["atlassian"]
    assert "atlassian" not in kernel.errors


def test_info(boot):
    about = json.loads(invoke(boot(), "atlassian", "info", "--json"))
    assert about["plugin"] == "heinzel-atlassian"
    assert about["modules"] == ["atlassian"]
    assert about["site_url"] is None


def test_contributes_to_system_info(boot):
    sections = json.loads(invoke(boot(), "system", "info", "--json"))["sections"]
    assert sections["atlassian"]["plugin"] == "heinzel-atlassian"


def test_plugin_can_be_disabled(boot):
    kernel = boot(disabled_modules=frozenset({"atlassian"}))
    assert not [r for r in kernel.modules if r.qualname.startswith("atlassian")]


def test_the_site_and_email_are_configurable_through_the_environment(boot, monkeypatch):
    monkeypatch.setenv("HZ_ATLASSIAN__SITE_URL", "https://acme.atlassian.net/wiki")
    assert json.loads(invoke(boot(), "atlassian", "info", "--json"))["site_url"] == "https://acme.atlassian.net"


def test_the_credential_is_declared_with_its_purpose(boot):
    listed = json.loads(invoke(boot(), "system", "credential", "list", "--json"))
    entries = listed if isinstance(listed, list) else listed.get("credentials", [])
    ours = [c for c in entries if c["name"] == "atlassian-token"]
    assert len(ours) == 1
    assert "without scopes" in ours[0]["purpose"]


def test_the_kernel_provides_the_real_transport_unless_a_test_binds_a_fake(boot):
    from heinzel_atlassian import Transport

    assert type(boot().resolve(Transport)) is Transport


def test_the_readme_setup_commands_write_the_user_configuration(boot, monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    for key, value in (
        ("atlassian.site_url", "https://acme.atlassian.net"),
        ("atlassian.email", "ada@acme.example"),
        ("capabilities.tracker", "atlassian"),
        ("capabilities.documents", "atlassian"),
    ):
        invoke(boot(), "system", "config", "set", key, value)
    records, errors = discover()
    kernel = Kernel(Settings(), records, errors, Config.load(tmp_path))  # as `hz` loads the files
    about = json.loads(invoke(kernel, "atlassian", "info", "--json"))
    assert about["site_url"] == "https://acme.atlassian.net"
    report = json.loads(invoke(kernel, "system", "capability", "list", "--json"))
    bound = {c["name"]: c["bound"] for c in report["capabilities"]}
    assert (bound["tracker"], bound["documents"]) == ("atlassian", "atlassian")


def test_an_invalid_site_is_refused_by_config_set(boot, monkeypatch, tmp_path):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    result = runner.invoke(build(boot()), ["system", "config", "set", "atlassian.site_url", "https://evil.example"])
    assert result.exit_code != 0
    assert "Atlassian Cloud site" in result.output
