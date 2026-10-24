"""Agent runners through the CLI head: the command runner against a fake agent CLI, and doctor (ADR 0029).

No test runs a real agent: the fake CLI is a Python script in the test's directory.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from heinzel import AgentResult, AgentRunner, Budget, Config, Kernel, Module, ToolProfile
from heinzel.heads.cli import build

pytestmark = pytest.mark.integration

FAKE_AGENT = """
import json, pathlib, sys
prompt = pathlib.Path(sys.argv[1]).read_text()
contract = pathlib.Path(sys.argv[2]).read_text()
pathlib.Path(sys.argv[3]).write_text(json.dumps({
    "result": f"Draft for: {prompt} ({contract})",
    "usage": {"input_tokens": 42, "output_tokens": 7},
}))
"""


def digest() -> Module:
    module = Module("digest", "Drafts a digest with an agent.", requires=["agent-runner:read"])

    @module.operation
    def draft(runner: AgentRunner) -> AgentResult:
        """Draft the weekly digest."""
        return runner.run(
            "the week", contract="be brief", tools=ToolProfile(), timeout=timedelta(seconds=10), budget=Budget()
        )

    return module


def hz(kernel: Kernel, *args: str) -> tuple[int, object]:
    result = CliRunner().invoke(build(kernel), [*args, "--json"])
    return result.exit_code, json.loads(result.output) if result.output.startswith(("{", "[", '"')) else result.output


def command_config(tmp_path: Path, **runner: object) -> Config:
    script = tmp_path / "fake_agent.py"
    script.write_text(FAKE_AGENT, encoding="utf-8")
    command = [sys.executable, str(script), "{prompt_file}", "{contract_file}", "{output_file}"]
    settings = {"command": command, "command_output": "file", "command_format": "json"} | runner
    return Config(files=(), data={"capabilities": {"agent_runner": "command"}, "system": {"runner": settings}})


def test_the_command_runner_runs_a_configured_agent_cli(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True, digest=digest(), config=command_config(tmp_path))
    code, result = hz(kernel, "digest", "draft")
    assert code == 0, result
    assert isinstance(result, dict)
    assert result["runner"] == "command"
    assert result["status"] == "completed"
    assert result["draft"] == "Draft for: the week (be brief)"
    assert result["usage"]["input_tokens"] == 42
    assert result["warnings"] == ["the command is not known to restrict its agent to read-only tools"]


def test_the_matrix_shows_the_runner_and_its_hosts(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True, digest=digest())
    code, info = hz(kernel, "system", "info")
    assert code == 0
    assert isinstance(info, dict)
    assert info["sections"]["system.capability"] == {"digest → agent-runner:read": "claude-code → api.anthropic.com"}
    kernel = make_kernel(builtins=True, digest=digest(), config=command_config(tmp_path))
    assert hz(kernel, "system", "info")[1]["sections"]["system.capability"] == {  # type: ignore[index]
        "digest → agent-runner:read": "command → hosts set by its configuration"
    }


def runner_checks(kernel: Kernel) -> list[dict[str, str]]:
    code, report = hz(kernel, "system", "doctor")
    assert code == 0
    assert isinstance(report, dict)
    return report["checks"]["system.runner"]


def test_doctor_says_when_the_command_runner_cannot_enforce_read_only(make_kernel, tmp_path):
    [check] = runner_checks(make_kernel(builtins=True, digest=digest(), config=command_config(tmp_path)))
    assert check["status"] == "degraded"
    assert check["detail"] == "the configured command is not known to restrict its agent to read-only tools"
    trusted = command_config(tmp_path, command_enforces_read_only=True)
    [check] = runner_checks(make_kernel(builtins=True, digest=digest(), config=trusted))
    assert check["status"] == "up"


def test_doctor_reports_a_missing_command(make_kernel, tmp_path):
    config = Config(files=(), data={"capabilities": {"agent_runner": "command"}})
    [check] = runner_checks(make_kernel(builtins=True, digest=digest(), config=config))
    assert (check["status"], check["detail"]) == ("degraded", "no command is configured")
    missing = Config(
        files=(),
        data={"capabilities": {"agent_runner": "command"}, "system": {"runner": {"command": ["no-such-agent-cli"]}}},
    )
    [check] = runner_checks(make_kernel(builtins=True, digest=digest(), config=missing))
    assert check["detail"] == "no-such-agent-cli not found"


def test_doctor_checks_the_claude_cli_only_when_someone_needs_a_runner(make_kernel, tmp_path):
    [check] = runner_checks(make_kernel(builtins=True))
    assert (check["status"], check["detail"]) == ("up", "no module requires agent-runner (bound: claude-code)")
    config = Config(files=(), data={"system": {"runner": {"claude_executable": str(tmp_path / "no-claude")}}})
    [check] = runner_checks(make_kernel(builtins=True, digest=digest(), config=config))
    assert check["status"] == "degraded"
    assert check["fix"] == 'Install Claude Code, or bind another runner: [capabilities] agent_runner = "command"'
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    config = Config(files=(), data={"system": {"runner": {"claude_executable": str(fake)}}})
    [check] = runner_checks(make_kernel(builtins=True, digest=digest(), config=config))
    assert check["status"] == "up"


def test_doctor_is_quiet_about_runners_from_plugins(make_kernel):
    config = Config(files=(), data={"capabilities": {"agent_runner": "acme"}})
    [check] = runner_checks(make_kernel(builtins=True, digest=digest(), config=config))
    assert (check["status"], check["detail"]) == ("up", "agent-runner is bound to acme")
