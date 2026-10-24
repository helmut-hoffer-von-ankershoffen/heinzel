"""The generic command agent runner: any agent CLI from a configured command line (ADR 0029).

The command line is a list of arguments with placeholders, run without a shell, from the user's configuration only.
It fails closed: it refuses to run until the user records that the command restricts its agent to read-only tools
(`command_enforces_read_only`), and passes only tools listed in `command_read_only_tools`. The prompt and the contract
are written to files in a private temporary directory; the result is read from stdout or from `{output_file}`, as
text or JSON. The environment is an allowlist. The runner enforces the timeout; the budget only if the command takes
`{max_usd}`.
"""

import json
import os
import shutil
import stat
import string
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from heinzel import AgentResult, Budget, HeinzelError, TokenUsage, ToolProfile
from heinzel.kernel import subprocess_environment
from heinzel.modules.system.runner._claude import mcp_config, resolve, working_dir
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import MAX_OUTPUT, Execute, amount, run_bounded, whole_number

NAME = "command"
SET_COMMAND = """hz system config set system.runner.command '["mytool", "run", "--prompt", "{prompt_file}"]'"""
TRUST_COMMAND = "hz system config set system.runner.command_enforces_read_only true"
PLACEHOLDERS = frozenset(
    {
        "prompt_file",
        "contract_file",
        "output_file",
        "working_dir",
        "tools",
        "model",
        "timeout_seconds",
        "max_usd",
        "mcp_config",
    }
)
BASE_ENVIRONMENT = frozenset({"PATH", "HOME", "LANG", "TMPDIR"})


def placeholders(argument: str) -> set[str]:
    """The placeholders an argument uses; `{{` and `}}` are literal braces.

    Raises:
        HeinzelError: If the argument is not a valid template, uses a format spec or conversion (`{tools!r}`,
            `{model:>9}`), or names an unknown placeholder.
    """
    try:
        fields = [(name, spec, conversion) for _, name, spec, conversion in string.Formatter().parse(argument)]
    except ValueError as exc:
        msg = f"[system.runner] command argument {argument!r} is not a valid template: {exc}"
        raise HeinzelError(msg) from exc
    names = {name for name, _, _ in fields if name is not None}
    if any(spec or conversion for name, spec, conversion in fields if name is not None):
        msg = f"[system.runner] command argument {argument!r} uses a format spec or conversion; write `{{name}}` only"
        raise HeinzelError(msg)
    if unknown := names - PLACEHOLDERS:
        msg = (
            f"[system.runner] command argument {argument!r} uses unknown placeholders {sorted(unknown)}; "
            f"known: {', '.join(sorted(PLACEHOLDERS))}"
        )
        raise HeinzelError(msg)
    return names


def render(argv: list[str], values: Mapping[str, str]) -> list[str]:
    """Fill the placeholders of each argument; arguments stay separate, so nothing is ever split or interpreted.

    Raises:
        HeinzelError: If an argument is not a valid template (see `placeholders`).
    """
    rendered: list[str] = []
    for argument in argv:
        placeholders(argument)
        rendered.append(argument.format_map(values))
    return rendered


def environment(names: list[str], source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The command's environment: PATH, HOME, LANG, LC_* and TMPDIR plus `names`, never a credential variable."""
    wanted = BASE_ENVIRONMENT | set(names)
    return {
        key: value for key, value in subprocess_environment(source).items() if key in wanted or key.startswith("LC_")
    }


def read_output(path: Path, limit: int = MAX_OUTPUT) -> str | None:
    """The output file, never through a symlink, at most `limit` bytes; None if the command wrote none.

    Raises:
        HeinzelError: If the file is a symlink or larger than `limit`.
    """
    try:
        # O_NONBLOCK: a FIFO left at the path must not hang the read
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP: a symlink, which could point anywhere the user can read
        msg = f"The command's output file is not a regular file ({exc.strerror}); it was not read."
        raise HeinzelError(msg) from None
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):  # a FIFO, a directory or a device
        os.close(descriptor)
        msg = "The command's output file is not a regular file; it was not read."
        raise HeinzelError(msg)
    with os.fdopen(descriptor, "rb") as file:
        data = file.read(limit + 1)
    if len(data) > limit:
        msg = f"The command's output file is larger than {limit} bytes; it was not read."
        raise HeinzelError(msg)
    return data.decode(errors="replace")


