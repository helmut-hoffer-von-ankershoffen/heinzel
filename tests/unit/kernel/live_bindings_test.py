"""A long-running head takes up the agent runner and AI access picked in Settings > AI before its next call.

`hz ui serve`, the daemon and the MCP server read the bindings at boot; a runner picked afterwards must apply without
a restart (`Kernel.refresh_live_bindings`). Real configuration files in an isolated folder, no live hz.
"""

import logging
import os
from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import AgentResult, AgentRunner, Budget, Config, Kernel, Module, OperationLet, ToolProfile
from heinzel.kernel._config import ConfigFile

pytestmark = pytest.mark.unit


class First:
    def run(self, prompt: str, **_: object) -> AgentResult:
        return AgentResult(runner="first", status="completed", draft=prompt, duration_seconds=0)


class Second:
    def run(self, prompt: str, **_: object) -> AgentResult:
        return AgentResult(runner="second", status="completed", draft=prompt, duration_seconds=0)


def runners() -> Module:
    module = Module("runners", "Two agent runners for tests.")
    module.provides("agent-runner", modes={"read"}, name="first")(First)
    module.provides("agent-runner", modes={"read"}, name="second")(Second)
    return module


def chat() -> Module:
    module = Module("talk", "Asks the bound runner.", requires=["agent-runner:read"])

    @module.operation
    def ask(runner: AgentRunner) -> str:
        """Ask the runner."""
        result = runner.run(
            "hi", contract="test", tools=ToolProfile(), timeout=timedelta(seconds=1), budget=Budget(max_usd=0.01)
        )
        return result.runner

    return module


def write(path: Path, runner: str) -> None:
    path.write_text(f'[capabilities]\nagent_runner = "{runner}"\n')
    # A second write within the clock's resolution still differs in size or time: make the time differ for sure.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def ask(kernel: Kernel) -> str:
    let = next(let for record, let in kernel.lets(OperationLet) if record.qualname == "talk" and let.name == "ask")
    return kernel.invoke(let)


@pytest.fixture
def setup(make_kernel, tmp_path) -> tuple[Kernel, Path]:
    path = tmp_path / "config.toml"
    write(path, "first")
    config = Config._read((ConfigFile("user", path),))
    return make_kernel(runners=runners(), talk=chat(), config=config), path


def test_a_runner_picked_after_start_serves_the_next_call_without_a_restart(setup, caplog):
    kernel, path = setup
    assert ask(kernel) == "first"
    write(path, "second")
    with caplog.at_level(logging.INFO, logger="heinzel.kernel._kernel"):
        assert ask(kernel) == "second"
    assert kernel.resolve(type(kernel._capabilities.view)).bound("agent-runner") == "second"
    events = [r for r in caplog.records if getattr(r, "event", "") == "kernel.bindings.refresh"]
    assert [(r.outcome, r.changed, r.agent_runner) for r in events] == [("ok", "agent_runner", "second")]


def test_nothing_is_reread_when_the_files_did_not_change(setup):
    kernel, _ = setup
    assert kernel.refresh_live_bindings() is False


def test_an_unreadable_file_keeps_the_runner_and_logs_why(setup, caplog):
    kernel, path = setup
    path.write_text("[capabilities\n")
    with caplog.at_level(logging.WARNING, logger="heinzel.kernel._kernel"):
        assert kernel.refresh_live_bindings() is False
    assert ask(kernel) == "first"
    events = [r for r in caplog.records if getattr(r, "event", "") == "kernel.bindings.refresh"]
    assert [(r.outcome, r.error_type) for r in events] == [("error", "HeinzelError")]


def test_only_the_runner_and_ai_access_bindings_change_live(make_kernel, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[capabilities]\nagent_runner = "first"\ndocuments = "a"\n')
    kernel = make_kernel(runners=runners(), talk=chat(), config=Config._read((ConfigFile("user", path),)))
    path.write_text('[capabilities]\nagent_runner = "second"\ndocuments = "b"\n')
    assert kernel.refresh_live_bindings() is True
    assert (kernel.capability_settings.bound("agent-runner"), kernel.capability_settings.bound("documents")) == (
        "second",
        "a",
    )
