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
- A run with MCP servers in its tool profile (a chat with Heinzelmännchen's operations as tools, ADR 0045) gets them
  in `--mcp-config`, a file in the private temporary directory, and `--allowed-tools mcp__<name>` for each. Claude
  Code 2.1.310 loads no `--mcp-config` server under `--safe-mode` (checked 2026-10-24: its init event lists none), so
  such a run drops `--safe-mode` and passes `--setting-sources ""` instead: no user, project or local settings, so no
  plugins, hooks or skills from them; `--restricted`, `--strict-mcp-config` and the rest stay.
- `--include-partial-messages` adds `stream_event` lines with the answer's text as it is written; they go to the
  injected `Progress`'s emitter as `text` events, and each tool call as a `tool` event, so a head can stream them.
- `--append-system-prompt-file` reads the contract from a file in a private temporary directory, so it never shows
  in the process list; `--max-budget-usd` stops a run at its budget. There is no turn limit flag, so a `max_turns`
  hint can't be enforced here.
"""

import json
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from heinzel import AgentResult, Budget, Check, Emit, HeinzelError, Progress, Status, TokenUsage, ToolProfile
from heinzel.kernel import Billing, Effort, RunStatus, clean_text, subprocess_environment
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import MAX_OUTPUT, Execute, Outcome, amount, run_bounded, whole_number

NAME = "claude-code"
HOSTS = ("api.anthropic.com",)
"""Where Claude Code sends prompts by default; a Bedrock or Vertex setup in managed settings differs."""
DOCS = "https://docs.claude.com/en/docs/claude-code"
BIND_COMMAND_RUNNER = "hz system config set capabilities.agent_runner command"
"""The fix when Claude Code is missing: the command runner is the other core runner."""
SAFE_MODE = ("--safe-mode",)
NO_SETTINGS = ("--setting-sources", "")
"""Instead of `--safe-mode` when the run has MCP servers, which `--safe-mode` would not load."""
ISOLATION = (
    "--restricted",
    "--disable-slash-commands",
    "--strict-mcp-config",
    "--no-session-persistence",
    "--permission-mode",
    "dontAsk",
)
SILENT = Progress()
"""Progress for a runner built outside a kernel (tests); the kernel injects the head's own."""
QUIET = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"}
"""No telemetry, error reporting or self-update from a run Heinzelmännchen starts."""
LEAN_PROMPT = "You are a helpful assistant. Answer briefly."
"""The system prompt of a lean run that brings none of its own."""


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
        progress: Progress = SILENT,
        execute: Execute = run_bounded,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Create the runner; settings and progress are injected, `execute` and `which` replace processes in tests."""
        self._settings = settings
        self._progress = progress
        self._execute = execute
        self._which = which
        self._login: Billing | bool | None = False
        """How the login is billed, asked once per runner when a run used no API key; False: not asked yet."""

    def command(  # noqa: PLR0913 - everything a run's command line holds
        self,
        *,
        contract_file: Path | None,
        tools: ToolProfile,
        budget: Budget,
        mcp_file: Path | None = None,
        model: str | None = None,
        effort: Effort | None = None,
    ) -> list[str]:
        """The command line of a run; the prompt goes to standard input and the contract to a file.

        A run without file tools (a chat, a question, a test) is lean: the contract replaces Claude Code's own system
        prompt, which describes coding tools the run doesn't have, so a short message sends a few hundred tokens of
        instructions instead of thousands.

        Raises:
            HeinzelError: If the CLI is missing, or the profile asks for a tool not configured as read-only.
        """
        try:
            executable = resolve(self._settings.claude_executable, self._which, "Claude Code")
        except HeinzelError as exc:
            msg = f"{exc} Install it ({DOCS}), or bind the command runner: {BIND_COMMAND_RUNNER}"
            raise HeinzelError(msg) from None
        allowed = (tools.tools or list(self._settings.read_only_tools)) if tools.read_files else []
        if unsafe := [tool for tool in allowed if tool not in self._settings.read_only_tools]:
            msg = (
                f"The tool profile asks for {', '.join(unsafe)}, which [system.runner] read_only_tools doesn't list as "
                f"read-only; runs never get tools that can change things."
            )
            raise HeinzelError(msg)
        names = ",".join(allowed)
        isolation = (*(NO_SETTINGS if tools.mcp_servers else SAFE_MODE), *ISOLATION)
        command = [executable, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        command += [*isolation, "--tools", names, "--allowed-tools", ",".join([*allowed, *servers(tools)])]
        if mcp_file is not None:
            command += ["--mcp-config", str(mcp_file)]
        if contract_file is not None:
            flag = "--append-system-prompt-file" if tools.read_files else "--system-prompt-file"
            command += [flag, str(contract_file)]
        if chosen := model or self._settings.model:
            command += ["--model", chosen]
        if effort is not None:
            command += ["--effort", effort]
        if budget.max_usd is not None:
            command += ["--max-budget-usd", f"{budget.max_usd:g}"]
        return command

    def run(  # noqa: PLR0913 - the protocol's run, by keyword
        self,
        prompt: str,
        *,
        contract: str,
        tools: ToolProfile,
        timeout: timedelta,
        budget: Budget,
        model: str | None = None,
        effort: Effort | None = None,
    ) -> AgentResult:
        """Run `prompt` under `contract` with read-only `tools`; stop at `timeout` or `budget`.

        Raises:
            HeinzelError: If the CLI is missing, the profile isn't read-only, or the working directory is missing.
        """
        cwd = working_dir(tools)
        with tempfile.TemporaryDirectory(prefix="hz-run-") as directory:  # 0700: only this user reads the contract
            contract_file = None
            if contract or not tools.read_files:  # a lean run always brings its own short system prompt
                contract_file = Path(directory) / "contract.md"
                contract_file.write_text(contract or LEAN_PROMPT, encoding="utf-8")
            mcp_file = None
            if tools.mcp_servers:
                mcp_file = Path(directory) / "mcp.json"
                mcp_file.write_text(json.dumps(mcp_config(tools)), encoding="utf-8")
            command = self.command(
                contract_file=contract_file, tools=tools, budget=budget, mcp_file=mcp_file, model=model, effort=effort
            )
            started = time.monotonic()
            outcome = self._execute(
                command,
                input=prompt,
                cwd=cwd,
                env=subprocess_environment() | QUIET,
                timeout=timeout.total_seconds(),
                on_line=Relay(self._progress.emitter()),
            )
        result = interpret(outcome, timeout, time.monotonic() - started)
        if result.billing is None and _no_api_key(outcome.stdout):
            # No API key: a claude.ai subscription, or a cloud provider's login. Only `auth status` tells them apart.
            if self._login is False:
                self._login = auth_status(command[0], self._execute)[1]
            result = result.model_copy(update={"billing": self._login or None})
        return result


def servers(tools: ToolProfile) -> list[str]:
    """The permission rules that allow every tool of the profile's MCP servers: `mcp__<name>`."""
    return [f"mcp__{name}" for name in sorted(tools.mcp_servers)]


def mcp_config(tools: ToolProfile) -> dict[str, object]:
    """The `--mcp-config` file of a profile: each server as a stdio process."""
    return {
        "mcpServers": {
            name: {"type": "stdio", "command": server.command[0], "args": server.command[1:], "env": server.env}
            for name, server in sorted(tools.mcp_servers.items())
        }
    }


class Relay:
    """Turns stream-json lines into `text` and `tool` events for a head to stream, as they arrive."""

    def __init__(self, emit: Emit) -> None:
        """Relay to `emit`."""
        self._emit = emit

    def __call__(self, line: str) -> None:
        """Emit what `line` adds: a text delta of the answer, or the name of a tool the agent calls."""
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        event = cast("dict[str, Any]", event)  # checked to be a dict just before
        if event.get("type") == "stream_event":
            inner = event.get("event")
            # Each cast follows an isinstance check: JSON objects, typed for the type checker.
            delta = cast("dict[str, Any]", inner).get("delta") if isinstance(inner, dict) else None
            if isinstance(delta, dict):
                delta = cast("dict[str, Any]", delta)  # checked to be a dict just before
                if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    self._emit("text", {"text": delta["text"]})
        elif event.get("type") == "assistant":
            for name in _tool_uses(event):
                self._emit("tool", {"name": name})


def interpret(outcome: Outcome, timeout: timedelta, duration: float) -> AgentResult:
    """The result of a finished, failed or stopped `claude` process."""
    result = parse_events(outcome.stdout.splitlines(), duration)
    if outcome.timed_out:
        detail = f"stopped after {timeout}, the run's timeout"
        return result.model_copy(update={"status": "timeout", "draft": "", "detail": detail})
    cut = f"; output over {MAX_OUTPUT} bytes was cut" if outcome.truncated else ""
    if outcome.returncode != 0 and result.status in {"completed", "failed"}:
        why = result.detail or "Claude Code ended"
        stderr = outcome.stderr_tail()
        detail = f"{why} (exit code {outcome.returncode}){': ' + stderr if stderr else ''}{cut}"
        return result.model_copy(update={"status": "failed", "draft": "", "detail": detail})
    if result.status == "completed" and not result.draft.strip():
        return result.model_copy(update={"status": "failed", "detail": "Claude Code returned an empty result"})
    if cut and result.status != "completed":
        return result.model_copy(update={"detail": result.detail + cut})
    return result


def _events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """The JSON objects among `lines`.

    Yields:
        Each line that is a JSON object; anything else is skipped.
    """
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield cast("dict[str, Any]", event)  # checked to be a dict just before


def _no_api_key(stdout: str) -> bool:
    """Whether Claude Code's init event says the run used no API key (`apiKeySource` is `none`)."""
    return any(e.get("type") == "system" and e.get("apiKeySource") == "none" for e in _events(stdout.splitlines()))


def parse_events(lines: Iterable[str], duration: float) -> AgentResult:
    """An `AgentResult` from Claude Code's stream-json events; fields it doesn't report stay none."""
    model: str | None = None
    billing: Billing | None = None
    calls: Counter[str] = Counter()
    final: dict[str, Any] | None = None
    api_error: str | None = None
    for event in _events(lines):
        kind = event.get("type")
        if kind == "system" and isinstance(event.get("model"), str):
            model = event["model"]
            source = event.get("apiKeySource")
            if isinstance(source, str) and source != "none":  # an API key; `none` alone doesn't mean a plan
                billing = "api"
        elif kind == "assistant":
            calls.update(_tool_uses(event))
            if isinstance(event.get("error"), str):
                api_error = event["error"]
        elif kind == "result":
            final = event
    if final is None:
        return AgentResult(
            runner=NAME,
            model=model,
            billing=billing,
            status="failed",
            tool_calls=dict(calls),
            duration_seconds=duration,
            detail="Claude Code printed no result",
        )
    subtype = str(final.get("subtype", ""))
    status: RunStatus = "completed"
    if "budget" in subtype:
        status = "budget"
    elif final.get("is_error") is True or subtype != "success":  # is_error wins: a failed login ends with success
        status = "failed"
    detail, fix = ("", "") if status == "completed" else explain(final, subtype, api_error)
    return AgentResult(
        runner=NAME,
        model=model,
        billing=billing,
        status=status,
        draft=str(final.get("result", "")) if status == "completed" else "",
        tool_calls=dict(calls),
        turns=whole_number(final.get("num_turns")),
        usage=_usage(final),
        duration_seconds=duration,
        detail=detail,
        fix=fix,
    )


LOGIN = "claude auth login"
"""The fix when Claude Code isn't logged in, or its login expired: it signs in without a session."""
ERROR_TEXT = 300
"""Characters of Claude Code's own error text kept in a detail line."""
SIGN_IN = "run `claude` in a terminal and `/login` (or `claude auth login`), then try again"
"""What to do about a missing or expired sign-in, as the chat shows it."""
KNOWN_ERRORS = (
    # (what Claude Code says, as a lower-case fragment or its error code; what to tell the person; the fix)
    (
        ("expired", "could not be refreshed"),
        f"Claude Code's sign-in expired: {SIGN_IN}",
        LOGIN,
    ),
    (
        ("authentication_failed", "not logged in", "failed to authenticate", "invalid api key", "/login"),
        f"Claude Code isn't signed in: {SIGN_IN}",
        LOGIN,
    ),
    (("rate_limit", "rate limit", "429"), "Claude Code hit a rate limit; try again in a minute", ""),
    (("overloaded", "529"), "The model is overloaded right now; try again shortly", ""),
    (("credit balance", "billing", "quota"), "The account Claude Code uses has no credit or quota left", ""),
)


def explain(final: dict[str, Any], subtype: str, api_error: str | None) -> tuple[str, str]:
    """Why a run failed, in words, and the command that fixes it when there is one.

    Claude Code reports a failed login or an API error as `is_error: true`, often with `subtype: success`, and puts
    what went wrong in `result` (and an `error` code on the assistant event); that text is shown, cleaned and cut.
    """
    said = clean_text(str(final.get("result") or "")).strip()
    said = said if len(said) <= ERROR_TEXT else said[: ERROR_TEXT - 1] + "…"
    haystack = f"{api_error or ''} {said} {final.get('terminal_reason') or ''}".lower()
    for fragments, message, fix in KNOWN_ERRORS:
        if any(fragment in haystack for fragment in fragments):
            return (f"{message} ({said})" if said else message), fix
    if said:
        return f"Claude Code: {said}", ""
    return f"Claude Code ended with {api_error or subtype or 'an error'}", ""


def _tool_uses(event: dict[str, Any]) -> list[str]:
    message = event.get("message")
    # Each cast follows an isinstance check: JSON objects and lists, typed for the type checker.
    content = cast("dict[str, Any]", message).get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    blocks = [cast("dict[str, Any]", block) for block in cast("list[object]", content) if isinstance(block, dict)]
    return [str(block["name"]) for block in blocks if block.get("type") == "tool_use" and "name" in block]


def _usage(final: dict[str, Any]) -> TokenUsage:
    raw = final.get("usage")
    usage = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}  # checked to be a dict just before
    return TokenUsage(
        input_tokens=whole_number(usage.get("input_tokens")),
        output_tokens=whole_number(usage.get("output_tokens")),
        cache_read_tokens=whole_number(usage.get("cache_read_input_tokens")),
        cache_write_tokens=whole_number(usage.get("cache_creation_input_tokens")),
        cost_usd=amount(final.get("total_cost_usd")),
    )


