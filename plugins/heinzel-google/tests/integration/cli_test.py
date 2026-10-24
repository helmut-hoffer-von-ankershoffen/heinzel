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
    assert [r.qualname for r in kernel.modules if r.origin.distribution == "heinzel-google"] == ["google"]
    assert "google" not in kernel.errors


def test_info(boot, gws_path, monkeypatch):
    monkeypatch.setenv("HZ_GOOGLE__GWS_PATH", gws_path)
    about = json.loads(invoke(boot(), "google", "info", "--json"))
    assert (about["plugin"], about["modules"], about["gws"]) == ("heinzel-google", ["google"], gws_path)


def test_contributes_to_system_info(boot):
    sections = json.loads(invoke(boot(), "system", "info", "--json"))["sections"]
    assert sections["google"]["plugin"] == "heinzel-google"


def test_plugin_can_be_disabled(boot):
    kernel = boot(disabled_modules=frozenset({"google"}))
    assert not [r for r in kernel.modules if r.qualname.startswith("google")]


def test_the_kernel_provides_the_real_runner_unless_a_test_binds_a_fake(boot):
    from heinzel_google import Gws

    assert type(boot().resolve(Gws)) is Gws


def test_setup_through_the_cli(google_kernel, fake_gws, tmp_path, monkeypatch):
    from heinzel import Prompter
    from heinzel.kernel import ScriptedPrompter

    home = tmp_path / "config"
    monkeypatch.setenv("HZ_CONFIG_HOME", str(home))
    kernel = google_kernel()
    prompter = ScriptedPrompter(["y", "", "", ""])  # change it (gws_path is set); documents and calendar; no mail
    kernel.bind(Prompter, prompter)
    result = runner.invoke(build(kernel), ["setup", "google"])
    assert result.exit_code == 0, result.output
    written = "".join(p.read_text(encoding="utf-8") for p in home.rglob("*.toml"))
    assert 'documents = "google"' in written, prompter.transcript
    assert 'calendar = "google"' in written
