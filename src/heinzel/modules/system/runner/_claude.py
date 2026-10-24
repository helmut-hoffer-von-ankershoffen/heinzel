"""The Claude Code agent runner: `claude -p` headless, with a read-only tool profile (ADR 0029).

Flags as in `claude --help` (checked 2026-10-24): `-p` reads the prompt from standard input, `--output-format
stream-json --verbose` prints one JSON event per line (the tool calls, then a `result` event with the text, usage and
cost), `--tools` limits the built-in tools available and `--allowed-tools` pre-approves them, `--permission-mode
dontAsk` denies everything else instead of prompting, `--strict-mcp-config` without `--mcp-config` loads no MCP
server, and `--max-budget-usd` stops a run at its budget. There is no turn limit flag, so a `max_turns` hint can't be
enforced here.
"""

import json
import shutil
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any, cast

from heinzel import AgentResult, Budget, HeinzelError, TokenUsage, ToolProfile
from heinzel.kernel import RunStatus, subprocess_environment
from heinzel.modules.system.runner._models import RunnerSettings

NAME = "claude-code"
HOSTS = ("api.anthropic.com",)
"""Where Claude Code sends prompts by default; a Bedrock or Vertex setup in Claude Code's own settings differs."""

Run = Callable[..., "subprocess.CompletedProcess[str]"]


class ClaudeCodeRunner:
    """Runs one agent session with the `claude` CLI in print mode."""

    def __init__(
        self,
        settings: RunnerSettings,
        run: Run = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Create the runner; settings are injected, `run` and `which` replace subprocesses in tests."""
        self._settings = settings
        self._run = run
        self._which = which

    def command(self, *, contract: str, tools: ToolProfile, budget: Budget) -> list[str]:
        """The command line of a run; the prompt goes to standard input, never into the arguments.

        Raises:
            HeinzelError: If the CLI is missing, or the profile asks for a tool not configured as read-only.
        """
        executable = self._which(self._settings.claude_executable)
        if executable is None:
            msg = (
                f"Claude Code ({self._settings.claude_executable}) is not on PATH; install it "
                f"(https://docs.claude.com/en/docs/claude-code) or bind another runner: "
                f'[capabilities] agent_runner = "command".'
            )
            raise HeinzelError(msg)
        allowed = tools.tools or list(self._settings.read_only_tools)
        if unsafe := [tool for tool in allowed if tool not in self._settings.read_only_tools]:
            msg = (
                f"The tool profile asks for {', '.join(unsafe)}, which [system.runner] read_only_tools doesn't list as "
                f"read-only; runs never get tools that can change things."
            )
            raise HeinzelError(msg)
        names = ",".join(allowed)
        command = [
            executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--tools",
            names,
            "--allowed-tools",
            names,
            "--permission-mode",
            "dontAsk",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]
        if contract:
            command += ["--append-system-prompt", contract]
        if self._settings.model:
            command += ["--model", self._settings.model]
        if budget.max_usd is not None:
            command += ["--max-budget-usd", f"{budget.max_usd:g}"]
        return command

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        """Run `prompt` under `contract` with read-only `tools`; stop at `timeout` or `budget`.

        Raises:
            HeinzelError: If the CLI is missing or the profile isn't read-only.
        """
        command = self.command(contract=contract, tools=tools, budget=budget)
        started = time.monotonic()
        try:
            completed = self._run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout.total_seconds(),
                cwd=tools.working_dir,
                env=subprocess_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            partial = _text(exc.stdout)
            result = parse_events(partial.splitlines(), time.monotonic() - started)
            return result.model_copy(
                update={"status": "timeout", "draft": "", "detail": f"stopped after {timeout}, the run's timeout"}
            )
        result = parse_events(completed.stdout.splitlines(), time.monotonic() - started)
        if completed.returncode != 0 and result.status == "completed":
            stderr = completed.stderr.strip()[-500:]
            return result.model_copy(
                update={"status": "failed", "draft": "", "detail": f"exit code {completed.returncode}: {stderr}"}
            )
        return result


def _text(output: object) -> str:
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output if isinstance(output, str) else ""


def parse_events(lines: Iterable[str], duration: float) -> AgentResult:
    """An `AgentResult` from Claude Code's stream-json events; fields it doesn't report stay none."""
    model: str | None = None
    calls: Counter[str] = Counter()
    final: dict[str, Any] | None = None
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event = cast("dict[str, Any]", event)
        kind = event.get("type")
        if kind == "system" and isinstance(event.get("model"), str):
            model = event["model"]
        elif kind == "assistant":
            calls.update(_tool_uses(event))
        elif kind == "result":
            final = event
    if final is None:
        return AgentResult(
            runner=NAME,
            model=model,
            status="failed",
            tool_calls=dict(calls),
            duration_seconds=duration,
            detail="Claude Code printed no result",
        )
    subtype = str(final.get("subtype", ""))
    status: RunStatus = "completed"
    if "budget" in subtype:
        status = "budget"
    elif final.get("is_error") or subtype != "success":
        status = "failed"
    return AgentResult(
        runner=NAME,
        model=model,
        status=status,
        draft=str(final.get("result", "")) if status == "completed" else "",
        tool_calls=dict(calls),
        turns=_int(final.get("num_turns")),
        usage=_usage(final),
        duration_seconds=duration,
        detail="" if status == "completed" else f"Claude Code ended with {subtype or 'an error'}",
    )


def _tool_uses(event: dict[str, Any]) -> list[str]:
    message = event.get("message")
    content = cast("dict[str, Any]", message).get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    blocks = [cast("dict[str, Any]", block) for block in cast("list[object]", content) if isinstance(block, dict)]
    return [str(block["name"]) for block in blocks if block.get("type") == "tool_use" and "name" in block]


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _usage(final: dict[str, Any]) -> TokenUsage:
    raw = final.get("usage")
    usage = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    cost = final.get("total_cost_usd")
    return TokenUsage(
        input_tokens=_int(usage.get("input_tokens")),
        output_tokens=_int(usage.get("output_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
        cache_write_tokens=_int(usage.get("cache_creation_input_tokens")),
        cost_usd=float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
    )
