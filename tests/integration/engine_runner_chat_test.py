"""The runner chat on hz's own engine with a plugin's models, end to end in an isolated profile (ADR 0159).

A made-up plugin brings streaming `FunctionModel`s, so nothing leaves the machine. The chat's MCP server is the real
`hz system serve mcp --chat <session>`, started by the engine runner over stdio.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCalls, FunctionModel
from typer.testing import CliRunner

from heinzel import Config, Kernel, ModelOffer, Module
from heinzel.heads.cli import build
from heinzel.modules.ai.access._engine_runner import ENGINE, EngineRunner
from tests.integration.chat_test import AUTH, stream_events, web_client

pytestmark = pytest.mark.integration

SEEN: list[tuple[str, list[str], list[str]]] = []


def streaming_models() -> Module:
    module = Module("stream-models", "Made-up streaming models through a made-up company proxy.")

    @module.provides("ai-models", modes={"read"}, hosts=["ai-proxy.example.invalid"], name="stream-models")
    class StreamModels:
        def models(self) -> list[ModelOffer]:
            return [
                ModelOffer(id="fast", context_window=200_000),
                ModelOffer(id="deep", thinking="adaptive", efforts=["low", "high"]),
                ModelOffer(id="retired", available=False),
            ]

        def model(self, model_id: str) -> FunctionModel:
            async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str | DeltaToolCalls]:
                asked = [
                    str(p.content) for m in messages for p in getattr(m, "parts", []) if isinstance(p, UserPromptPart)
                ]
                SEEN.append((model_id, asked, sorted(t.name for t in info.function_tools)))
                for word in ("Hello ", "from ", model_id):
                    yield word

            return FunctionModel(stream_function=stream, model_name=model_id)

    # Head A registers the engine runner in ai/access; this test module does the same for an isolated kernel.
    module.provides("agent-runner", modes={"read"}, name=ENGINE)(EngineRunner)
    return module


@pytest.fixture
def kernel(make_kernel: Any) -> Kernel:
    SEEN.clear()
    config = Config(files=(), data={"capabilities": {"ai_access": "stream-models", "agent_runner": ENGINE}})
    return make_kernel(builtins=True, streammodels=streaming_models(), config=config)


def hz(kernel: Kernel, *args: str) -> Any:
    return CliRunner().invoke(build(kernel), list(args))


def test_a_chat_on_a_plugins_model_answers_and_continues_its_session(kernel: Kernel) -> None:
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]

    first = hz(kernel, "chat", "send", session, "hi", "--json")
    second = hz(kernel, "chat", "send", session, "and again", "--model", "deep", "--effort", "high", "--json")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    shown = json.loads(hz(kernel, "chat", "show", session, "--json").output)
    answers = [m for m in shown["messages"] if m["role"] == "assistant"]
    assert [a["text"] for a in answers] == ["Hello from fast", "Hello from deep"]
    assert answers[1]["model"] == "deep"
    (model1, asked1, tools1), (model2, asked2, _tools2) = SEEN
    assert (model1, model2) == ("fast", "deep")
    assert asked1 == ["User: hi"]
    assert asked2 == ["User: hi", "User: and again"]  # the session's history, then only the new message
    assert tools1
    assert all(name.startswith("mcp__hz__") for name in tools1)  # hz's own chat server, nothing else


def test_an_unavailable_model_is_refused_naming_others(kernel: Kernel) -> None:
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]

    refused = hz(kernel, "chat", "send", session, "hi", "--model", "retired", "--json")

    answer = json.loads(refused.output)["messages"][-1]
    assert answer["status"] == "failed"
    assert "The model retired is unavailable" in answer["detail"]
    assert "fast" in answer["detail"]
    assert not SEEN


def test_the_web_head_streams_the_engine_s_answer(kernel: Kernel, tmp_path: Path) -> None:
    client = web_client(kernel, tmp_path)
    opened = client.post("/api/system/runner/chat/open", headers=AUTH).json()
    with client.stream(
        "POST", "/api/stream/system/runner/chat/send", params={"session": opened["id"], "message": "hi"}, headers=AUTH
    ) as response:
        body = "".join(response.iter_text())

    events = stream_events(body)
    assert [kind for kind, _ in events] == ["text", "text", "text", "result"]
    assert "".join(data["text"] for kind, data in events if kind == "text") == "Hello from fast"
