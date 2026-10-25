"""Hints about capabilities end with a command that works (CODING_STANDARDS.md, Errors).

Every fix that doctor or an error gives for an unmet capability or a misconfigured runner ends with an `hz ...`
command line. The test parses each against the real command tree, so a renamed command, option or setting fails it,
and runs the configuration commands to show they leave the capability served.
"""

import json
import re
import shlex
import sys
from pathlib import Path

import click
import pytest
import typer
from typer.testing import CliRunner

from heinzel import Config, Documents, Kernel, Module
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

COMMAND = re.compile(r"(?:^|\s)(hz [^\n]+)$")


class Notes:
    """A documents adapter for tests."""

    def read(self, ref: str) -> object:
        return ref


def notes() -> Module:
    module = Module("notes", "Test-only documents adapter.")
    module.provides("documents", modes={"read"})(Notes)
    return module


def digest() -> Module:
    module = Module("digest", "Reads documents.", requires=["documents:read"])

    @module.operation
    def show(documents: Documents) -> str:
        """Show the weekly document."""
        return str(documents.read("weekly"))

    return module


def drafter() -> Module:
    return Module("drafter", "Needs an agent runner.", requires=["agent-runner:read", "tracker:read"])


def command_of(hint: str) -> list[str]:
    """The `hz ...` command line a hint ends with, as arguments without `hz`."""
    match = COMMAND.search(hint.strip())
    assert match, f"hint does not end with an `hz ...` command: {hint!r}"
    return shlex.split(match.group(1))[1:]


def assert_parses(kernel: Kernel, args: list[str]) -> None:
    """`hz <args>` names a command of the tree, and its arguments and options parse."""
    command: click.Command = typer.main.get_command(build(kernel))
    rest = list(args)
    while isinstance(command, click.Group):
        sub = command.get_command(click.Context(command), rest[0])
        assert sub is not None, f"`hz {' '.join(args)}`: no command {rest[0]!r}"
        command, rest = sub, rest[1:]
    command.make_context(command.name or "", rest)


def hints(kernel: Kernel) -> list[str]:
    """Every fix doctor gives for capabilities and runners."""
    result = CliRunner().invoke(build(kernel), ["system", "doctor", "--json"])
    checks = json.loads(result.output)["checks"]
    return [c["fix"] for section in ("system.capability", "system.runner") for c in checks[section] if c["fix"]]


@pytest.fixture
def loaded(make_kernel, tmp_path):
    """A kernel that reads the user configuration file, as `hz` does, so commands that change it take effect."""

    def make(**modules: Module) -> Kernel:
        return make_kernel(builtins=True, config=Config.load(tmp_path), **modules)

    return make


def test_capability_and_runner_fixes_end_with_commands_that_parse(make_kernel, tmp_path):
    bound = {"documents": "nowhere", "agent_runner": "command"}
    scenarios = [
        Config(files=(), data={"system": {"runner": {"claude_executable": str(tmp_path / "no-claude")}}}),
        Config(files=(), data={"capabilities": bound}),
        Config(
            files=(),
            data={"capabilities": {"agent_runner": "command"}, "system": {"runner": {"command": [sys.executable]}}},
        ),
    ]
    seen: list[str] = []
    for config in scenarios:
        kernel = make_kernel(builtins=True, notes=notes(), digest=digest(), drafter=drafter(), config=config)
        for hint in hints(kernel):
            assert_parses(kernel, command_of(hint))
            seen.append(hint)
    assert len(seen) >= 6, seen  # claude-code, command, its read-only flag, tracker, documents bound and unbound


def test_errors_for_an_unmet_capability_end_with_a_command_that_parses(make_kernel):
    for documents in ("none", "nowhere"):  # `none`: the sole adapter would serve it automatically (ADR 0055)
        config = Config(files=(), data={"capabilities": {"documents": documents}})
        kernel = make_kernel(builtins=True, notes=notes(), digest=digest(), config=config)
        result = CliRunner().invoke(build(kernel), ["digest", "show"])
        assert result.exit_code != 0
        assert_parses(kernel, command_of(" ".join(result.output.split())))


def test_running_the_binding_fix_serves_the_capability(loaded):
    off = ["system", "config", "set", "capabilities.documents", "none"]  # the sole adapter would serve it (ADR 0055)
    assert CliRunner().invoke(build(loaded(notes=notes(), digest=digest())), off).exit_code == 0
    kernel = loaded(notes=notes(), digest=digest())
    [hint] = [h for h in hints(kernel) if "documents" in h]
    result = CliRunner().invoke(build(kernel), command_of(hint))
    assert result.exit_code == 0, result.output
    after = CliRunner().invoke(build(loaded(notes=notes(), digest=digest())), ["digest", "show", "--json"])
    assert (after.exit_code, json.loads(after.output)) == (0, "weekly")


def test_running_the_runner_fixes_makes_the_command_runner_ready(loaded, tmp_path: Path):
    fixes = [
        "hz system config set capabilities.agent_runner command",
        f"hz system config set system.runner.command '[{json.dumps(sys.executable)}]'",
    ]
    for fix in fixes:
        result = CliRunner().invoke(build(loaded(drafter=drafter())), command_of(fix))
        assert result.exit_code == 0, result.output
    [hint] = [h for h in hints(loaded(drafter=drafter())) if "read-only" in h]
    assert CliRunner().invoke(build(loaded(drafter=drafter())), command_of(hint)).exit_code == 0
    runner = [h for h in hints(loaded(drafter=drafter())) if "runner" in h or "read-only" in h]
    assert runner == [], runner
