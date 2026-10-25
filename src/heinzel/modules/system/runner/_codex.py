"""The Codex CLI as an agent runner and an AI access option: a ChatGPT plan's coding agent (ADR 0029, 0057).

A run is `codex exec` with the prompt on standard input (`-`), newline-delimited JSON events (`--json`), a read-only
sandbox (`--sandbox read-only`), no session kept (`--ephemeral`), in the run's working directory (`-C`, allowed
outside a git repository with `--skip-git-repo-check`); `-m` sets the model and `-c model_reasoning_effort=...` the
effort, `-c mcp_servers.<name>.*` the run's MCP servers. Codex has no flag for a system prompt, so the contract comes
first in the prompt. Its events: `item.completed` items of type `agent_message` (`text`), `command_execution` and
`mcp_tool_call`; `turn.completed` with `usage` (`input_tokens`, `cached_input_tokens`, `output_tokens`); `turn.failed`
and `error`. Codex reports no cost and has no budget or tool allowlist: its read-only sandbox prevents writes, not
reads, so a run says so in its warnings. `codex login status` exits 0 when signed in and names how.

Flags and events verified against OpenAI's Codex documentation only (2026-10-25), not against a live run.
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

from heinzel import AccessStatus, AccessTest, AgentResult, Budget, HeinzelError, Progress, TokenUsage, ToolProfile
from heinzel.kernel import RunStatus, clean_text
from heinzel.modules.system.runner._claude import SILENT, resolve, working_dir
from heinzel.modules.system.runner._command import environment
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Execute, Outcome, run_bounded, whole_number

NAME = "codex"
TITLE = "Codex"
SUMMARY = "The Codex CLI with your ChatGPT plan (or a key `codex login` keeps); it can also run the chat."
HOSTS = ("api.openai.com", "chatgpt.com")
DOCS = "https://developers.openai.com/codex/cli"
LOGIN = "codex login"
LOGIN_TIMEOUT = 10.0
TEST_PROMPT = "Reply with the single word OK."
TEST_TIMEOUT = timedelta(seconds=90)
DETAIL_LIMIT = 300
ENVIRONMENT = ["CODEX_HOME"]
"""Variables a run gets besides PATH, HOME, LANG, LC_* and TMPDIR: where Codex keeps its login."""
READS = "Codex's read-only sandbox prevents writes, not reads: the agent may read files outside its working directory"
MESSAGES = {"agent_message", "assistant_message"}
"""The item types of the answer; `assistant_message` before Codex 0.44."""


def _toml(value: str | list[str] | dict[str, str]) -> str:
    """A TOML value for `-c key=value`: a string, a list of strings or a table of strings, as JSON writes them.

    Raises:
        HeinzelError: If a string holds a control character, which a TOML string can't hold as JSON writes it.
    """
    if isinstance(value, str):
        strings = [value]
    elif isinstance(value, list):
        strings = value
    else:
        strings = [text for pair in value.items() for text in pair]
    if not all(text.isprintable() for text in strings):
        msg = "An MCP server's command, arguments or variables hold a control character; Codex can't take them"
        raise HeinzelError(msg)

    def quoted(text: str) -> str:
        return json.dumps(text, ensure_ascii=False)

    if isinstance(value, dict):
        return "{" + ", ".join(f"{quoted(key)} = {quoted(text)}" for key, text in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(quoted(text) for text in value) + "]"
    return quoted(value)


def mcp_options(tools: ToolProfile) -> list[str]:
    """`-c` options that give the run its MCP servers, each a stdio process.

    A server's name is safe in the dotted key: `ToolProfile` admits only `[a-z][a-z0-9_-]*`.
    """
    options: list[str] = []
    for name, server in sorted(tools.mcp_servers.items()):
        options += ["-c", f"mcp_servers.{name}.command={_toml(server.command[0])}"]
        options += ["-c", f"mcp_servers.{name}.args={_toml(server.command[1:])}"]
        if server.env:
            options += ["-c", f"mcp_servers.{name}.env={_toml(server.env)}"]
    return options


def _events(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    for line in lines:
        try:
            event: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield cast("dict[str, Any]", event)


def _message(error: object) -> str:
    if isinstance(error, dict):
        error = cast("dict[str, Any]", error).get("message", "")
    return clean_text(str(error or ""))[:DETAIL_LIMIT]


def _about_login(text: str) -> bool:
    """Whether a failure is about the sign-in, so `codex login` fixes it."""
    lower = text.lower()
    return any(sign in lower for sign in ("not logged in", "codex login", "unauthorized", "401", "login required"))


def parse_events(lines: Iterable[str], duration: float, model: str | None) -> AgentResult:
    """An `AgentResult` from `codex exec --json` events; what Codex doesn't report stays none."""
    answer, calls, usage, failure, noted, turns = "", Counter[str](), TokenUsage(), "", "", 0
    for event in _events(lines):
        kind = event.get("type")
        item: object = event.get("item")
        if kind == "item.completed" and isinstance(item, dict):
            item = cast("dict[str, Any]", item)
            item_type = item.get("type") or item.get("item_type")
            if item_type in MESSAGES and isinstance(item.get("text"), str):
                answer = item["text"]
            elif item_type == "command_execution":
                calls["shell"] += 1
            elif item_type == "mcp_tool_call":
                calls[str(item.get("tool") or item.get("name") or "mcp")] += 1
        elif kind == "turn.completed":
            turns += 1
            found: object = event.get("usage")
            if isinstance(found, dict):
                counts = cast("dict[str, Any]", found)
                total, cached = (
                    whole_number(counts.get("input_tokens")),
                    whole_number(counts.get("cached_input_tokens")),
                )
                usage = TokenUsage(
                    input_tokens=None if total is None else max(total - (cached or 0), 0),
                    output_tokens=whole_number(counts.get("output_tokens")),
                    cache_read_tokens=cached,
                )
        elif kind == "turn.failed":
            failure = _message(event.get("error"))
        elif kind == "error":  # also a notice mid-run ("Reconnecting..."): it fails a run only without an answer
            noted = _message(event.get("message"))
    status: RunStatus = "completed" if answer and not failure else "failed"
    detail = "" if status == "completed" else failure or noted or "Codex printed no answer"
    fix = LOGIN if _about_login(detail) else ""
    return AgentResult(
        runner=NAME,
        model=model,
        status=status,
        draft=answer if status == "completed" else "",
        tool_calls=dict(calls),
        turns=turns or None,
        usage=usage,
        duration_seconds=duration,
        detail=detail,
        fix=fix,
    )


