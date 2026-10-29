"""Configured model construction and a real bounded turn using an offline transport."""

import asyncio
from decimal import Decimal

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import CallIdentity, Config, Credentials, CurrentUser, HeinzelError, Principal, Settings
from heinzel.kernel._credentials import EnvironmentBackend
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def test_reserved_direct_client_ignores_proxy_and_endpoint_environment(monkeypatch):
    from heinzel.modules.ai.access._pydantic import build_reserved_openai

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")
    monkeypatch.delenv("OPENAI_CUSTOM_HEADERS", raising=False)
    model = build_reserved_openai("test-only")
    assert str(model.client.base_url) == "https://api.openai.com/v1/"
    assert model.client.max_retries == 0
    assert model.client._client.trust_env is False
    assert model.client._client.follow_redirects is False
    asyncio.run(model.client.close())
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: unsafe")
    with pytest.raises(HeinzelError, match="Custom OpenAI headers"):
        build_reserved_openai("test-only")


def test_configured_owner_credential_drives_real_bounded_turn_without_live_billing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "example-private-key")
    current = CurrentUser(tmp_path / "state" / "identity")
    captured = []
    calls = []

    def model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(1)
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Hello from the configured model"})])

    def build(model_name, key, base):
        captured.append((model_name, key, base))
        return FunctionModel(model)

    access = PydanticAiAccess(
        AccessSettings(model="openai:configured"),
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=build,
        installed=lambda _: True,
        binding=lambda: "pydantic-ai",
        current_settings=lambda: AccessSettings(model="openai:configured"),
    )
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Say hello"))
        runner = InteractiveTurn(crud)
        result = asyncio.run(
            runner.run_connected(
                session,
                "one",
                current,
                access,
                selected_access="pydantic-ai",
                approved_access="pydantic-ai",
                approved_model="openai:configured",
                limits=TurnLimits(requests=2, total_tokens=2000, output_tokens=200),
            )
        )
        assert result.text == "Hello from the configured model"
        assert captured == [("openai:configured", "example-private-key", None)]
        assert calls == [1]
        assert "example-private-key" not in str(repo.control_read(session))
        replay = asyncio.run(
            runner.run_connected(
                session,
                "one",
                current,
                access,
                selected_access="pydantic-ai",
                approved_access="pydantic-ai",
                approved_model="openai:configured",
                limits=TurnLimits(requests=2, total_tokens=2000, output_tokens=200),
            )
        )
        assert replay == result
        assert calls == [1]


def test_invalid_owner_credential_binding_and_hard_dollar_cap_never_build_or_start(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    current = CurrentUser(tmp_path / "state" / "identity")
    builds = []
    access = PydanticAiAccess(
        AccessSettings(model="openai:configured"),
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=lambda *args: builds.append(args),
        installed=lambda _: True,
        binding=lambda: "pydantic-ai",
        current_settings=lambda: AccessSettings(model="openai:configured"),
    )
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="hello"))
        runner = InteractiveTurn(crud)

        async def execute(selected="pydantic-ai", model="openai:configured", limits=None):
            return await runner.run_connected(
                session,
                "one",
                current,
                access,
                selected_access=selected,
                approved_access="pydantic-ai",
                approved_model=model,
                limits=limits or TurnLimits(),
            )

        with pytest.raises(HeinzelError, match="model binding"):
            asyncio.run(execute(selected="other"))
        with pytest.raises(HeinzelError, match="configured model"):
            asyncio.run(execute(model="openai:other"))
        with pytest.raises(HeinzelError, match="credential is unavailable"):
            asyncio.run(execute())
        monkeypatch.setenv("OPENAI_API_KEY", "example-private-key")
        with pytest.raises(HeinzelError, match="hard dollar cap"):
            asyncio.run(execute(limits=TurnLimits(cost_usd=Decimal(1))))
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="different authenticated owner"):
            asyncio.run(execute())
        assert not builds
        assert all(event.kind != "interactive.started" for event in repo.control_read(session).events)


def test_factory_checks_selected_access_owner_and_endpoint_before_build(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "example-private-key")
    current = CurrentUser(tmp_path / "state" / "identity")
    builds = []
    selected = ["other"]
    access = PydanticAiAccess(
        AccessSettings(model="openai:configured"),
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=lambda *args: builds.append(args),
        installed=lambda _: True,
        binding=lambda: selected[0],
        current_settings=lambda: AccessSettings(model="openai:configured"),
    )
    with current.local("cli"):
        with pytest.raises(HeinzelError, match="selected AI access"):
            access.turn_model(current, "openai:configured")
        selected[0] = "pydantic-ai"
        stale = PydanticAiAccess(
            AccessSettings(model="openai:configured"),
            Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
            Config.empty().settings(Settings, "hz"),
            build=lambda *args: builds.append(args),
            installed=lambda _: True,
            binding=lambda: "pydantic-ai",
            current_settings=lambda: AccessSettings(model="openai:changed"),
        )
        with pytest.raises(HeinzelError, match="changed"):
            stale.turn_model(current, "openai:configured")
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="installation owner"):
            access.turn_model(current, "openai:configured")
        unsafe = PydanticAiAccess(
            AccessSettings(model="openai:configured", base_url="http://example.com/v1"),
            Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
            Config.empty().settings(Settings, "hz"),
            build=lambda *args: builds.append(args),
            installed=lambda _: True,
            binding=lambda: "pydantic-ai",
            current_settings=lambda: AccessSettings(model="openai:configured", base_url="http://example.com/v1"),
        )
        with pytest.raises(HeinzelError, match="requires a configured credential"):
            unsafe.turn_model(current, "openai:configured")
    assert builds == []
