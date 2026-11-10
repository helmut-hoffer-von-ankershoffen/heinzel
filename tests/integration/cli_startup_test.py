"""Direct UI startup avoids schemas belonging to other CLI commands."""

import json

import pytest
import typer
from typer.testing import CliRunner

from heinzel.heads import cli

pytestmark = pytest.mark.integration


def test_ui_subtree_retains_every_command_option_and_help(make_kernel):
    kernel = make_kernel(builtins=True)
    runner = CliRunner()
    full = runner.invoke(cli.build(kernel), ["--command-tree"])
    direct = runner.invoke(cli.build(kernel, ui_only=True), ["--command-tree"])
    assert full.exit_code == 0, full.output
    assert direct.exit_code == 0, direct.output
    full_root = json.loads(full.stdout)["root"]
    direct_root = json.loads(direct.stdout)["root"]
    assert next(node for node in direct_root["commands"] if node["command"] == "hz ui") == next(
        node for node in full_root["commands"] if node["command"] == "hz ui"
    )
    assert direct_root["parameters"] == full_root["parameters"]


@pytest.mark.parametrize(
    "arguments", [["ui", "serve", "--devices"], ["ui", "serve", "--unknown"], ["ui", "serve", "--port", "bad"]]
)
def test_ui_parsing_and_read_only_results_match(arguments, make_kernel):
    kernel = make_kernel(builtins=True)
    runner = CliRunner()
    full = runner.invoke(cli.build(kernel), arguments)
    direct = runner.invoke(cli.build(kernel, ui_only=True), arguments)
    assert (direct.exit_code, direct.output) == (full.exit_code, full.output)


def test_direct_ui_does_not_construct_unrelated_operation_schemas(monkeypatch, make_kernel):
    kernel = make_kernel(builtins=True)
    original = cli.command
    seen = []

    def tracked(*args, **kwargs):
        seen.append(kwargs["module"])
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "command", tracked)
    typer.main.get_command(cli.build(kernel, ui_only=True))
    assert seen
    assert all(name == "ui" or name.startswith("ui.") for name in seen)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["ui", "serve", "--port", "0", "--no-browser"], True),
        (["ui", "status"], True),
        (["ui", "--help"], False),
        (["ui", "serve", "--help"], False),
        (["--help", "ui"], False),
        (["--command-tree"], False),
        (["--version"], False),
        (["system", "doctor"], False),
    ],
)
def test_entrypoint_selects_only_direct_ui(arguments, expected, monkeypatch, make_kernel):
    kernel = make_kernel(builtins=True)
    seen = []
    monkeypatch.setattr(cli, "boot", lambda: kernel)
    monkeypatch.setattr(cli, "start_check", lambda _: None)
    monkeypatch.setattr(cli.sys, "argv", ["hz", *arguments])

    def build(*args, **kwargs):
        seen.append(dict(kwargs))
        return lambda: None

    monkeypatch.setattr(cli, "build", build)
    cli.main()
    assert seen == [{"ui_only": True} if expected else {}]


def test_disabled_ui_keeps_plugin_aliases_on_complete_tree(monkeypatch, make_kernel):
    from heinzel import Module, Settings

    plugin = Module("example", "Example.")

    @plugin.operation(aliases=["ui legacy"])
    def legacy() -> str:
        """Run a retained alias."""
        return "legacy result"

    kernel = make_kernel(builtins=True, settings_=Settings(disabled_modules=frozenset({"ui"})), example=plugin)
    original = cli.build
    seen = []

    def build(kernel, **kwargs):
        seen.append(dict(kwargs))
        app = original(kernel, **kwargs)
        result = CliRunner().invoke(app, ["ui", "legacy", "--json"])
        assert result.exit_code == 0, result.output
        assert '"legacy result"' in result.output
        return lambda: None

    monkeypatch.setattr(cli, "boot", lambda: kernel)
    monkeypatch.setattr(cli, "start_check", lambda _: None)
    monkeypatch.setattr(cli.sys, "argv", ["hz", "ui", "legacy", "--json"])
    monkeypatch.setattr(cli, "build", build)
    cli.main()
    assert seen == [{}]