class CodexRunner:
    """Runs one agent session with `codex exec`, read-only."""

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

    def command(self, *, cwd: Path, tools: ToolProfile) -> list[str]:
        """The command line of a run; the prompt goes to standard input.

        Raises:
            HeinzelError: If Codex is not on PATH.
        """
        try:
            executable = resolve(self._settings.codex_executable, self._which, "Codex")
        except HeinzelError as exc:
            msg = f"{exc} Install it ({DOCS}), or pick another AI access: hz ai access list"
            raise HeinzelError(msg) from None
        command = [executable, "exec", "--json", "--sandbox", "read-only", "--ephemeral", "--skip-git-repo-check"]
        command += ["-C", str(cwd)]
        if self._settings.codex_model:
            command += ["-m", self._settings.codex_model]
        if self._settings.codex_effort:
            command += ["-c", f"model_reasoning_effort={_toml(self._settings.codex_effort)}"]
        return [*command, *mcp_options(tools), "-"]

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        """Run `prompt` under `contract`, read-only; stop at `timeout`.

        Raises:
            HeinzelError: If Codex is not on PATH or the working directory is missing.
        """
        warnings = [READS]
        if budget.max_usd is not None:
            warnings.append(f"Codex takes no budget, so the cap of {budget.max_usd:g} USD is not enforced")
        if tools.tools:
            warnings.append("Codex takes no tool allowlist: its read-only sandbox decides what it may run")
        if any(server.env for server in tools.mcp_servers.values()):
            warnings.append("an MCP server's variables go to Codex on its command line, where other users can see them")
        warnings.append("Codex also loads the MCP servers of your own ~/.codex/config.toml")
        with tempfile.TemporaryDirectory(prefix="hz-run-") as directory:
            cwd = working_dir(tools) or Path(directory)
            command = self.command(cwd=cwd, tools=tools)
            text = f"{contract}\n\n---\n\n{prompt}" if contract else prompt
            started = time.monotonic()
            outcome: Outcome = self._execute(
                command, input=text, cwd=cwd, env=environment(ENVIRONMENT), timeout=timeout.total_seconds()
            )
        duration = time.monotonic() - started
        if outcome.timed_out:
            return AgentResult(
                runner=NAME,
                status="timeout",
                duration_seconds=duration,
                detail=f"stopped after {timeout}, the run's timeout",
                warnings=warnings,
            )
        result = parse_events(outcome.stdout.splitlines(), duration, self._settings.codex_model)
        if outcome.returncode != 0 and result.status == "completed":
            result = result.model_copy(update={"status": "failed", "draft": ""})
        if result.status == "failed" and outcome.returncode != 0:
            tail = clean_text(outcome.stderr_tail())
            detail = f"{result.detail} (exit code {outcome.returncode}){': ' + tail if tail else ''}"[:DETAIL_LIMIT]
            fix = result.fix or (LOGIN if _about_login(tail) else "")
            result = result.model_copy(update={"detail": detail, "fix": fix})
        if result.status == "completed":
            self._progress.emitter()("text", {"text": result.draft})
        return result.model_copy(update={"warnings": [*result.warnings, *warnings]})