LOGIN_TIMEOUT = 10.0
"""Seconds `claude auth status` may take; it reads Claude Code's own login and calls no model."""


def login_check(executable: str, execute: Execute = run_bounded) -> Check:
    """Whether Claude Code is logged in, from `claude auth status --json`; spends nothing."""
    return auth_status(executable, execute)[0]


def auth_status(executable: str, execute: Execute = run_bounded) -> tuple[Check, Billing | None]:
    """Whether Claude Code is logged in, and how it is billed: `plan` for a claude.ai login, `api` for a key.

    From `claude auth status --json`, which spends nothing.

    Best effort: a Claude Code without `auth status`, or output it can't read, is reported as unknown, not as a
    problem. A login that expired on the server may still show as logged in until a run tries it; the chat then
    says so with the same fix.
    """
    try:
        # In an empty directory, like a run: no project's Claude settings (and their helpers) come into play.
        with tempfile.TemporaryDirectory(prefix="hz-auth-") as directory:
            outcome = execute(
                [executable, "auth", "status", "--json"],
                input=None,
                cwd=Path(directory),
                env=subprocess_environment() | QUIET,
                timeout=LOGIN_TIMEOUT,
            )
    except OSError as exc:
        return Check(
            name="claude-code login",
            status=Status.DEGRADED,
            detail=f"`claude auth status` didn't start: {exc}",
            fix=f"Reinstall Claude Code ({DOCS})",
        ), None
    status: object = None
    method: object = None
    try:  # it exits with 1 when not logged in, so the JSON decides, not the exit code
        data = json.loads(outcome.stdout)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        status = cast("dict[str, Any]", data).get("loggedIn")  # checked to be a dict just before
        method = cast("dict[str, Any]", data).get("authMethod")
    billing: Billing | None = None
    if isinstance(method, str):  # `claude.ai`: the person's subscription; an API key otherwise
        billing = "plan" if method == "claude.ai" else "api"
    if status is True:
        return Check(name="claude-code login", status=Status.UP, detail="logged in"), billing
    if status is False:
        return Check(
            name="claude-code login",
            status=Status.DEGRADED,
            detail="Claude Code isn't logged in, so the chat and agent runs fail",
            fix=LOGIN,
        ), None
    return Check(
        name="claude-code login",
        status=Status.UP,
        detail="unknown: this Claude Code has no `auth status`; a run says when it isn't logged in",
    ), None
