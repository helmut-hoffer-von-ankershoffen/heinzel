"""The engine runner runs a read-only coding session on hz's own engine with the selected option's model (ADR 0159).

No network and no model: `FunctionModel` plays the model and calls the tools; a tiny Python script plays the MCP
server over stdio.
"""

import asyncio
import inspect
import json
import stat
import sys
import textwrap
import time
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.models.test import TestModel

from heinzel import Budget, Config, Credentials, CurrentUser, HeinzelError, McpServer, Progress, Settings, ToolProfile
from heinzel.kernel import AiModelsSource, EnvironmentBackend, ModelOffer, _ai_models, _claude_code
from heinzel.kernel._ai_models import OUTPUT_TOKENS
from heinzel.modules.ai.access._engine_history import SessionHistory
from heinzel.modules.ai.access._engine_runner import ENGINE, EngineModel, EngineRunner, SelectedModels
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fresh_model_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test reads the plugins' lists afresh: hz keeps a list per adapter for a few seconds."""
    monkeypatch.setattr(_ai_models, "_listings", {})


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


def streamed(respond: Callable[[list[ModelMessage], AgentInfo], Any]) -> FunctionModel:
    """A streaming model from a function returning whole responses: its text in two chunks, its tool calls whole."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
        response = respond(messages, info)
        if inspect.isawaitable(response):
            response = await response
        for part in response.parts:
            if isinstance(part, TextPart):
                half = len(part.content) // 2
                yield part.content[:half]
                yield part.content[half:]
        calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        if calls:
            yield {
                i: DeltaToolCall(name=c.tool_name, json_args=c.args_as_json_str(), tool_call_id=c.tool_call_id)
                for i, c in enumerate(calls)
            }

    return FunctionModel(stream_function=stream)


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

    return streamed(respond)


HISTORY: list[SessionHistory] = []


@pytest.fixture(autouse=True)
def history(tmp_path: Path) -> SessionHistory:
    """Each test keeps its chat histories in its own folder."""
    (tmp_path / "chats" / "chat-1").mkdir(parents=True)  # the chat's own folder, which the chat creates
    HISTORY[:] = [SessionHistory(tmp_path / "chats")]
    return HISTORY[0]


def runner(model: Any, *, progress: Progress | None = None, **chosen: Any) -> EngineRunner:
    return EngineRunner(
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        progress or Progress(),
        models=lambda _m, _e: EngineModel(model, "example-model", **chosen),
        history=HISTORY[0],
    )


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

    result = runner(streamed(respond)).run(
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

    result = runner(streamed(respond), settings={"anthropic_effort": "high"}, warnings=["a note"]).run(
        "x", contract="", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget(max_usd=2)
    )

    assert result.warnings == ["a note", "the model reports no dollar cost, so the cap of 2 USD is not enforced"]
    assert settings[0]["anthropic_effort"] == "high"


def test_a_slow_or_failing_model_is_a_result(work: Path) -> None:
    async def slow(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        await asyncio.sleep(5)
        return ModelResponse([TextPart("late")])

    def broken(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        msg = "the proxy said no, token sk-secret"
        raise RuntimeError(msg)

    timed = runner(streamed(slow)).run(
        "x", contract="", tools=ToolProfile(working_dir=work), timeout=timedelta(seconds=0.2), budget=Budget()
    )
    failed = runner(streamed(broken)).run(
        "x", contract="", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget()
    )

    assert (timed.status, timed.draft) == ("timeout", "")
    assert failed.status == "failed"
    assert failed.detail == "The plugin's model failed (RuntimeError)."  # never the error's own text


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
    result = runner(streamed(respond)).run("x", contract="", tools=tools, timeout=MINUTE, budget=Budget())

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
        {"max_tokens": OUTPUT_TOKENS, "anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "high"},
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


# Streaming and the chat session's history


class Recorded(Progress):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emitter(self) -> Any:
        return lambda kind, data: self.events.append((kind, dict(data)))


def test_text_tool_calls_and_results_stream_as_they_happen(work: Path) -> None:
    progress = Recorded()
    model = scripted([("glob", {"pattern": "*.py"})], "all done")

    result = runner(model, progress=progress).run(
        "x", contract="", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget()
    )

    assert result.draft == "all done"
    assert progress.events == [
        ("tool", {"name": "glob"}),
        ("tool_result", {"name": "glob"}),
        ("text", {"text": "all "}),
        ("text", {"text": "done"}),
    ]
    assert result.usage.output_tokens is not None  # from the streamed responses


def chat_tools(work: Path, tmp_path: Path, session: str = "chat-1") -> ToolProfile:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    server = McpServer(command=[sys.executable, str(script), "--chat", session])
    return ToolProfile(working_dir=work, read_files=False, mcp_servers={"heinzel": server})


def prompts(messages: list[ModelMessage]) -> list[str]:
    return [str(p.content) for m in messages for p in getattr(m, "parts", []) if isinstance(p, UserPromptPart)]


def test_a_chat_session_continues_its_message_history(work: Path, tmp_path: Path, history: SessionHistory) -> None:
    seen: list[list[ModelMessage]] = []
    answers = iter(["Hi there, how can I help?", "Fine, thanks.", "Starting over."])

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return ModelResponse([TextPart(next(answers))])

    tools = chat_tools(work, tmp_path)
    first = "User: hello"
    second = f"{first}\n\nAssistant: Hi there, how can I help?\n\nUser: how are you?"
    edited = "User: hello\n\nAssistant: something else\n\nUser: and now?"
    for prompt in (first, second, edited):
        result = runner(streamed(respond)).run(prompt, contract="", tools=tools, timeout=MINUTE, budget=Budget())
        assert result.status == "completed", result.detail

    assert prompts(seen[0]) == [first]
    assert prompts(seen[1]) == [first, "User: how are you?"]  # only what is new, after the stored history
    assert [type(m).__name__ for m in seen[1]] == ["ModelRequest", "ModelResponse", "ModelRequest"]
    assert prompts(seen[2]) == [edited]  # the chat no longer matches the history: start again
    kept, notes = history.load("chat-1")
    assert prompts(kept) == [edited]
    assert notes == {"answer_start": "Starting over."}
    folder = tmp_path / "chats" / "chat-1"
    assert stat.S_IMODE((folder / "engine-history.json").stat().st_mode) == 0o600
    assert [p.name for p in folder.iterdir()] == ["engine-history.json"]  # no temporary file left


def test_a_run_without_a_chat_keeps_no_history(work: Path, tmp_path: Path) -> None:
    runner(scripted("ok")).run("x", contract="", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget())

    assert not any((tmp_path / "chats").rglob("*.json"))


def test_a_deleted_chat_keeps_no_history_and_the_answer_stands(work: Path, tmp_path: Path) -> None:
    (tmp_path / "chats" / "chat-1").rmdir()  # the chat was deleted while its turn ran

    result = runner(scripted("ok")).run(
        "User: hi", contract="", tools=chat_tools(work, tmp_path), timeout=MINUTE, budget=Budget()
    )

    assert (result.status, result.draft) == ("completed", "ok")
    assert result.warnings == ["the chat's history wasn't kept (FileNotFoundError), so the next message starts afresh"]
    assert not (tmp_path / "chats" / "chat-1").exists()


def test_a_stopped_turn_keeps_its_partial_answer_marked_stopped(
    work: Path, tmp_path: Path, history: SessionHistory
) -> None:
    async def stream(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        yield "The answer is"
        await asyncio.sleep(5)
        yield " never finished"

    progress = Recorded()
    result = runner(FunctionModel(stream_function=stream), progress=progress).run(
        "User: tell me", contract="", tools=chat_tools(work, tmp_path), timeout=timedelta(seconds=1), budget=Budget()
    )

    assert result.status == "timeout"
    assert progress.events == [("text", {"text": "The answer is"})]
    kept, _notes = history.load("chat-1")
    last = kept[-1]
    assert isinstance(last, ModelResponse)
    assert [p.content for p in last.parts] == [  # type: ignore[union-attr]
        "The answer is",
        "[stopped: the run's timeout of 0:00:01]",
    ]
    seen: list[list[ModelMessage]] = []

    def respond(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return ModelResponse([TextPart("Here it is.")])

    again = "User: tell me\n\nAssistant: (no answer: stopped after 0:00:01)\n\nUser: again"
    runner(streamed(respond)).run(again, contract="", tools=chat_tools(work, tmp_path), timeout=MINUTE, budget=Budget())
    assert prompts(seen[0]) == ["User: tell me", "User: again"]  # the stopped turn is part of the session


def test_the_toolset_is_exactly_the_read_only_tools_and_the_mcp_servers(work: Path, tmp_path: Path) -> None:
    seen: list[AgentInfo] = []
    tools = chat_tools(work, tmp_path)
    tools = tools.model_copy(update={"read_files": True})

    runner(scripted("ok", seen=seen)).run("x", contract="", tools=tools, timeout=MINUTE, budget=Budget())

    names = {t.name for t in seen[0].function_tools}
    assert names - {n for n in names if n.startswith("mcp__")} == {"read", "grep", "glob"}
    assert names == {"read", "grep", "glob", "mcp__heinzel__chat_search"}


def test_a_model_chosen_regex_never_holds_the_run_past_its_timeout(work: Path) -> None:
    (work / "evil.txt").write_text("a" * 40 + "!\n", encoding="utf-8")
    model = scripted([("grep", {"pattern": "(a+)+$", "path": "evil.txt"})], "done")
    started = time.monotonic()

    result = runner(model).run(
        "go", contract="", tools=ToolProfile(working_dir=work), timeout=timedelta(seconds=2), budget=Budget()
    )

    assert result.status == "timeout"
    assert time.monotonic() - started < 8


def test_the_whole_disk_or_home_is_no_working_directory(tmp_path: Path) -> None:
    for wide in (Path("/"), Path.home()):
        with pytest.raises(HeinzelError, match="whole disk or your home"):
            runner(TestModel()).run(
                "x", contract="", tools=ToolProfile(working_dir=wide), timeout=MINUTE, budget=Budget()
            )
    runner(TestModel()).run(
        "x", contract="", tools=ToolProfile(working_dir=Path.home(), read_files=False), timeout=MINUTE, budget=Budget()
    )


def test_home_swapped_in_after_the_early_check_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The early check is by name; the folder the tools open is checked again by identity (ADR 0159, A3)."""
    from heinzel.modules.ai.access import _engine_runner

    alias = tmp_path / "project"
    alias.symlink_to(Path.home())

    def checked_by_name_only(_directory: Path) -> bool:
        return False  # as if the folder were swapped after the early check

    monkeypatch.setattr(_engine_runner, "_too_wide", checked_by_name_only)

    for wide in (Path("/"), Path.home(), alias):
        with pytest.raises(HeinzelError, match="whole disk or your home"):
            runner(TestModel()).run(
                "x", contract="", tools=ToolProfile(working_dir=wide), timeout=MINUTE, budget=Budget()
            )


def test_the_folder_is_let_go_when_the_run_fails_before_its_turn(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    from heinzel.modules.ai.access._file_tools import ConfinedFiles

    closed: list[int] = []
    real = ConfinedFiles.close

    def close(files: ConfinedFiles) -> None:
        closed.append(files.root_fd)
        real(files)

    def broken() -> Any:
        raise RuntimeError

    progress = Progress()
    monkeypatch.setattr(ConfinedFiles, "close", close)
    monkeypatch.setattr(progress, "emitter", broken)
    engine = runner(TestModel(), progress=progress)

    with pytest.raises(RuntimeError):
        engine.run("x", contract="", tools=ToolProfile(working_dir=work), timeout=MINUTE, budget=Budget())

    assert len(closed) == 1
    assert closed[0] >= 0
