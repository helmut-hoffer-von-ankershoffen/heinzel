"""A renamed module (ADR 0051): old command paths, tool names, settings and job ids work for one minor release."""

import json
from typing import Annotated

import anyio
import pytest
from mcp import Client
from pydantic import Field
from typer.testing import CliRunner

from heinzel import Config, Module, ModuleSettings
from heinzel.heads import mcp
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

runner = CliRunner()


class NotesSettings(ModuleSettings):
    greeting: str = Field(default="Hello", description="How to greet.")


def notes() -> Module:
    module = Module("notes", "Notes, by a newer name.", renamed_from="jottings")
    module.settings(NotesSettings)

    @module.operation(aliases=["jottings run"])
    def wake(
        settings: NotesSettings, name: Annotated[str, Field(description="Who to greet.")] = "Mira"
    ) -> dict[str, str]:
        """Greet someone with the configured greeting."""
        return {"text": f"{settings.greeting}, {name}!"}

    @module.operation(aliases=["jottings list", "notes wake"])
    def list_() -> list[str]:
        """List the notes."""
        return ["one"]

    @module.job(schedule="@daily")
    def tidy() -> None:
        """Tidy the notes."""

    return module


@pytest.fixture
def kernel(make_kernel):
    return make_kernel(builtins=True, config=Config(files=(), data={"jottings": {"greeting": "Moin"}}), notes=notes())


def test_an_old_command_runs_the_operation_and_names_the_new_one_on_stderr(kernel):
    result = runner.invoke(build(kernel), ["jottings", "run", "--name", "Kai", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"text": "Moin, Kai!"}  # the old settings table still counts
    assert "hz jottings run is deprecated and goes in the next minor release; use hz notes wake." in result.stderr
    assert result.stderr.count("deprecated") == 1


def test_old_commands_are_hidden_from_help_and_the_reference(kernel):
    assert "jottings" not in runner.invoke(build(kernel), ["--help"]).stdout
    assert "jottings" not in runner.invoke(build(kernel, plain=True), ["--help"]).stdout
    assert runner.invoke(build(kernel, plain=True), ["jottings", "run"]).exit_code != 0  # not in the reference


def test_an_alias_never_shadows_a_command_that_exists(kernel):
    result = runner.invoke(build(kernel), ["notes", "wake", "--json"])
    assert json.loads(result.stdout) == {"text": "Moin, Mira!"}
    assert "deprecated" not in result.stderr


def test_mcp_keeps_the_old_tool_names_marked_deprecated(kernel):
    async def listed() -> dict[str, str]:
        async with Client(mcp.build(kernel)) as client:
            return {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}

    tools = anyio.run(listed)
    assert tools["jottings_run"].startswith("Deprecated: use `notes_wake`")
    assert tools["notes_wake"] == "Greet someone with the configured greeting."
    assert "jottings_list" in tools


def test_mcp_runs_an_old_tool_name(kernel):
    async def call() -> str:
        async with Client(mcp.build(kernel)) as client:
            result = await client.call_tool("jottings_run", {"name": "Kai"})
            return json.dumps(result.structured_content)

    assert "Moin, Kai!" in anyio.run(call)


def test_a_chat_gets_only_the_current_tool_names(kernel):
    async def listed() -> set[str]:
        async with Client(mcp.build(kernel, chat="0123456789abcdef", chat_tools="all")) as client:
            return {tool.name for tool in (await client.list_tools()).tools}

    names = anyio.run(listed)
    assert "notes_wake" in names
    assert "jottings_run" not in names


def test_the_daemon_runs_a_job_asked_for_by_its_old_id(kernel):
    result = runner.invoke(build(kernel), ["system", "daemon", "tick", "--job", "jottings:tidy", "--json"])
    assert result.exit_code == 0, result.output
    assert [run["job"] for run in json.loads(result.stdout)["runs"]] == ["notes:tidy"]


def test_the_kernel_knows_what_a_module_was_called(kernel):
    assert kernel.renamed("jottings") == "notes"
    assert kernel.renamed("notes") is None


def test_getting_or_setting_an_old_key_names_the_new_one(kernel):
    for args in (["get", "jottings.greeting"], ["set", "jottings.greeting", "Hi"]):
        result = runner.invoke(build(kernel), ["system", "config", *args])
        assert result.exit_code == 1
        assert "[jottings] is now [notes]: use notes.greeting" in result.output


def test_unsetting_an_old_key_removes_it_from_the_old_table(kernel, tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CONFIG_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text('[jottings]\ngreeting = "Moin"\n', encoding="utf-8")
    result = runner.invoke(build(kernel), ["system", "config", "unset", "jottings.greeting"])
    assert result.exit_code == 0, result.output
    assert "greeting" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


@pytest.mark.parametrize("job", ["nothere:tidy", "tidy"])
def test_an_unknown_job_is_still_no_job(kernel, job):
    result = runner.invoke(build(kernel), ["system", "daemon", "tick", "--job", job])
    assert result.exit_code == 1
    assert f"No job {job!r}" in result.output
