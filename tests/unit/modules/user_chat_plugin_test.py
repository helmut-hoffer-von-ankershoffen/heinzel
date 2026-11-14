"""Chat, drafts and schedules on a plugin's own models, run by hz's engine (ADR 0159).

The plugin's model is a `FunctionModel`, or a real `AnthropicModel` whose HTTP client takes its bearer token from a
command and talks to a fake transport: what a company proxy speaking Anthropic's Messages API looks like.
"""

import asyncio
import json
from dataclasses import dataclass, field

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from heinzel import CurrentUser, HeinzelError, ModelOffer, ModelUnavailableError, UsageEntry
from heinzel.kernel import AiModelsSource
from heinzel.kernel._ai_models import PluginModelChoice
from heinzel.kernel._bindings import CapabilitySettings
from heinzel.modules.heinzel._autonomous_schedule import model_binding_fingerprint
from heinzel.modules.heinzel._models import HeinzelSettings
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit

REPLY = "Hello from the plugin's model"


def answer(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """The final answer, through the output tool the engine asks for."""
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"report": "Answered", "summary": REPLY})])


@dataclass
class Plugin:
    """A plugin's `ai-models` adapter: three models, one of which didn't answer when last probed."""

    model_factory: object = None
    built: list[str] = field(default_factory=list)

    def models(self) -> list[ModelOffer]:
        return [
            ModelOffer(id="fast", thinking="adaptive"),
            ModelOffer(id="slow", thinking="budget"),
            ModelOffer(id="down", available=False),
        ]

    def model(self, model_id: str) -> Model:
        self.built.append(model_id)
        factory = self.model_factory
        return factory(model_id) if callable(factory) else FunctionModel(answer, model_name=model_id)


@dataclass
class Models:
    """The kernel's view of installed `ai-models` adapters, for the chat service."""

    plugin: Plugin
    version: str = "1.0.0"

    def offers(self, access: str) -> bool:
        return access == "acme"

    def source(self, access: str) -> AiModelsSource:
        return AiModelsSource(
            name=access, module="acme", distribution="heinzel-acme", version=self.version, adapter=self.plugin
        )


@dataclass
class Settings:
    """What `[capabilities]`, `[heinzel]` and `[ai.access]` say for the test."""

    access: str = "acme"
    model: str | None = None
    effort: str | None = None
    mode: str = "plan-unknown"

    def settings(self, cls, _section):
        if cls is CapabilitySettings:
            return CapabilitySettings(ai_access=self.access, agent_runner="claude-code")
        if cls is HeinzelSettings:
            return HeinzelSettings(subscription_budget_mode=self.mode)
        if cls is PluginModelChoice:
            return PluginModelChoice(plugin_model=self.model, plugin_effort=self.effort)
        msg = f"Unexpected settings {cls.__name__}"
        raise AssertionError(msg)


def service(  # noqa: PLR0913 - every seam of the chat service
    crud, current, plugin: Plugin, settings: Settings, usage: list[UsageEntry], *, models: Models | None = None
):
    return UserChatService(
        crud,
        current,
        RunnerSnapshotService(),
        load=lambda: settings,  # type: ignore[arg-type, return-value]
        models=models or Models(plugin),
        usage=usage.append,
    )


