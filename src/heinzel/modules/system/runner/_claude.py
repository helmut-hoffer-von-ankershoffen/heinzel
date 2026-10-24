"""The Claude Code agent runner: `claude -p` headless, isolated from the user's Claude setup (ADR 0029).

Flags as in `claude --help` of Claude Code 2.1 (checked 2026-10-24):

- `-p` reads the prompt from standard input; `--output-format stream-json --verbose` prints one JSON event per line
  (the tool calls, then a `result` event with the text, usage and cost).
- `--safe-mode` disables CLAUDE.md, skills, plugins, hooks, MCP servers, custom commands and agents;
  `--restricted` ignores user, project and local settings files (so their hooks, `env` and `apiKeyHelper`), removes
  the tools that run commands or code unless `--tools` names them, and confines file tools to the working directory;
  `--disable-slash-commands` turns off skills and commands; `--strict-mcp-config` without `--mcp-config` loads no
  MCP server. Managed (policy) settings still apply, as an administrator intends. Not `--bare`: it allows only API
  keys and would break OAuth logins.
- `--tools` limits the built-in tools available and `--allowed-tools` pre-approves them; `--permission-mode dontAsk`
  denies everything else instead of prompting.
- `--append-system-prompt-file` reads the contract from a file in a private temporary directory, so it never shows
  in the process list; `--max-budget-usd` stops a run at its budget. There is no turn limit flag, so a `max_turns`
  hint can't be enforced here.
"""

import json
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from heinzel import AgentResult, Budget, HeinzelError, TokenUsage, ToolProfile
from heinzel.kernel import RunStatus, subprocess_environment
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import MAX_OUTPUT, Execute, Outcome, amount, run_bounded, whole_number

NAME = "claude-code"
HOSTS = ("api.anthropic.com",)
"""Where Claude Code sends prompts by default; a Bedrock or Vertex setup in managed settings differs."""
DOCS = "https://docs.claude.com/en/docs/claude-code"
BIND_COMMAND_RUNNER = "hz system config set capabilities.agent_runner command"
"""The fix when Claude Code is missing: the command runner is the other core runner."""
ISOLATION = (
    "--safe-mode",
    "--restricted",
    "--disable-slash-commands",
    "--strict-mcp-config",
    "--no-session-persistence",
    "--permission-mode",
    "dontAsk",
)
QUIET = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"}
"""No telemetry, error reporting or self-update from a run Heinzelmännchen starts."""


def working_dir(tools: ToolProfile) -> Path | None:
    """The profile's working directory, which must exist.

    Raises:
        HeinzelError: If it is given but is not a directory.
    """
    if tools.working_dir is not None and not tools.working_dir.is_dir():
        msg = f"The run's working directory {tools.working_dir} does not exist or is not a directory."
        raise HeinzelError(msg)
    return tools.working_dir


def resolve(executable: str, which: Callable[[str], str | None], what: str) -> str:
    """The absolute path of `executable`, looked up before the run changes directory.

    Raises:
        HeinzelError: If it isn't found.
    """
    found = which(executable)
    if found is None:
        msg = f"{what} ({executable}) is not on PATH."
        raise HeinzelError(msg)
    return str(Path(found).absolute())  # absolute, not resolved: a symlinked install stays as PATH names it


class ClaudeCodeRunner:
    """Runs one agent session with the `claude` CLI in print mode."""

    def __init__(
        self,
        settings: RunnerSettings,
        execute: Execute = run_bounded,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Create the runner; settings are injected, `execute` and `which` replace processes in tests."""
        self._settings = settings
        self._execute = execute
        self._which = which

    def command(self, *, contract_file: Path | None, tools: ToolProfile, budget: Budget) -> list[str]:
        """The command line of a run; the prompt goes to standard input and the contract to a file.

        Raises:
            HeinzelError: If the CLI is missing, or the profile asks for a tool not configured as read-only.
        """
        try:
            executable = resolve(self._settings.claude_executable, self._which, "Claude Code")
        except HeinzelError as exc:
            msg = f"{exc} Install it ({DOCS}), or bind the command runner: {BIND_COMMAND_RUNNER}"
            raise HeinzelError(msg) from None
        allowed = tools.tools or list(self._settings.read_only_tools)
        if unsafe := [tool for tool in allowed if tool not in self._settings.read_only_tools]:
            msg = (
                f"The tool profile asks for {', '.join(unsafe)}, which [system.runner] read_only_tools doesn't list as "
                f"read-only; runs never get tools that can change things."
            )
            raise HeinzelError(msg)
        names = ",".join(allowed)
        command = [executable, "-p", "--output-format", "stream-json", "--verbose", *ISOLATION]
        command += ["--tools", names, "--allowed-tools", names]
        if contract_file is not None:
            command += ["--append-system-prompt-file", str(contract_file)]
        if self._settings.model:
            command += ["--model", self._settings.model]
        if budget.max_usd is not None:
            command += ["--max-budget-usd", f"{budget.max_usd:g}"]
        return command

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        """Run `prompt` under `contract` with read-only `tools`; stop at `timeout` or `budget`.

        Raises:
            HeinzelError: If the CLI is missing, the profile isn't read-only, or the working directory is missing.
        """
        cwd = working_dir(tools)
        with tempfile.TemporaryDirectory(prefix="hz-run-") as directory:  # 0700: only this user reads the contract
            contract_file = None
            if contract:
                contract_file = Path(directory) / "contract.md"
                contract_file.write_text(contract, encoding="utf-8")
            command = self.command(contract_file=contract_file, tools=tools, budget=budget)
            started = time.monotonic()
            outcome = self._execute(
                command,
                input=prompt,
                cwd=cwd,
                env=subprocess_environment() | QUIET,
                timeout=timeout.total_seconds(),
            )
        return interpret(outcome, timeout, time.monotonic() - started)


def interpret(outcome: Outcome, timeout: timedelta, duration: float) -> AgentResult:
    """The result of a finished, failed or stopped `claude` process."""
    result = parse_events(outcome.stdout.splitlines(), duration)
    if outcome.timed_out:
        detail = f"stopped after {timeout}, the run's timeout"
        return result.model_copy(update={"status": "timeout", "draft": "", "detail": detail})
    cut = f"; output over {MAX_OUTPUT} bytes was cut" if outcome.truncated else ""
    if outcome.returncode != 0 and result.status in {"completed", "failed"}:
        why = result.detail or "ended"
        detail = f"exit code {outcome.returncode} ({why}): {outcome.stderr_tail()}{cut}"
        return result.model_copy(update={"status": "failed", "draft": "", "detail": detail})
    if result.status == "completed" and not result.draft.strip():
        return result.model_copy(update={"status": "failed", "detail": "Claude Code returned an empty result"})
    if cut and result.status != "completed":
        return result.model_copy(update={"detail": result.detail + cut})
    return result


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
        turns=whole_number(final.get("num_turns")),
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


def _usage(final: dict[str, Any]) -> TokenUsage:
    raw = final.get("usage")
    usage = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    return TokenUsage(
        input_tokens=whole_number(usage.get("input_tokens")),
        output_tokens=whole_number(usage.get("output_tokens")),
        cache_read_tokens=whole_number(usage.get("cache_read_input_tokens")),
        cache_write_tokens=whole_number(usage.get("cache_creation_input_tokens")),
        cost_usd=amount(final.get("total_cost_usd")),
    )
