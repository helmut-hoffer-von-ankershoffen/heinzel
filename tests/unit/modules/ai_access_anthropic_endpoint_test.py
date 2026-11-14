"""The `pydantic-ai` option at an Anthropic-compatible endpoint, on a fake transport (ADR 0159).

No network: every request goes to an `httpx2.MockTransport`, which checks what a company proxy would see.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModelSettings
from pydantic_ai.models.test import TestModel

from heinzel import Config, Credentials, CurrentUser, HeinzelError, Settings
from heinzel.kernel import EnvironmentBackend
from heinzel.modules.ai.access import _pydantic
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess, build_model, model_hosts, vendor_of

pytestmark = pytest.mark.unit

BASE = "https://ai-proxy.example.com"


def message(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-example-1",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 3},
    }


class Proxy:
    """A fake Anthropic Messages proxy: Bearer only, no `/v1/models`, records what it was sent."""

    def __init__(self, replies: list[httpx2.Response]) -> None:
        self.replies = replies
        self.requests: list[httpx2.Request] = []
        self.tokens: list[str | None] = []
        self.path = "/v1/messages"

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.tokens.append(request.headers.get("authorization"))  # the flow reuses the request object on a retry
        if request.url.path != self.path:
            return httpx2.Response(404, json={"type": "error", "error": {"type": "not_found_error", "message": "no"}})
        return self.replies.pop(0)

    def body(self, n: int = 0) -> dict[str, Any]:
        sent: dict[str, Any] = json.loads(self.requests[n].content)
        return sent


@pytest.fixture
def proxy(monkeypatch: pytest.MonkeyPatch) -> Proxy:
    """Route every client the option builds to the fake proxy."""
    fake = Proxy([])
    real = httpx2.AsyncClient

    class Routed(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**(kwargs | {"transport": httpx2.MockTransport(fake)}))

    monkeypatch.setattr(httpx2, "AsyncClient", Routed)
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", True)  # safe: the transport is the fake above
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "ambient-token-must-not-leak")
    return fake


def run(model: Any, prompt: str = "Are you there?", **kwargs: Any) -> Any:
    return asyncio.run(Agent(model, **kwargs).run(prompt))


def test_the_key_goes_as_bearer_to_v1_messages_and_nothing_ambient(proxy: Proxy) -> None:
    proxy.replies.append(httpx2.Response(200, json=message([{"type": "text", "text": "OK"}])))
    model = build_model("anthropic:claude-example-1", "sk-proxy", BASE, api="anthropic")

    result = run(model)

    assert result.output == "OK"
    sent = proxy.requests[0]
    assert (sent.url.host, sent.url.path) == ("ai-proxy.example.com", "/v1/messages")
    assert sent.headers["authorization"] == "Bearer sk-proxy"
    assert "x-api-key" not in sent.headers
    assert "must-not-leak" not in str(sent.headers)
    assert proxy.body()["model"] == "claude-example-1"  # the vendor prefix is dropped
    assert (result.usage.input_tokens, result.usage.output_tokens) == (11, 3)


def test_a_local_endpoint_gets_its_own_client_and_the_placeholder_key(proxy: Proxy) -> None:
    proxy.replies.append(httpx2.Response(200, json=message([{"type": "text", "text": "OK"}])))

    run(build_model("claude-example-1", None, "http://localhost:8080", api="anthropic"))

    assert proxy.requests[0].url.host == "localhost"
    assert proxy.requests[0].headers["authorization"] == "Bearer none"
    assert "x-api-key" not in proxy.requests[0].headers


def test_tools_and_both_thinking_styles_pass_through(proxy: Proxy, tmp_path: Path) -> None:
    proxy.replies += [
        httpx2.Response(
            200, json=message([{"type": "tool_use", "id": "t1", "name": "lookup", "input": {"q": "x"}}], "tool_use")
        ),
        httpx2.Response(200, json=message([{"type": "text", "text": "found"}])),
        httpx2.Response(200, json=message([{"type": "text", "text": "OK"}])),
    ]
    model = build_model("claude-example-1", "sk-proxy", BASE, api="anthropic")
    agent = Agent(model)

    @agent.tool_plain
    def lookup(q: str) -> str:
        return f"result for {q}"

    adaptive = AnthropicModelSettings(anthropic_thinking={"type": "adaptive"}, anthropic_effort="high", max_tokens=4096)
    assert asyncio.run(agent.run("look it up", model_settings=adaptive)).output == "found"
    budget = AnthropicModelSettings(anthropic_thinking={"type": "enabled", "budget_tokens": 2048}, max_tokens=4096)
    asyncio.run(Agent(model).run("hi", model_settings=budget))

    first, second, third = proxy.body(0), proxy.body(1), proxy.body(2)
    assert [t["name"] for t in first["tools"]] == ["lookup"]
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"]["effort"] == "high"
    assert second["messages"][-1]["content"][0]["type"] == "tool_result"
    assert third["thinking"] == {"type": "enabled", "budget_tokens": 2048}


def sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def test_streaming_reads_server_sent_events(proxy: Proxy, tmp_path: Path) -> None:
    start = message([])
    start["usage"] = {"input_tokens": 7, "output_tokens": 0}
    body = sse(
        {"type": "message_start", "message": start},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi, "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "there"}},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    )
    proxy.replies.append(httpx2.Response(200, content=body, headers={"content-type": "text/event-stream"}))
    model = build_model("claude-example-1", "sk-proxy", BASE, api="anthropic")

    async def stream() -> tuple[list[str], Any]:
        async with Agent(model).run_stream("hi") as result:
            chunks = [chunk async for chunk in result.stream_text(delta=True)]
            return chunks, result.usage

    chunks, usage = asyncio.run(stream())
    assert "".join(chunks) == "Hi, there"
    assert proxy.body()["stream"] is True
    assert proxy.requests[0].headers["authorization"] == "Bearer sk-proxy"
    assert usage.output_tokens == 2


# The option


def access(settings: AccessSettings, *, build: Any = None, **seams: Any) -> PydanticAiAccess:
    return PydanticAiAccess(
        settings,
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=build or (lambda *_args, **_kwargs: TestModel(custom_output_text="OK")),
        installed=lambda _package: True,
        binding=lambda: "pydantic-ai",
        current_settings=lambda: settings,
        **seams,
    )


def proxied(**changes: Any) -> AccessSettings:
    values: dict[str, Any] = {
        "model": "claude-example-1",
        "base_url": BASE,
        "api": "anthropic",
    }
    return AccessSettings(**(values | changes))


def test_the_option_names_the_endpoint_kind_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")

    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError

    found = access(proxied(), build=never, get=never).status()

    assert found.installed is True
    assert found.detail == f"claude-example-1 at {BASE}; key from the credential ai.access/api-key"
    assert vendor_of(proxied()) == "anthropic-compatible"
    assert vendor_of(proxied(api="openai")) == "openai-compatible"
    assert model_hosts(proxied()) == ["ai-proxy.example.com"]


def test_a_key_goes_only_to_a_safe_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
    plain = access(proxied(base_url="http://ai-proxy.example.com")).status()
    assert (plain.signed_in, "only over https" in plain.detail) == (False, True)
    refused = access(proxied(base_url="http://ai-proxy.example.com")).test()
    assert (refused.ok, "only over https" in refused.detail) == (False, True)


def test_the_test_builds_the_anthropic_endpoint_and_lists_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def build(*args: Any, **kwargs: Any) -> TestModel:
        calls.append((args, kwargs))
        return TestModel(custom_output_text="OK")

    def listing(*_args: Any) -> bytes:
        raise AssertionError  # no `/v1/models` at such a proxy

    result = access(proxied(), build=build, get=listing).test()

    assert result.ok is True
    assert calls == [
        (
            ("claude-example-1", "sk-proxy", BASE),
            {"api": "anthropic"},
        )
    ]


def test_today_s_configurations_call_the_factory_exactly_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-stored")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def build(*args: Any, **kwargs: Any) -> TestModel:
        calls.append((args, kwargs))
        return TestModel(custom_output_text="OK")

    endpoint = AccessSettings(model="llama3.1:8b", base_url="http://localhost:11434/v1")
    vendor = AccessSettings(model="anthropic:claude-example-1")
    for settings in (endpoint, vendor):
        access(settings, build=build, get=lambda *_a: b'{"data": []}').test()

    assert calls == [
        (("llama3.1:8b", "sk-stored", "http://localhost:11434/v1"), {}),
        (("anthropic:claude-example-1", "sk-stored", None), {}),
    ]


def test_a_turn_at_the_proxy_needs_the_key_and_the_same_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
    current = CurrentUser(tmp_path / "state" / "identity")
    with current.local("cli"):
        turn_at_the_proxy(current, monkeypatch)


def turn_at_the_proxy(current: CurrentUser, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = proxied()
    built: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def build(*args: Any, **kwargs: Any) -> TestModel:
        built.append((args, kwargs))
        return TestModel()

    assert isinstance(access(settings, build=build).turn_model(current, "claude-example-1"), TestModel)
    assert built == [(("claude-example-1", "sk-proxy", BASE), {"api": "anthropic"})]

    changed = PydanticAiAccess(
        settings,
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=build,
        installed=lambda _package: True,
        binding=lambda: "pydantic-ai",
        current_settings=lambda: proxied(api="openai"),
    )
    with pytest.raises(HeinzelError, match="changed"):
        changed.turn_model(current, "claude-example-1")
    monkeypatch.delenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY")
    remote = access(proxied())
    with pytest.raises(HeinzelError, match="requires a configured credential"):
        remote.turn_model(current, "claude-example-1")


def test_no_own_client_is_made_for_a_remote_endpoint() -> None:
    assert _pydantic._local_client(BASE) is None


# Today's paths, unchanged


def test_the_factory_still_builds_openai_endpoints_and_vendor_models(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.models.openai import OpenAIChatModel

    monkeypatch.setenv("OPENAI_API_KEY", "sk-ambient")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ambient")
    endpoint = build_model("openai:llama3.1:8b", None, "http://localhost:11434/v1")
    assert isinstance(endpoint, OpenAIChatModel)
    assert endpoint.model_name == "llama3.1:8b"
    assert isinstance(build_model("anthropic:claude-example-1", "sk-given"), AnthropicModel)
    assert isinstance(build_model("anthropic:claude-example-1", None), AnthropicModel)  # the vendor's variable


def test_the_option_reads_the_selection_and_its_settings_afresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HZ_CAPABILITIES__AI_ACCESS", "pydantic-ai")
    monkeypatch.setenv("HZ_AI_ACCESS__MODEL", "claude-example-1")

    assert _pydantic.selected_access() == "pydantic-ai"
    assert _pydantic._current_model_settings().model == "claude-example-1"
    assert _pydantic._installed("json") is True
    assert _pydantic._installed("no_such_package_hz") is False
    assert _pydantic._installed("..") is False


def test_the_reserved_path_refuses_an_endpoint_and_another_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
    current = CurrentUser(tmp_path / "state" / "identity")
    option = access(proxied())
    with pytest.raises(HeinzelError, match="unavailable or changed"):
        option.check_reserved_configuration("claude-example-1")
    with current.local("cli"), pytest.raises(HeinzelError, match="not supported for reserved turns"):
        option.priced_turn_model(current, "claude-example-1")
    assert option.pinned("claude-example-1") is False
    assert option.endpoint_identity() == (BASE, "anthropic")
    assert access(AccessSettings(model="anthropic:claude-example-1")).endpoint_identity() == (
        "https://api.anthropic.com",
        "anthropic",
    )
    with pytest.raises(HeinzelError, match="unavailable or changed"):
        access(AccessSettings()).configured_model()


def test_a_turn_refuses_an_unsafe_endpoint_or_a_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current = CurrentUser(tmp_path / "state" / "identity")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with current.local("cli"):
        monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
        with pytest.raises(HeinzelError, match="unavailable"):
            access(proxied(base_url="http://ai.example.com")).turn_model(current, "claude-example-1")
        monkeypatch.delenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY")
        with pytest.raises(HeinzelError, match="unavailable"):
            access(AccessSettings(model="anthropic:claude-example-1")).turn_model(current, "anthropic:claude-example-1")
        assert isinstance(
            access(proxied(base_url="http://localhost:8080")).turn_model(current, "claude-example-1"), TestModel
        )