def test_a_chat_turn_runs_on_the_plugins_model_without_claude_code(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin, usage = Plugin(), list[UsageEntry]()
        chat = service(crud, current, plugin, Settings(), usage)
        replied = asyncio.run(chat.answer(session, "hello"))
        unknown = crud.snapshot(session).costs.unknown_turns
    assert replied.message.text == REPLY
    binding = replied.message.backend_binding
    assert (binding.backend, binding.mode, binding.model) == ("acme", "api", "fast")
    assert replied.message.billing == "api"
    assert replied.message.cost_usd is None
    assert unknown == 1
    assert plugin.built == ["fast"]
    assert [(u.cause, u.access, u.model, u.cost_usd) for u in usage] == [("chat", "acme", "fast", None)]
    assert usage[0].input_tokens > 0


def test_the_chosen_model_and_effort_reach_the_plugin_model(tmp_path):
    seen: list[dict[str, object]] = []

    def factory(model_id: str) -> Model:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(dict(info.model_settings or {}))
            return answer(messages, info)

        return FunctionModel(respond, model_name=model_id)

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin = Plugin(model_factory=factory)
        chat = service(crud, current, plugin, Settings(model="slow", effort="high"), [])
        asyncio.run(chat.answer(session, "hello"))
    assert plugin.built == ["slow"]
    assert seen[0]["anthropic_thinking"] == {"type": "enabled", "budget_tokens": 16384}
    assert seen[0]["max_tokens"] > 16384


def test_an_unavailable_model_is_refused_before_the_turn_and_names_others(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin = Plugin()
        chat = service(crud, current, plugin, Settings(model="down"), [])
        with pytest.raises(ModelUnavailableError, match=r"ai\.access\.plugin_model fast") as refused:
            asyncio.run(chat.answer(session, "hello"))
        assert not any(e.kind == "interactive.started" for e in crud.repository.control_read(session).events)
    assert refused.value.others == ["fast", "slow"]
    assert plugin.built == []


def test_dollar_caps_refuse_a_plugin_model_before_it_is_built(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin = Plugin()
        chat = service(crud, current, plugin, Settings(mode="usd-cap"), [])
        with pytest.raises(HeinzelError, match="cannot enforce the configured USD caps"):
            asyncio.run(chat.answer(session, "hello"))
    assert plugin.built == []


def test_changing_the_model_mid_turn_stops_it_before_the_next_request(tmp_path):
    settings = Settings()

    def factory(model_id: str) -> Model:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            settings.model = "slow"  # the person picks another model while the turn runs
            return ModelResponse(parts=[ToolCallPart("unknown_tool", {})])

        return FunctionModel(respond, model_name=model_id)

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, Plugin(model_factory=factory), settings, [])
        with pytest.raises(HeinzelError, match="changed during this turn"):
            asyncio.run(chat.answer(session, "hello"))


def test_the_schedule_fingerprint_follows_plugin_version_and_model(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, _session = setup_chat(current)
        plugin, settings = Plugin(), Settings()
        models = Models(plugin)
        chat = service(crud, current, plugin, settings, [], models=models)
        first = model_binding_fingerprint(chat)
        assert model_binding_fingerprint(chat) == first
        settings.model = "slow"
        assert model_binding_fingerprint(chat) != first
        settings.model = None
        models.version = "1.0.1"
        assert model_binding_fingerprint(chat) != first
    assert chat.binding_terms()["distribution"] == "heinzel-acme"


def test_claude_code_stays_selected_when_the_access_is_not_a_plugins_models(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, _session = setup_chat(current)
        chat = service(crud, current, Plugin(), Settings(access="claude-code"), [])
        assert chat.plugin_selected() is None


class PluginAuth(httpx2.Auth):
    """How a plugin might sign its requests: its own business, here a fixed bearer token."""

    def auth_flow(self, request: httpx2.Request):
        request.headers.pop("x-api-key", None)
        request.headers["authorization"] = "Bearer plugin-token"
        yield request


def anthropic_reply(body: dict[str, object]) -> dict[str, object]:
    """Anthropic's Messages API answer: the final answer through the output tool, or as JSON text when thinking is on
    (Pydantic AI can't force a tool then, so it asks for the output in the text)."""
    tools = [t["name"] for t in body.get("tools", [])]  # type: ignore[attr-defined, index]
    answer = {"report": "ok", "summary": REPLY}
    content = (
        [{"type": "tool_use", "id": "t1", "name": "final_result", "input": answer}]
        if "final_result" in tools
        else [{"type": "text", "text": json.dumps(answer)}]
    )
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": content,
        "stop_reason": "tool_use" if "final_result" in tools else "end_turn",
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def sse(body: dict[str, object]) -> httpx2.Response:
    """The same answer as Anthropic's server-sent events, in several deltas, for a streamed request."""
    reply = anthropic_reply(body)
    [block] = reply["content"]  # type: ignore[misc]
    usage = reply["usage"]
    events: list[tuple[str, dict[str, object]]] = [
        ("message_start", {"type": "message_start", "message": {**reply, "content": [], "stop_reason": None}}),
    ]
    if block["type"] == "text":
        start = {"type": "text", "text": ""}
        text = block["text"]
        deltas = [{"type": "text_delta", "text": text[i : i + 7]} for i in range(0, len(text), 7)]
    else:
        start = {**block, "input": {}}
        raw = json.dumps(block["input"])
        deltas = [{"type": "input_json_delta", "partial_json": raw[i : i + 7]} for i in range(0, len(raw), 7)]
    events.append(("content_block_start", {"type": "content_block_start", "index": 0, "content_block": start}))
    events += [("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": d}) for d in deltas]
    events.append(("content_block_stop", {"type": "content_block_stop", "index": 0}))
    delta = {"stop_reason": reply["stop_reason"], "stop_sequence": None}
    events.append(("message_delta", {"type": "message_delta", "delta": delta, "usage": usage}))
    events.append(("message_stop", {"type": "message_stop"}))
    text = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=text.encode())


def answer_from(body: dict[str, object]) -> httpx2.Response:
    """A streamed answer when the request asks for one (Anthropic's client does for a large `max_tokens`)."""
    return sse(body) if body.get("stream") else httpx2.Response(200, json=anthropic_reply(body))


def anthropic_model(proxy):
    """What a plugin's `model()` returns: a stock AnthropicModel whose client signs the plugin's own way."""

    def factory(model_id: str) -> Model:
        client = httpx2.AsyncClient(auth=PluginAuth(), transport=httpx2.MockTransport(proxy))
        provider = AnthropicProvider(api_key="unused", base_url="https://ai-proxy.example.invalid", http_client=client)
        return AnthropicModel(model_id, provider=provider)

    return factory


def test_an_anthropic_proxy_answers_a_chat_turn_with_the_effort_and_output_cap(tmp_path, monkeypatch):
    """A company proxy speaking Anthropic's Messages API, signed the plugin's own way."""
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", True)  # the transport is fake, nothing leaves
    seen: list[tuple[str, dict[str, object], dict[str, str]]] = []

    def proxy(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, body, dict(request.headers)))
        return answer_from(body)

    current = CurrentUser(tmp_path / "identity")
    usage: list[UsageEntry] = []
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin = Plugin(model_factory=anthropic_model(proxy))
        chat = service(crud, current, plugin, Settings(model="slow", effort="max"), usage)
        replied = asyncio.run(chat.answer(session, "hello"))
    assert replied.message.text == REPLY
    [(path, body, headers)] = seen
    assert path == "/v1/messages"
    assert headers["authorization"] == "Bearer plugin-token"
    assert "x-api-key" not in headers
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 63999}
    assert body["max_tokens"] > 63999
    assert [(u.input_tokens, u.output_tokens, u.cost_usd, u.model) for u in usage] == [(120, 30, None, "slow")]


