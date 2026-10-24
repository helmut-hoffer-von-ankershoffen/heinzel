"""`hz system runner ask`: the web app's chat, one read-only, capped agent run per message (ADR 0039, 0029)."""

from datetime import timedelta
from pathlib import Path

import pytest

from heinzel import AgentResult, Budget, HeinzelError, ToolProfile
from heinzel.modules.system.runner import ASK_TIMEOUT, ask
from heinzel.modules.system.runner._models import RunnerSettings

pytestmark = pytest.mark.unit


class Recorder:
    """An agent runner that records what it was asked and answers at once."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget) -> AgentResult:
        self.calls.append(
            {"prompt": prompt, "contract": contract, "tools": tools, "timeout": timeout, "budget": budget}
        )
        assert tools.working_dir is not None
        assert list(Path(tools.working_dir).iterdir()) == []  # nothing on this machine to read
        return AgentResult(runner="fake", status="completed", draft="Run `hz system doctor`.", duration_seconds=0.1)


def test_asks_read_only_in_an_empty_directory_within_the_cap():
    runner = Recorder()
    result = ask(runner, RunnerSettings(), "User: How healthy is hz?")
    assert result.draft == "Run `hz system doctor`."
    [call] = runner.calls
    assert str(call["prompt"]).endswith("User: How healthy is hz?")
    assert call["contract"] == ""  # lean: its own short system prompt, the preamble is in the prompt
    assert call["tools"].tools == []  # type: ignore[attr-defined] - the runner's read-only default
    assert call["timeout"] == ASK_TIMEOUT
    assert call["budget"] == Budget(max_usd=0.25)


@pytest.mark.parametrize(("asked", "cap"), [(0.05, 0.05), (5.0, 0.25), (None, 0.25)])
def test_a_budget_can_only_lower_the_configured_cap(asked, cap):
    runner = Recorder()
    ask(runner, RunnerSettings(), "Hi", budget_usd=asked)
    assert runner.calls[0]["budget"] == Budget(max_usd=cap)


def test_a_blank_prompt_is_refused_before_anything_runs():
    runner = Recorder()
    with pytest.raises(HeinzelError, match="the prompt is empty"):
        ask(runner, RunnerSettings(), "   ")
    assert runner.calls == []
