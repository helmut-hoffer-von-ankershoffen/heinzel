"""Claude Code as an AI access option: the CLI and its own login, beside the runner of the same name (ADR 0057).

`status` asks `claude auth status`, which spends nothing; `test` runs one line through the `claude-code` runner with no
tools, in an empty directory and within a small cap. The option names the runner `claude-code`, so `hz ai access use
claude-code` binds it for the chat and coding handoffs too.
"""

import shutil
import tempfile
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from heinzel import AccessStatus, AccessTest, Budget, HeinzelError, Progress, Status, ToolProfile
from heinzel.kernel import Effort
from heinzel.modules.system.runner._claude import DOCS, LOGIN, NAME, SILENT, ClaudeCodeRunner, auth_status
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Execute, run_bounded

TITLE = "Claude Code"
SUMMARY = "The Claude Code CLI with your own Claude login or subscription; it also runs the chat and coding handoffs."
TEST_PROMPT = "Reply with the single word OK."
TEST_TIMEOUT = timedelta(seconds=90)
TEST_MAX_USD = 0.05
"""Most a test may cost: one short answer, no tools."""
ANSWER_LIMIT = 200
MODELS = ("fable", "opus", "sonnet", "haiku")
"""The aliases Claude Code takes for the latest model of each family (`claude --help`, 2.1); a full model name works
too."""
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")
"""The efforts `claude --effort` takes (Claude Code 2.1)."""


class ClaudeCodeAccess:
    """The `claude-code` option of `ai-access`."""

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
        """Whether the CLI is on PATH and logged in, from `claude auth status`; calls no model."""
        configured = [self._settings.model] if self._settings.model else []
        models = list(dict.fromkeys([*configured, *MODELS]))
        found = self._which(self._settings.claude_executable)
        if found is None:
            return AccessStatus(
                title=TITLE,
                summary=SUMMARY,
                installed=False,
                detail=f"{self._settings.claude_executable} is not on PATH",
                fix=f"Install Claude Code: {DOCS}",
                models=models,
                efforts=list(EFFORTS),
                runner=NAME,
            )
        login, billing = auth_status(found, self._execute)
        signed_in: bool | None = None
        if login.status is Status.UP and login.detail == "logged in":
            signed_in = True
        elif login.fix == LOGIN:
            signed_in = False
        return AccessStatus(
            title=TITLE,
            summary=SUMMARY,
            installed=True,
            signed_in=signed_in,
            detail=f"{found}; {login.detail}",
            fix=login.fix,
            models=models,
            efforts=list(EFFORTS),
            runner=NAME,
            billing=billing,
        )

    def test(self) -> AccessTest:
        """One short answer through the `claude-code` runner: no tools, an empty directory, at most TEST_MAX_USD."""
        runner = ClaudeCodeRunner(self._settings, self._progress, self._execute, self._which)
        try:
            with tempfile.TemporaryDirectory(prefix="hz-ai-test-") as directory:
                result = runner.run(
                    TEST_PROMPT,
                    contract="",
                    tools=ToolProfile(working_dir=Path(directory), read_files=False),
                    timeout=TEST_TIMEOUT,
                    budget=Budget(max_usd=TEST_MAX_USD),
                )
        except HeinzelError as exc:  # the CLI is missing: a failed check, not an error
            return AccessTest(ok=False, detail=str(exc), fix=f"Install Claude Code: {DOCS}")
        ok = result.status == "completed"
        return AccessTest(
            ok=ok,
            detail=(result.draft.strip()[:ANSWER_LIMIT] if ok else result.detail) or result.status,
            fix=result.fix,
            model=result.model,
            cost_usd=result.usage.cost_usd,
            duration_seconds=result.duration_seconds,
        )
