"""The Head A review's probes of a plugin's models in chat, kept as tests (ADR 0159): bounded builds, a free event
loop, one listing thread, capped answers, effort on the wire, and a usable chat after a failure."""

import asyncio
import itertools
import json
import threading
import time

import httpx2
import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from heinzel import CurrentUser, HeinzelError, ModelOffer, UsageEntry
from heinzel.kernel import AiModelsSource
from heinzel.kernel.ai._ai_models import list_offers
from heinzel.testing import scaled
from tests.unit.modules.user_chat_plugin_test import REPLY, Plugin, Settings, answer, answer_from, service
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit


# --- R2: plugin.model() runs synchronously on the event loop with no timeout -------------------------------------
class SlowBuild(Plugin):
    def model(self, model_id: str) -> Model:
        time.sleep(scaled(3))  # a hung SSO prompt or a network call while building
        return super().model(model_id)


def test_probe_r2_a_hanging_model_build_is_bounded(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, SlowBuild(), Settings(), [])

        async def main() -> float:
            ticks: list[float] = []

            async def ticker() -> None:
                while True:
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(ticker())
            await asyncio.sleep(0.1)
            await chat.answer(session, "hello")
            task.cancel()
            return max(b - a for a, b in itertools.pairwise(ticks))

        gap = asyncio.run(main())
    assert gap < 1.0, f"the event loop was blocked {gap:.1f}s by the plugin's model() build"


# --- R3: the per-request continuity check calls models() synchronously on the event loop ------------------------
class SlowList(Plugin):
    calls: int = 0

    def models(self) -> list[ModelOffer]:
        self.calls += 1
        if self.calls > 1:
            time.sleep(scaled(1.5))  # the cache refresh is slow now and then
        return super().models()


def test_probe_r3_continuity_check_does_not_block_the_event_loop(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, SlowList(), Settings(), [])

        async def main() -> float:
            ticks: list[float] = []

            async def ticker() -> None:
                while True:
                    ticks.append(time.monotonic())
                    await asyncio.sleep(0.05)

            task = asyncio.create_task(ticker())
            await asyncio.sleep(0.1)
            await chat.answer(session, "hello")
            task.cancel()
            return max(b - a for a, b in itertools.pairwise(ticks))

        gap = asyncio.run(main())
    assert gap < 1.0, f"the event loop was blocked {gap:.1f}s by models() inside the request"


# --- R4: a hung models() leaks one thread per listing ----------------------------------------------------------------
class Hung:
    def __init__(self) -> None:
        self.release = threading.Event()

    def models(self) -> list[ModelOffer]:
        self.release.wait(scaled(30))
        return [ModelOffer(id="x")]

    def model(self, model_id: str) -> object:
        raise NotImplementedError


def test_probe_r4_a_hung_models_call_does_not_leak_a_thread_per_listing():
    hung = Hung()
    source = AiModelsSource(name="hung", module="m", distribution="d", version="1", adapter=hung)  # type: ignore[arg-type]
    before = threading.active_count()
    try:
        for _ in range(10):
            offers, note = list_offers(source, timeout=scaled(0.05))
            assert offers == []
            assert "took longer" in note
        grown = threading.active_count() - before
    finally:
        hung.release.set()
    assert grown <= 1, f"{grown} threads stuck in the plugin's models() after 10 listings"


# --- R5: no output cap on the plugin path (the Claude path caps at 128 KiB) ------------------------------------------
def test_probe_r5_a_huge_answer_is_capped(tmp_path):
    def huge(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {"report": "x", "summary": "A" * (4 * 1024 * 1024)})]
        )

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin = Plugin(model_factory=lambda mid: FunctionModel(huge, model_name=mid))
        chat = service(crud, current, plugin, Settings(), [])
        try:
            replied = asyncio.run(chat.answer(session, "hello"))
        except HeinzelError:
            return  # refusing it is fine too
    assert len(replied.message.text) <= 128 * 1024, f"stored a {len(replied.message.text)} character answer"


# --- R6: budget-style thinking on a real AnthropicModel: what reaches the wire --------------------------------------
def _anthropic_capture(bodies: list[dict]) -> object:
    def proxy(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        return answer_from(body)  # streamed when Anthropic's client asks for it (large max_tokens)

    def factory(model_id: str) -> Model:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(proxy))
        provider = AnthropicProvider(api_key="k", base_url="https://ai-proxy.example.invalid", http_client=client)
        return AnthropicModel(model_id, provider=provider)

    return factory


@pytest.mark.parametrize(("model", "effort"), [("slow", "high"), ("slow", "max"), ("fast", "high")])
def test_probe_r6_effort_reaches_the_wire_in_a_form_anthropic_accepts(tmp_path, monkeypatch, model, effort):
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", True)
    bodies: list[dict] = []
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin = Plugin(model_factory=_anthropic_capture(bodies))
        chat = service(crud, current, plugin, Settings(model=model, effort=effort), [])
        asyncio.run(chat.answer(session, "hello"))
    body = bodies[0]
    thinking = body.get("thinking") or {}
    choice = (body.get("tool_choice") or {}).get("type")
    if model == "slow":
        assert thinking.get("type") == "enabled"
        assert choice not in {"any", "tool"}, "Anthropic rejects forced tool use with extended thinking"
        assert body["max_tokens"] > thinking["budget_tokens"]
    else:
        assert thinking.get("type") == "adaptive"
        assert (body.get("output_config") or {}).get("effort") == effort
    system = body.get("system")
    assert isinstance(system, list), "instructions go as blocks so the last one can carry cache_control"
    assert system[-1].get("cache_control") == {"type": "ephemeral", "ttl": "5m"}


# --- A: an effort the offer doesn't list is still sent ---------------------------------------------------------------
def test_probe_a_effort_outside_the_offers_list_is_not_sent(tmp_path):
    seen: list[dict] = []

    class Narrow(Plugin):
        def models(self) -> list[ModelOffer]:
            return [ModelOffer(id="fast", thinking="adaptive", efforts=["low"])]

    def factory(model_id: str) -> Model:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(dict(info.model_settings or {}))
            return answer(messages, info)

        return FunctionModel(respond, model_name=model_id)

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, Narrow(model_factory=factory), Settings(effort="max"), [])
        try:
            asyncio.run(chat.answer(session, "hello"))
        except HeinzelError:
            return
    assert seen[0].get("anthropic_effort") != "max"


# --- R7: a plugin model raising mid-turn: clean error, and the next message still runs --------------------------------
def test_probe_r7_a_raising_plugin_model_leaves_a_usable_chat(tmp_path):
    state = {"n": 0}

    def flaky(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        state["n"] += 1
        if state["n"] == 1:
            msg = "proxy said no; Authorization: Bearer eyJSECRET"
            raise RuntimeError(msg)
        return answer(messages, info)

    from heinzel.modules.heinzel._session_models import MessageRequest

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        plugin = Plugin(model_factory=lambda mid: FunctionModel(flaky, model_name=mid))
        chat = service(crud, current, plugin, Settings(), [])
        with pytest.raises(Exception) as first:  # noqa: PT011
            asyncio.run(chat.answer(session, "hello"))
        assert isinstance(first.value, HeinzelError), f"raw {type(first.value).__name__} escaped dispatch"
        crud.accept(session, MessageRequest(request_id="again", text="Again"))
        replied = asyncio.run(chat.answer(session, "again"))
    assert replied.message.text == REPLY


def test_probe_usage_entry_has_no_header_fields():
    assert not {f for f in UsageEntry.model_fields if "token" in f and not f.endswith("_tokens")}
