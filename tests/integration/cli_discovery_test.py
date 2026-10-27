"""Agent discovery describes executable commands and keeps UI operations reachable through the CLI."""

import json
from typing import Annotated

import pytest
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from heinzel import Config, Module, Settings
from heinzel.heads._exposure import exposed, input_schema
from heinzel.heads.cli import build
from heinzel.heads.cli._chat import command_words
from heinzel.kernel import Kernel, discover

pytestmark = pytest.mark.integration
runner = CliRunner()


def flatten(node):
    yield node
    for child in node.get("commands", []):
        yield from flatten(child)


def command_catalog(app):
    result = runner.invoke(app, ["--command-tree"])
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    document = json.loads(result.stdout)
    assert document["schema_version"] == 1
    return {node["command"]: node for node in flatten(document["root"])}


class Entry(BaseModel):
    """A nested input with validation constraints."""

    title: str = Field(min_length=2, description="Entry title.")


def test_discovery_includes_hidden_commands_syntax_and_schemas_without_invoking(make_kernel):
    notes = Module("notes", "A test plugin.")

    @notes.operation(read_only=False)
    def save(entry: Entry, copies: Annotated[int, Field(ge=1, description="Number of copies.")] = 1) -> Entry:
        """Save an entry."""
        pytest.fail("Discovery must not invoke operations")

    hz = make_kernel(notes=notes)
    commands = command_catalog(build(hz))
    entry = commands["hz notes save"]
    assert "notes" not in runner.invoke(build(hz), ["--help"]).output.split("Commands")[-1]
    assert entry["operation"]["read_only"] is False
    assert entry["operation"]["audience"] == "expert"
    schema = entry["operation"]["input_schema"]
    assert schema["required"] == ["entry"]
    assert schema["$defs"]["Entry"]["properties"]["title"]["minLength"] == 2
    assert schema["properties"]["copies"]["minimum"] == 1
    assert entry["operation"]["output_schema"]["properties"]["title"]["minLength"] == 2
    parameters = {p["name"]: p for p in entry["parameters"]}
    assert parameters["entry"]["kind"] == "argument"
    assert parameters["entry"]["metavar"] == "JSON"
    assert parameters["copies"]["opts"] == ["--copies"]
    assert parameters["copies"]["help"] == "Number of copies."
    assert parameters["json_output"]["opts"] == ["--json"]
    assert hz.settings.expert is False


def test_every_ui_operation_has_a_discoverable_cli_command_and_matching_input_schema():
    hz = Kernel(Settings(), discover()[0], config=Config.empty())
    commands = command_catalog(build(hz))
    cli = {id(item.operation): item for item in exposed(hz, "cli")}
    for item in exposed(hz, "web"):
        assert id(item.operation) in cli, f"UI operation lacks CLI parity: {item.name}"
        name = " ".join(("hz", *command_words(cli[id(item.operation)].path)))
        assert commands[name]["operation"]["name"] == item.operation.name
        assert commands[name]["operation"]["input_schema"] == input_schema(item)
    for item in cli.values():
        name = " ".join(("hz", *command_words(item.path)))
        assert name in commands, f"CLI operation is not discoverable: {name}"
    assert {"hz setup", "hz join", "hz ui", "hz chat", "hz system serve api"} <= commands.keys()
    assert "hz clock" in commands
    assert commands["hz system expert"]["operation"]["name"] == "expert"


def test_cli_respects_head_restrictions(make_kernel):
    example = Module("example", "Head restrictions.")

    @example.operation(heads={"web"}, aliases=["old-browser"])
    def browser() -> str:
        """Browser only."""
        return "web"

    @example.operation(heads={"cli"})
    def terminal() -> str:
        """Terminal only."""
        return "cli"

    app = build(make_kernel(example=example))
    commands = command_catalog(app)
    assert "hz example terminal" in commands
    assert "hz example browser" not in commands
    assert "hz old-browser" not in commands
    assert runner.invoke(app, ["example", "browser"]).exit_code == 2
    assert runner.invoke(app, ["example", "terminal", "--json"]).stdout.strip() == '"cli"'


def test_discovery_describes_aliases_and_omits_disabled_modules(make_kernel):
    example = Module("example", "Alias and disabled-module discovery.")

    @example.operation(aliases=["old-command"])
    def current() -> str:
        """The current command."""
        return "current"

    commands = command_catalog(build(make_kernel(example=example)))
    assert commands["hz old-command"]["deprecated"] is True
    assert "hz example current" in commands["hz old-command"]["help"]
    disabled = make_kernel(example=example, settings_=Settings(disabled_modules=frozenset({"example"})))
    assert "hz example current" not in command_catalog(build(disabled))
    assert "hz old-command" not in command_catalog(build(disabled))


def test_discovery_entrypoint_does_not_start_update_check(make_kernel, monkeypatch, capsys):
    from heinzel.heads import cli

    def unexpected_check(kernel):
        pytest.fail("Discovery must not start an update check")

    monkeypatch.setattr(cli, "boot", make_kernel)
    monkeypatch.setattr(cli, "start_check", unexpected_check)
    monkeypatch.setattr("sys.argv", ["hz", "--command-tree"])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["schema_version"] == 1
    assert output.err == ""
