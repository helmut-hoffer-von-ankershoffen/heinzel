"""The generic command agent runner: any agent CLI from a configured command line (ADR 0029).

The command line is a list of arguments with placeholders, run without a shell. The prompt and the contract are
written to files in a private temporary directory; the result is read from stdout or from `{output_file}`, as text
or JSON. The runner can enforce the timeout; the read-only profile and the budget only as far as the command does.
"""

import json
import shutil
import string
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from heinzel import AgentResult, Budget, HeinzelError, TokenUsage, ToolProfile
from heinzel.kernel import subprocess_environment
from heinzel.modules.system.runner._models import RunnerSettings

NAME = "command"
PLACEHOLDERS = frozenset(
    {"prompt_file", "contract_file", "output_file", "working_dir", "tools", "model", "timeout_seconds", "max_usd"}
)

Run = Callable[..., "subprocess.CompletedProcess[str]"]


def render(argv: list[str], values: Mapping[str, str]) -> list[str]:
    """Fill the placeholders of each argument; arguments stay separate, so nothing is ever split or interpreted.

    Raises:
        HeinzelError: If an argument names an unknown placeholder or is not a valid template.
    """
    rendered: list[str] = []
    for argument in argv:
        try:
            names = {name for _, name, _, _ in string.Formatter().parse(argument) if name is not None}
        except ValueError as exc:
            msg = f"[system.runner] command argument {argument!r} is not a valid template: {exc}"
            raise HeinzelError(msg) from exc
        if unknown := names - PLACEHOLDERS:
            msg = (
                f"[system.runner] command argument {argument!r} uses unknown placeholders {sorted(unknown)}; "
                f"known: {', '.join(sorted(PLACEHOLDERS))}"
            )
            raise HeinzelError(msg)
        rendered.append(argument.format_map(values))
    return rendered


class CommandRunner:
    """Runs one agent session with a configured command line."""

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

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        """Write prompt and contract to files, run the command, read the result; stop at `timeout`.

        Raises:
            HeinzelError: If no command is configured, its executable is missing, or a placeholder is unknown.
        """
        settings = self._settings
        if not settings.command:
            msg = 'No command is configured for the command runner; set [system.runner] command = ["mytool", ...].'
            raise HeinzelError(msg)
        executable = self._which(settings.command[0])
        if executable is None:
            msg = f"The command runner's executable {settings.command[0]!r} is not on PATH."
            raise HeinzelError(msg)
        warnings = self._warnings(budget)
        with tempfile.TemporaryDirectory(prefix="hz-run-") as directory:  # 0700: only this user reads the prompt
            folder = Path(directory)
            (folder / "prompt.md").write_text(prompt, encoding="utf-8")
            (folder / "contract.md").write_text(contract, encoding="utf-8")
            output_file = folder / "output"
            values = {
                "prompt_file": str(folder / "prompt.md"),
                "contract_file": str(folder / "contract.md"),
                "output_file": str(output_file),
                "working_dir": str(tools.working_dir or Path.cwd()),
                "tools": ",".join(tools.tools),
                "model": settings.model or "",
                "timeout_seconds": str(int(timeout.total_seconds())),
                "max_usd": "" if budget.max_usd is None else f"{budget.max_usd:g}",
            }
            command = [executable, *render(settings.command[1:], values)]
            started = time.monotonic()
            try:
                completed = self._run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout.total_seconds(),
                    cwd=tools.working_dir,
                    env=subprocess_environment(),
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                return AgentResult(
                    runner=NAME,
                    model=settings.model,
                    status="timeout",
                    duration_seconds=time.monotonic() - started,
                    detail=f"stopped after {timeout}, the run's timeout",
                    warnings=warnings,
                )
            duration = time.monotonic() - started
            if completed.returncode != 0:
                return AgentResult(
                    runner=NAME,
                    model=settings.model,
                    status="failed",
                    duration_seconds=duration,
                    detail=f"exit code {completed.returncode}: {completed.stderr.strip()[-500:]}",
                    warnings=warnings,
                )
            if settings.command_output == "file":
                output = output_file.read_text(encoding="utf-8") if output_file.is_file() else ""
            else:
                output = completed.stdout
        return self._result(output, duration, warnings)

    def _warnings(self, budget: Budget) -> list[str]:
        warnings: list[str] = []
        if not self._settings.command_enforces_read_only:
            warnings.append("the command is not known to restrict its agent to read-only tools")
        if budget.max_usd is not None and not any("{max_usd}" in argument for argument in self._settings.command):
            warnings.append(f"the budget of {budget.max_usd:g} USD is not passed to the command, so not enforced")
        return warnings

    def _result(self, output: str, duration: float, warnings: list[str]) -> AgentResult:
        settings = self._settings
        base: dict[str, Any] = {"runner": NAME, "model": settings.model, "duration_seconds": duration}
        if settings.command_format == "text":
            return AgentResult(**base, status="completed", draft=output.strip(), warnings=warnings)
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            return AgentResult(**base, status="failed", detail=f"output is not JSON: {exc}", warnings=warnings)
        if not isinstance(data, dict) or not isinstance(
            cast("dict[str, Any]", data).get(settings.command_draft_key), str
        ):
            detail = f"output has no text under {settings.command_draft_key!r}"
            return AgentResult(**base, status="failed", detail=detail, warnings=warnings)
        data = cast("dict[str, Any]", data)
        raw = data.get("usage")
        usage = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        cost = data.get("total_cost_usd")
        return AgentResult(
            **base,
            status="completed",
            draft=data[settings.command_draft_key].strip(),
            usage=TokenUsage(
                input_tokens=usage.get("input_tokens") if isinstance(usage.get("input_tokens"), int) else None,
                output_tokens=usage.get("output_tokens") if isinstance(usage.get("output_tokens"), int) else None,
                cost_usd=float(cost) if isinstance(cost, int | float) else None,
            ),
            warnings=warnings,
        )
