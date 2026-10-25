"""The runner module: the core's agent runners, Claude Code and a generic command (ADR 0029).

Both are adapters of the `agent-runner` capability; `[capabilities] agent_runner` picks one, and plugins add others.
"""

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from pydantic import Field

from heinzel import AgentResult, AgentRunner, Budget, Check, HeinzelError, Module, Status, ToolProfile
from heinzel.kernel import CapabilityView
from heinzel.modules.system.runner._access import ClaudeCodeAccess
from heinzel.modules.system.runner._claude import BIND_COMMAND_RUNNER, HOSTS, ClaudeCodeRunner, login_check
from heinzel.modules.system.runner._claude import DOCS as CLAUDE_CODE_DOCS
from heinzel.modules.system.runner._codex import HOSTS as CODEX_HOSTS
from heinzel.modules.system.runner._codex import NAME as CODEX
from heinzel.modules.system.runner._codex import CodexAccess, CodexRunner
from heinzel.modules.system.runner._command import SET_COMMAND, TRUST_COMMAND, CommandRunner
from heinzel.modules.system.runner._models import RunnerSettings

module = Module("runner", "Run bounded agent sessions with Claude Code or a configured command.")
module.settings(RunnerSettings)
module.provides("agent-runner", modes={"read"}, hosts=HOSTS, name="claude-code")(ClaudeCodeRunner)
module.provides("agent-runner", modes={"read"}, name="command")(CommandRunner)
# The same CLI as an AI access option, which names the runner above (ADR 0057).
module.provides("ai-access", modes={"read"}, hosts=HOSTS, name="claude-code")(ClaudeCodeAccess)
# The Codex CLI, a ChatGPT plan's coding agent, the same two ways (ADR 0057).
module.provides("agent-runner", modes={"read"}, hosts=CODEX_HOSTS, name=CODEX)(CodexRunner)
module.provides("ai-access", modes={"read"}, hosts=CODEX_HOSTS, name=CODEX)(CodexAccess)


ASK_TIMEOUT = timedelta(minutes=3)
ASK_PREAMBLE = (
    "You answer in the chat of Heinzelmännchen (hz), open-source helpers for product teams. Answer briefly, in "
    "Markdown. You can't run hz or change anything; when the user should run a command, name it. What follows is the "
    "conversation so far, or just a question; answer its last message.\n\n"
)
PROMPT_LIMIT = 6000
"""Characters a prompt may have: a question, or a short transcript the web app resends each turn (no session)."""


# A question spends money, within its cap: the CLI, MCP and the web head (behind its session token) get it, never the
# unauthenticated REST head (ADR 0039).
@module.operation(read_only=False, heads={"cli", "mcp", "web"}, requires=["agent-runner:read"])
def ask(
    runner: AgentRunner,
    settings: RunnerSettings,
    prompt: Annotated[
        str,
        Field(
            min_length=1,
            max_length=PROMPT_LIMIT,
            description="What to ask: a question, or the conversation so far ending with one; nothing is kept between "
            "calls.",
        ),
    ],
    budget_usd: Annotated[
        float | None,
        Field(
            gt=0, description="Most this answer may cost in US dollars, at most the setting system.runner.ask_max_usd."
        ),
    ] = None,
) -> AgentResult:
    """Ask the bound agent runner, read-only and within a cost cap; this may cost money.

    Raises:
        HeinzelError: If the prompt is blank, or the runner is misconfigured.
    """
    cap = min(budget_usd or settings.ask_max_usd, settings.ask_max_usd)
    if not prompt.strip():
        msg = "Ask something: the prompt is empty, e.g. hz system runner ask 'What does hz workflow graph show?'"
        raise HeinzelError(msg)
    # An empty working directory: the answer reads nothing on this machine.
    with tempfile.TemporaryDirectory(prefix="hz-ask-") as directory:
        return runner.run(
            ASK_PREAMBLE + prompt.strip(),
            contract="",  # a lean run's own short system prompt; the preamble is in the prompt
            tools=ToolProfile(working_dir=Path(directory), read_files=False),  # lean: a question reads no files
            timeout=ASK_TIMEOUT,
            budget=Budget(max_usd=cap),
        )


def claude_code_checks(settings: RunnerSettings) -> list[Check]:
    """Claude Code is on PATH, and logged in as far as it says without calling a model."""
    found = shutil.which(settings.claude_executable)
    if not found:
        return [
            Check(
                name="claude-code",
                status=Status.DEGRADED,
                detail=f"{settings.claude_executable} is not on PATH",
                fix=f"Install Claude Code ({CLAUDE_CODE_DOCS}), or bind the command runner: {BIND_COMMAND_RUNNER}",
            )
        ]
    installed = Check(
        name="claude-code", status=Status.UP, detail=f"{found}; read-only tools: {settings.read_only_tools}"
    )
    return [installed, login_check(found)]


def codex_checks(settings: RunnerSettings) -> list[Check]:
    """Codex is on PATH and signed in, as `codex login status` says; nothing spent."""
    found = CodexAccess(settings).status()
    status = Status.UP if found.installed and found.signed_in else Status.DEGRADED
    return [Check(name="codex", status=status, detail=found.detail, fix=found.fix)]


CLI_CHECKS = {"claude-code": claude_code_checks, CODEX: codex_checks}
"""The checks of each core runner that is an agent's CLI, by name."""


@module.health
def runner(capabilities: CapabilityView, settings: RunnerSettings) -> list[Check]:
    """The bound core runner: its CLI is there, and whether it enforces the read-only tool profile."""
    bound = capabilities.bound("agent-runner")
    if not any(use.capability == "agent-runner" for use in capabilities.matrix()):
        return [Check(name="runner", status=Status.UP, detail=f"no module requires agent-runner (bound: {bound})")]
    if bound in CLI_CHECKS:
        return CLI_CHECKS[bound](settings)
    if bound == "command":
        if not settings.command or shutil.which(settings.command[0]) is None:
            return [
                Check(
                    name="command",
                    status=Status.DEGRADED,
                    detail="no command is configured" if not settings.command else f"{settings.command[0]} not found",
                    fix=f"Set the agent CLI's command line, e.g.: {SET_COMMAND}",
                )
            ]
        if not settings.command_enforces_read_only:
            return [
                Check(
                    name="command",
                    status=Status.DEGRADED,
                    detail="it refuses to run: nothing records that the command restricts its agent to read-only tools",
                    fix=f"Configure the command's own read-only mode, then record that it does: {TRUST_COMMAND}",
                )
            ]
        return [Check(name="command", status=Status.UP, detail=" ".join(settings.command))]
    return [Check(name="runner", status=Status.UP, detail=f"agent-runner is bound to {bound or 'nothing'}")]


__all__ = ["ASK_TIMEOUT", "ClaudeCodeAccess", "ClaudeCodeRunner", "CommandRunner", "RunnerSettings", "module"]
