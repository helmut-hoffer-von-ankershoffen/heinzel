"""The runner module: the core's agent runners, Claude Code and a generic command (ADR 0029).

Both are adapters of the `agent-runner` capability; `[capabilities] agent_runner` picks one, and plugins add others.
"""

import shutil

from heinzel import Check, Module, Status
from heinzel.kernel import Capabilities
from heinzel.modules.system.runner._claude import HOSTS, ClaudeCodeRunner
from heinzel.modules.system.runner._command import CommandRunner
from heinzel.modules.system.runner._models import RunnerSettings

module = Module("runner", "Run bounded agent sessions with Claude Code or a configured command.")
module.settings(RunnerSettings)
module.provides("agent-runner", modes={"read"}, hosts=HOSTS, name="claude-code")(ClaudeCodeRunner)
module.provides("agent-runner", modes={"read"}, name="command")(CommandRunner)


@module.health
def runner(capabilities: Capabilities, settings: RunnerSettings) -> list[Check]:
    """The bound core runner: its CLI is there, and whether it enforces the read-only tool profile."""
    bound = capabilities.settings.bound("agent-runner")
    if not any(use.capability == "agent-runner" for use in capabilities.matrix()):
        return [Check(name="runner", status=Status.UP, detail=f"no module requires agent-runner (bound: {bound})")]
    if bound == "claude-code":
        found = shutil.which(settings.claude_executable)
        return [
            Check(name="claude-code", status=Status.UP, detail=f"{found}; read-only tools: {settings.read_only_tools}")
            if found
            else Check(
                name="claude-code",
                status=Status.DEGRADED,
                detail=f"{settings.claude_executable} is not on PATH",
                fix='Install Claude Code, or bind another runner: [capabilities] agent_runner = "command"',
            )
        ]
    if bound == "command":
        if not settings.command or shutil.which(settings.command[0]) is None:
            return [
                Check(
                    name="command",
                    status=Status.DEGRADED,
                    detail="no command is configured" if not settings.command else f"{settings.command[0]} not found",
                    fix='Set [system.runner] command = ["mytool", "run", "--prompt", "{prompt_file}"]',
                )
            ]
        if not settings.command_enforces_read_only:
            return [
                Check(
                    name="command",
                    status=Status.DEGRADED,
                    detail="the configured command is not known to restrict its agent to read-only tools",
                    fix="Configure the command's own read-only mode, then set "
                    "[system.runner] command_enforces_read_only = true",
                )
            ]
        return [Check(name="command", status=Status.UP, detail=" ".join(settings.command))]
    return [Check(name="runner", status=Status.UP, detail=f"agent-runner is bound to {bound or 'nothing'}")]


__all__ = ["ClaudeCodeRunner", "CommandRunner", "RunnerSettings", "module"]