class CommandRunner:
    """Runs one agent session with a configured command line."""

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

    def _check(self, tools: ToolProfile) -> str:
        settings = self._settings
        if not settings.command:
            msg = f"The command runner has no command; set the agent CLI's command line, e.g.: {SET_COMMAND}"
            raise HeinzelError(msg)
        if not settings.command_enforces_read_only:
            msg = (
                "The command runner refuses to run: nothing records that the configured command restricts its agent "
                f"to read-only tools. Configure that in the command, then record that it does: {TRUST_COMMAND}"
            )
            raise HeinzelError(msg)
        if unsafe := [tool for tool in tools.tools if tool not in settings.command_read_only_tools]:
            msg = (
                f"The tool profile asks for {', '.join(unsafe)}, which [system.runner] command_read_only_tools doesn't "
                f"list as read-only; runs never get tools that can change things."
            )
            raise HeinzelError(msg)
        for argument in settings.command[1:]:
            placeholders(argument)
        return resolve(settings.command[0], self._which, "The command runner's executable")

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        """Write prompt and contract to files, run the command, read the result; stop at `timeout`.

        Raises:
            HeinzelError: If the runner is not configured to run (no command, read-only not recorded, a tool not
                listed, a bad placeholder, a missing executable or working directory) or the output file is unsafe.
        """
        settings = self._settings
        cwd = working_dir(tools)
        executable = self._check(tools)
        warnings = self._warnings(budget, tools)
        with tempfile.TemporaryDirectory(prefix="hz-run-") as directory:  # 0700: only this user reads the prompt
            folder = Path(directory)
            (folder / "prompt.md").write_text(prompt, encoding="utf-8")
            (folder / "contract.md").write_text(contract, encoding="utf-8")
            output_file = folder / "output"
            (folder / "mcp.json").write_text(json.dumps(mcp_config(tools)), encoding="utf-8")
            values = {
                "prompt_file": str(folder / "prompt.md"),
                "contract_file": str(folder / "contract.md"),
                "output_file": str(output_file),
                "working_dir": str(cwd or Path.cwd()),
                "tools": ",".join(tools.tools),
                "model": settings.model or "",
                "timeout_seconds": str(int(timeout.total_seconds())),
                "max_usd": "" if budget.max_usd is None else f"{budget.max_usd:g}",
                "mcp_config": str(folder / "mcp.json"),
            }
            command = [executable, *render(settings.command[1:], values)]
            started = time.monotonic()
            outcome = self._execute(
                command, input=None, cwd=cwd, env=environment(settings.command_env), timeout=timeout.total_seconds()
            )
            duration = time.monotonic() - started
            base: dict[str, Any] = {"runner": NAME, "model": settings.model, "duration_seconds": duration}
            if outcome.timed_out:
                detail = f"stopped after {timeout}, the run's timeout"
                return AgentResult(**base, status="timeout", detail=detail, warnings=warnings)
            if outcome.returncode != 0:
                detail = f"exit code {outcome.returncode}: {outcome.stderr_tail()}"
                return AgentResult(**base, status="failed", detail=detail, warnings=warnings)
            if settings.command_output == "file":
                output = read_output(output_file)
                if output is None:
                    detail = "the command wrote no output file"
                    return AgentResult(**base, status="failed", detail=detail, warnings=warnings)
            elif outcome.truncated:
                detail = f"the command printed more than {MAX_OUTPUT} bytes"
                return AgentResult(**base, status="failed", detail=detail, warnings=warnings)
            else:
                output = outcome.stdout
        return self._result(output, base, warnings)

    def _warnings(self, budget: Budget, tools: ToolProfile) -> list[str]:
        used = set[str]().union(*(placeholders(argument) for argument in self._settings.command[1:]))
        warnings: list[str] = []
        if budget.max_usd is not None and "max_usd" not in used:
            warnings.append(f"the budget of {budget.max_usd:g} USD is not passed to the command, so not enforced")
        if not tools.read_files:
            warnings.append("the command decides its agent's file tools itself; this run asked for none")
        if tools.mcp_servers and "mcp_config" not in used:
            names = ", ".join(sorted(tools.mcp_servers))
            warnings.append(f"the command takes no {{mcp_config}}, so the agent had no tools of {names}")
        return warnings

    def _result(self, output: str, base: dict[str, Any], warnings: list[str]) -> AgentResult:
        settings = self._settings
        if settings.command_format == "text":
            if not output.strip():
                return AgentResult(**base, status="failed", detail="the command printed nothing", warnings=warnings)
            return AgentResult(**base, status="completed", draft=output.strip(), warnings=warnings)
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            return AgentResult(**base, status="failed", detail=f"output is not JSON: {exc}", warnings=warnings)
        draft = cast("dict[str, Any]", data).get(settings.command_draft_key) if isinstance(data, dict) else None
        if not isinstance(draft, str) or not draft.strip():
            detail = f"output has no text under {settings.command_draft_key!r}"
            return AgentResult(**base, status="failed", detail=detail, warnings=warnings)
        data = cast("dict[str, Any]", data)
        raw = data.get("usage")
        usage = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        return AgentResult(
            **base,
            status="completed",
            draft=draft.strip(),
            usage=TokenUsage(
                input_tokens=whole_number(usage.get("input_tokens")),
                output_tokens=whole_number(usage.get("output_tokens")),
                cost_usd=amount(data.get("total_cost_usd")),
            ),
            warnings=warnings,
        )