class CodexAccess:
    """The `codex` option of `ai-access`: the CLI and its own login; it names the runner `codex`."""

    def __init__(
        self,
        settings: RunnerSettings,
        progress: Progress = SILENT,
        execute: Execute = run_bounded,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Create the option; settings and progress are injected, `execute` and `which` replace processes in tests."""
        self._settings = settings
        self._progress = progress
        self._execute = execute
        self._which = which

    def status(self) -> AccessStatus:
        """Whether Codex is on PATH and signed in, from `codex login status`; calls no model."""
        models = [self._settings.codex_model] if self._settings.codex_model else []
        base = AccessStatus(title=TITLE, summary=SUMMARY, installed=False, models=models, runner=NAME)
        found = self._which(self._settings.codex_executable)
        if found is None:
            detail = f"{self._settings.codex_executable} is not on PATH"
            return base.model_copy(update={"detail": detail, "fix": f"Install Codex: {DOCS}"})
        try:
            with tempfile.TemporaryDirectory(prefix="hz-auth-") as directory:
                outcome = self._execute(
                    [found, "login", "status"],
                    input=None,
                    cwd=Path(directory),
                    env=environment(ENVIRONMENT),
                    timeout=LOGIN_TIMEOUT,
                )
        except OSError as exc:
            return base.model_copy(update={"detail": f"`codex login status` didn't start: {exc}", "fix": LOGIN})
        printed = (outcome.stdout or outcome.stderr).strip()
        said = clean_text(printed.splitlines()[0]) if printed else ""
        if outcome.timed_out:
            return base.model_copy(update={"installed": True, "detail": "`codex login status` didn't answer in time"})
        signed_in = outcome.returncode == 0
        detail = f"{found}; {said[:DETAIL_LIMIT] or ('signed in' if signed_in else 'not signed in')}"
        return base.model_copy(
            update={"installed": True, "signed_in": signed_in, "detail": detail, "fix": "" if signed_in else LOGIN}
        )

    def test(self) -> AccessTest:
        """One short answer through the `codex` runner, in an empty directory; a failure is a result."""
        runner = CodexRunner(self._settings, self._progress, self._execute, self._which)
        try:
            with tempfile.TemporaryDirectory(prefix="hz-ai-test-") as directory:
                result = runner.run(
                    TEST_PROMPT,
                    contract="",
                    tools=ToolProfile(working_dir=Path(directory), read_files=False),
                    timeout=TEST_TIMEOUT,
                    budget=Budget(),
                )
        except HeinzelError as exc:  # Codex is missing: a failed check, not an error
            return AccessTest(ok=False, detail=str(exc), fix=f"Install Codex: {DOCS}")
        ok = result.status == "completed"
        return AccessTest(
            ok=ok,
            detail=(result.draft.strip()[:DETAIL_LIMIT] if ok else result.detail) or result.status,
            fix=result.fix,
            model=result.model,
            cost_usd=result.usage.cost_usd,
            duration_seconds=result.duration_seconds,
        )
