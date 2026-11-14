"""The engine runner runs a read-only coding session on hz's own engine with the selected option's model (ADR 0159).

No network and no model: `FunctionModel` plays the model and calls the tools; a tiny Python script plays the MCP
server over stdio.
"""

import asyncio
import json
import sys
import textwrap
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from heinzel import Budget, Config, Credentials, CurrentUser, HeinzelError, McpServer, Settings, ToolProfile
from heinzel.kernel import AiModelsSource, EnvironmentBackend, ModelOffer, _claude_code
from heinzel.modules.ai.access._engine_runner import ENGINE, EngineModel, EngineRunner, SelectedModels
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess

pytestmark = pytest.mark.unit

MINUTE = timedelta(minutes=1)


@pytest.fixture(autouse=True)
def no_claude_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine path never asks for a Claude login or strips the environment for the Claude CLI."""

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError

    monkeypatch.setattr(_claude_code, "require_subscription_login", refuse)
    monkeypatch.setattr(_claude_code, "subscription_environment", refuse)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("top secret\n", encoding="utf-8")
    return root


def scripted(*steps: list[Any], seen: list[AgentInfo] | None = None) -> FunctionModel:
    """A model that makes one response per step: tool calls (name, args) or a final text."""
    queue = list(steps)

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info)
        step = queue.pop(0)
        if isinstance(step, str):
            return ModelResponse([TextPart(step)])
        return ModelResponse([ToolCallPart(name, args, tool_call_id=f"c{i}") for i, (name, args) in enumerate(step)])

    return FunctionModel(respond)


def runner(model: Any, **chosen: Any) -> EngineRunner:
    return EngineRunner(None, None, models=lambda _m, _e: EngineModel(model, "example-model", **chosen))  # type: ignore[arg-type]


def returns(messages: list[ModelMessage]) -> list[str]:
    return [str(p.content) for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]


def test_a_session_reads_greps_and_globs_inside_the_working_directory(work: Path) -> None:
    history: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        history.append(messages)
        if len(history) == 1:
            calls = [
                ("glob", {"pattern": "*.py"}),
                ("read", {"path": "src/app.py"}),
                ("grep", {"pattern": "return"}),
                ("read", {"path": "../secret.txt"}),
                ("read", {"path": str(work.parent / "secret.txt")}),
            ]
            return ModelResponse([ToolCallPart(n, a, tool_call_id=f"c{i}") for i, (n, a) in enumerate(calls)])
        return ModelResponse([TextPart("done")])

    result = runner(FunctionModel(respond)).run(
        "look", contract="Be brief.", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget()
    )

    assert (result.runner, result.status, result.draft, result.model) == (ENGINE, "completed", "done", "example-model")
    assert result.tool_calls == {"glob": 1, "read": 3, "grep": 1}
    assert result.billing == "api"
    got = returns(history[1])
    assert got[0] == "src/app.py"
    assert got[1] == "     1\tdef answer():\n     2\t    return 42"
    assert got[2] == "src/app.py:2:     return 42"
    assert got[3].startswith("Refused:")
    assert "'..'" in got[3]
    assert got[4] == f"Refused: {work.parent / 'secret.txt'} is outside the working directory"
    assert "top secret" not in json.dumps(got)


def test_the_profile_decides_which_tools_the_model_sees(work: Path) -> None:
    seen: list[AgentInfo] = []

    runner(scripted("ok", seen=seen)).run(
        "x", contract="", tools=ToolProfile(working_dir=work, tools=["Read"]), timeout=MINUTE, budget=Budget()
    )
    runner(scripted("ok", seen=seen)).run(
        "x", contract="", tools=ToolProfile(working_dir=work, read_files=False), timeout=MINUTE, budget=Budget()
    )

    assert [t.name for t in seen[0].function_tools] == ["read"]
    assert seen[1].function_tools == []


def test_a_tool_that_isn_t_read_only_or_a_missing_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(HeinzelError, match="Bash"):
        runner(TestModel()).run("x", contract="", tools=ToolProfile(tools=["Bash"]), timeout=MINUTE, budget=Budget())
    with pytest.raises(HeinzelError, match="does not exist"):
        runner(TestModel()).run(
            "x", contract="", tools=ToolProfile(working_dir=tmp_path / "gone"), timeout=MINUTE, budget=Budget()
        )


def test_limits_it_can_t_enforce_are_warnings_and_effort_reaches_the_model(work: Path) -> None:
    settings: list[Any] = []

    def respond(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        settings.append(info.model_settings)
        return ModelResponse([TextPart("ok")])

    result = runner(FunctionModel(respond), settings={"anthropic_effort": "high"}, warnings=["a note"]).run(
        "x", contract="", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget(max_usd=2)
    )

    assert result.warnings == ["a note", "the model reports no dollar cost, so the cap of 2 USD is not enforced"]
    assert settings[0]["anthropic_effort"] == "high"


def test_a_slow_or_failing_model_is_a_result(work: Path) -> None:
    async def slow(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(5)
        return ModelResponse([TextPart("late")])

    def broken(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        msg = "the proxy said no"
        raise RuntimeError(msg)

    timed = runner(FunctionModel(slow)).run(
        "x", contract="", tools=ToolProfile(working_dir=work), timeout=timedelta(seconds=0.2), budget=Budget()
    )
    failed = runner(FunctionModel(broken)).run(
        "x", contract="", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget()
    )

    assert (timed.status, timed.draft) == ("timeout", "")
    assert (failed.status, failed.detail) == ("failed", "RuntimeError: the proxy said no")


SERVER = textwrap.dedent(
    """
    import json, sys
    def send(m):
        sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("method") == "initialize":
            print("a log line on stdout", flush=True)
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        elif m.get("method") == "tools/list":
            tool = {"name": "chat.search", "description": "Search", "inputSchema": {
                "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"tools": [tool]}})
        elif m.get("method") == "tools/call":
            send({"jsonrpc": "2.0", "id": 99, "method": "sampling/createMessage", "params": {}})
            reply = json.loads(sys.stdin.readline())
            assert reply["error"]["code"] == -32601
            q = m["params"]["arguments"]["q"]
            send({"jsonrpc": "2.0", "id": m["id"], "result": {"content": [{"type": "text", "text": "found " + q}]}})
    """
)


def test_mcp_servers_are_tools_named_as_claude_code_names_them(work: Path, tmp_path: Path) -> None:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    history: list[list[ModelMessage]] = []
    seen: list[AgentInfo] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        history.append(messages)
        seen.append(info)
        if len(history) == 1:
            return ModelResponse([ToolCallPart("mcp__chat__chat_search", {"q": "drafts"}, tool_call_id="c1")])
        return ModelResponse([TextPart("done")])

    tools = ToolProfile(
        working_dir=work, read_files=False, mcp_servers={"chat": McpServer(command=[sys.executable, str(script)])}
    )
    result = runner(FunctionModel(respond)).run("x", contract="", tools=tools, timeout=MINUTE, budget=Budget())

    assert result.status == "completed", result.detail
    assert [t.name for t in seen[0].function_tools] == ["mcp__chat__chat_search"]
    assert returns(history[1]) == ["found drafts"]
    assert result.tool_calls == {"mcp__chat__chat_search": 1}


def test_an_mcp_server_that_can_t_start_fails_the_run(work: Path) -> None:
    tools = ToolProfile(working_dir=work, mcp_servers={"chat": McpServer(command=[str(work / "no-such-server")])})

    result = runner(scripted("ok")).run("x", contract="", tools=tools, timeout=MINUTE, budget=Budget())

    assert result.status == "failed"
    assert "couldn't start" in result.detail


# Which model the session runs on


class Models:
    def models(self) -> list[ModelOffer]:
        return [ModelOffer(id="acme-large", thinking="adaptive", efforts=["low", "high"]), ModelOffer(id="acme-small")]

    def model(self, model_id: str) -> Any:
        return TestModel(custom_output_text=model_id)


class FakeKernel:
    def __init__(self, access: PydanticAiAccess | None = None) -> None:
        self.access = access

    def offers_ai_models(self, name: str | None) -> bool:
        return name == "acme"

    def ai_models(self, name: str) -> AiModelsSource:
        return AiModelsSource(name, "acme.models", "heinzel-acme", "1.0.0", Models())

    def resolve_builtin_service(self, kind: type, owner: str) -> Any:
        assert (kind, owner) == (PydanticAiAccess, "ai.access")
        return self.access


def config(access: str, **settings: Any) -> Config:
    return Config(files=(), data={"capabilities": {"ai_access": access}, "ai": {"access": settings}})


def test_a_plugin_s_chosen_model_and_effort(tmp_path: Path) -> None:
    current = CurrentUser(tmp_path / "identity")
    models = SelectedModels(FakeKernel(), current, lambda: config("acme", plugin_effort="high"))  # type: ignore[arg-type]

    chosen = models(None, None)
    assert (chosen.label, chosen.settings) == (
        "acme-large",
        {"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "high"},
    )
    assert models("acme-small", "low").label == "acme-small"
    assert models(None, "low").settings["anthropic_effort"] == "low"


def test_the_model_api_option_s_configured_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
    current = CurrentUser(tmp_path / "identity")
    settings = AccessSettings(model="claude-example-1", base_url="https://ai-proxy.example.com", api="anthropic")
    built: list[Any] = []
    option = PydanticAiAccess(
        settings,
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=lambda *args, **kwargs: built.append((args, kwargs)) or TestModel(),
        installed=lambda _package: True,
        binding=lambda: "pydantic-ai",
        current_settings=lambda: settings,
    )
    models = SelectedModels(FakeKernel(option), current, lambda: config("pydantic-ai"))  # type: ignore[arg-type]
    with current.local("cli"):
        chosen = models("other", "high")
    assert chosen.label == "claude-example-1"
    assert chosen.warnings == [
        "the Model API option runs its configured model claude-example-1, not other",
        "the Model API option takes no effort, so the run used the model's default",
    ]
    assert built[0][1] == {"api": "anthropic"}


def test_an_option_with_an_agent_of_its_own_is_refused(tmp_path: Path) -> None:
    models = SelectedModels(FakeKernel(), CurrentUser(tmp_path / "identity"), lambda: config("claude-code"))  # type: ignore[arg-type]
    with pytest.raises(HeinzelError, match="selected AI access is claude-code"):
        models(None, None)
