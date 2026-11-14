"""Claude Code as an AI access option: the CLI and its own login, beside the runner of the same name (ADR 0057).

`status` asks `claude auth status`, which spends nothing; `test` asks one line through the chat's own admission (the
reviewed release range, the help contract, the closed structured result), with no tools and in an empty directory, so
a passing test means chat can run. The option names the runner `claude-code`, so `hz ai access use
claude-code` binds it for the chat and coding handoffs too.
"""

import logging
import shutil
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from heinzel import AccessStatus, AccessTest, HeinzelError, Progress, Status
from heinzel.kernel import Effort
from heinzel.modules.system.runner._claude import DOCS, LOGIN, NAME, SILENT, auth_status
from heinzel.modules.system.runner._models import RunnerSettings
from heinzel.modules.system.runner._process import Execute, run_bounded

TITLE = "Claude Code"
SUMMARY = "The Claude Code CLI with your own Claude login or subscription; it also runs the chat and coding handoffs."
TEST_PROMPT = "Reply with the single word OK."
TEST_TIMEOUT = timedelta(seconds=90)
ANSWER_LIMIT = 200
MODELS = ("fable", "opus", "sonnet", "haiku")
"""The aliases Claude Code takes for the latest model of each family (`claude --help`, 2.1); a full model name works
too."""
logger = logging.getLogger(__name__)
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
        """One short answer through the chat's own admission: reviewed release, help contract, closed result."""
        from heinzel.modules.heinzel._claude_subscription_model import (  # noqa: PLC0415 - bridge runtime only when testing
            Denial,
            SubscriptionDeniedError,
            check_access,
        )

        found = self._which(self._settings.claude_executable)
        if found is None:
            return AccessTest(
                ok=False,
                detail=f"{self._settings.claude_executable} is not on PATH",
                fix=f"Install Claude Code: {DOCS}",
            )
        started = time.monotonic()
        try:
            version, answer = check_access(Path(found), self._execute, TEST_PROMPT, TEST_TIMEOUT.total_seconds())
        except SubscriptionDeniedError as denied:
            logger.warning("Claude Code access test refused: %s", denied.reason.value)
            fix = f"Install Claude Code: {DOCS}" if denied.reason is Denial.UNAVAILABLE else ""
            return AccessTest(ok=False, detail=str(denied), fix=fix, duration_seconds=time.monotonic() - started)
        except HeinzelError as error:  # the usage limit: plain words, never the CLI's body
            return AccessTest(ok=False, detail=str(error), duration_seconds=time.monotonic() - started)
        return AccessTest(
            ok=True,
            detail=f"{answer.strip()[:ANSWER_LIMIT]} (Claude Code {version})",
            duration_seconds=time.monotonic() - started,
        )