@pytest.mark.parametrize(
    ("status", "said"),
    [
        (401, "The provider refused the request (401): check the plugin's sign-in."),
        (429, "The provider is busy or rate-limited (429); try again shortly."),
        (500, "The provider failed the request (500); try again later."),
    ],
)
def test_a_provider_failure_is_a_clean_error_and_the_message_can_be_sent_again(tmp_path, monkeypatch, status, said):
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", True)
    secret = "Bearer leaked-token in the proxy's error body"

    def proxy(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"type": "error", "error": {"type": "x", "message": secret}})

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, Plugin(model_factory=anthropic_model(proxy)), Settings(), [])
        with pytest.raises(HeinzelError) as failed:
            asyncio.run(chat.answer(session, "hello"))
        state = chat.turn_state(session, "hello").status
    assert str(failed.value) == said
    assert "leaked" not in str(failed.value)
    assert state == "abandoned", "nothing was answered and no tool ran: set aside, so the person can send it again"


def test_an_unknown_model_at_the_provider_names_it_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", True)

    def proxy(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": "no"}})

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, Plugin(model_factory=anthropic_model(proxy)), Settings(), [])
        with pytest.raises(ModelUnavailableError, match="the provider doesn't know it"):
            asyncio.run(chat.answer(session, "hello"))
