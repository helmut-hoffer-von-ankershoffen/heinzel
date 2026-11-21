"""Configured API bindings and connected turns beyond the pinned OpenAI model (ADR 0159).

A plugin's `ai-models` option and the built-in option at an Anthropic-compatible endpoint go through the same
owner-bound bindings, model admission and grant checks; nothing here reaches a network.
"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from heinzel import (
    CallIdentity,
    Config,
    ConfiguredApiSnapshot,
    Credentials,
    CurrentUser,
    HeinzelError,
    Principal,
    Settings,
)
from heinzel.kernel import AiModelsSource, EnvironmentBackend, ModelOffer
from heinzel.kernel.ai import _ai_models
from heinzel.modules.ai.access._configured import (
    ConfiguredApiBinding,
    EndpointApiBinding,
    PluginApiBinding,
    TrustedConfiguredApiAccess,
)
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._model_admission import connected_model
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._priced_model import MODEL, priced
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def fresh_model_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test reads the plugins' lists afresh: hz keeps a list per adapter for a few seconds."""
    monkeypatch.setattr(_ai_models, "_listings", {})


BASE = "https://ai-proxy.example.com"
STRANGER = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset())


def proxied(**changes: Any) -> AccessSettings:
    values: dict[str, Any] = {
        "model": "claude-example-1",
        "base_url": BASE,
        "api": "anthropic",
    }
    return AccessSettings(**(values | changes))


def builtin(settings: list[AccessSettings], selected: list[str], built: list[Any] | None = None) -> PydanticAiAccess:
    def build(*args: Any, **kwargs: Any) -> Any:
        if built is not None:
            built.append((args, kwargs))
        return TestModel(custom_output_text="OK")

    return PydanticAiAccess(
        settings[0],
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=build,
        installed=lambda _package: True,
        binding=lambda: selected[0],
        current_settings=lambda: settings[0],
    )


class Models:
    """A plugin's `ai-models` adapter: one model, built without calling it."""

    def __init__(self) -> None:
        self.offers = [ModelOffer(id="acme-large"), ModelOffer(id="acme-small")]
        self.built: list[str] = []

    def models(self) -> list[ModelOffer]:
        return self.offers

    def model(self, model_id: str) -> Any:
        self.built.append(model_id)
        return TestModel(custom_output_text=f"from {model_id}")


def plugin(models: Models, version: list[str], selected: list[str], wanted: list[str | None], current: CurrentUser):
    def source() -> AiModelsSource:
        return AiModelsSource("acme", "acme.models", "heinzel-acme", version[0], models)

    return PluginApiBinding(current, source, lambda: selected[0], lambda: wanted[0])


# The built-in option at an endpoint


def test_an_endpoint_binding_fingerprints_the_endpoint_and_key_never_shows_it(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy-one")
    current = CurrentUser(tmp_path / "identity")
    settings, selected, built = [proxied()], ["pydantic-ai"], []
    binding = EndpointApiBinding(current, builtin(settings, selected, built))
    with current.local("cli"):
        snapshot = binding.capture()
        assert (snapshot.access, snapshot.model, snapshot.endpoint) == ("pydantic-ai", "claude-example-1", BASE)
        assert "sk-proxy" not in repr(snapshot)
        binding.recheck(snapshot)
        assert not built
        lease = binding.lease()
        assert isinstance(lease.build_approved(snapshot), TestModel)
        assert built == [(("claude-example-1", "sk-proxy-one", BASE), {"api": "anthropic"})]
        with pytest.raises(HeinzelError):
            lease.build_approved(snapshot)  # at most once

        for change in (
            {"base_url": "https://other.example.com"},
            {"api": "openai"},
            {"model": "claude-example-2"},
        ):
            settings[0] = proxied(**change)
            with pytest.raises(HeinzelError, match="changed"):
                EndpointApiBinding(current, builtin(settings, selected)).recheck(snapshot)
        settings[0] = proxied()
        monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy-two")
        with pytest.raises(HeinzelError, match="changed"):
            binding.recheck(snapshot)  # a rotated key changes what the turn would send
        monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy-one")
        selected[0] = "claude-code"
        with pytest.raises(HeinzelError):
            binding.recheck(snapshot)
        selected[0] = "pydantic-ai"
        with current.using(STRANGER), pytest.raises(HeinzelError):
            binding.capture()
        binding.recheck(snapshot)


def test_an_endpoint_binding_refuses_what_a_turn_refuses(tmp_path, monkeypatch):
    monkeypatch.delenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", raising=False)
    current = CurrentUser(tmp_path / "identity")
    selected = ["pydantic-ai"]
    with current.local("cli"):
        with pytest.raises(HeinzelError, match="unavailable or changed"):
            EndpointApiBinding(current, builtin([proxied()], selected)).capture()  # remote, no key
        monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
        with pytest.raises(HeinzelError, match="unavailable or changed"):
            EndpointApiBinding(current, builtin([proxied(base_url="http://ai.example.com")], selected)).capture()
        with pytest.raises(HeinzelError, match="unavailable or changed"):
            EndpointApiBinding(current, builtin([AccessSettings()], selected)).capture()  # no model
    with pytest.raises(HeinzelError):
        EndpointApiBinding(current, SimpleNamespace())  # type: ignore[arg-type]


def test_a_connected_turn_runs_on_the_anthropic_endpoint_option(tmp_path, monkeypatch):
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
    current = CurrentUser(tmp_path / "identity")
    built: list[Any] = []

    def answer(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "via the proxy"})])

    access = builtin([proxied()], ["pydantic-ai"])
    access._build = lambda *args, **kwargs: built.append((args, kwargs)) or FunctionModel(answer)
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=1)
        )
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="hello"))
        result = asyncio.run(
            InteractiveTurn(crud).run_connected(
                session,
                "one",
                current,
                access,
                selected_access="pydantic-ai",
                approved_access="pydantic-ai",
                approved_model="claude-example-1",
                limits=TurnLimits(requests=2, total_tokens=2000, output_tokens=200),
            )
        )
    assert result.text == "via the proxy"
    assert built == [(("claude-example-1", "sk-proxy", BASE), {"api": "anthropic"})]


# A plugin's models


def test_a_plugin_binding_fingerprints_plugin_version_and_model(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    models, version, selected, wanted = Models(), ["1.0.0"], ["acme"], [None]
    binding = plugin(models, version, selected, wanted, current)
    with current.local("cli"):
        snapshot = binding.capture()
        assert (snapshot.access, snapshot.model, snapshot.endpoint) == (
            "acme",
            "acme-large",
            "plugin:heinzel-acme==1.0.0",
        )
        assert binding.access == "acme"
        binding.recheck(snapshot)
        assert not models.built
        lease = binding.lease()
        assert isinstance(lease.build_approved(snapshot), TestModel)
        assert models.built == ["acme-large"]

        for change in (lambda: version.__setitem__(0, "1.0.1"), lambda: wanted.__setitem__(0, "acme-small")):
            change()
            with pytest.raises(HeinzelError, match="changed"):
                binding.recheck(snapshot)
            version[0], wanted[0] = "1.0.0", None
        stale = binding.lease()
        version[0] = "2.0.0"  # the plugin updated between the lease and the build
        with pytest.raises(HeinzelError, match="unavailable or changed"):
            stale.build_approved(replace(stale.snapshot))
        version[0] = "1.0.0"
        selected[0] = "pydantic-ai"
        with pytest.raises(HeinzelError):
            binding.capture()
        selected[0] = "acme"
        with current.using(STRANGER), pytest.raises(HeinzelError):
            binding.capture()
        retired = Models()  # a new adapter: its own model list, not the one hz keeps for a few seconds
        retired.offers = [ModelOffer(id="acme-large", available=False)]
        with pytest.raises(HeinzelError):
            plugin(retired, version, selected, wanted, current).capture()


def test_connected_model_takes_a_plugin_factory_for_its_own_selection(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    models, selected = Models(), ["acme"]
    factory = plugin(models, ["1.0.0"], selected, [None], current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = (
            SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("create").session_id
        )

        def admit(selected_access: str, model: str = "acme-large") -> Any:
            return connected_model(
                repo,
                current,
                factory,
                session_id=session,
                owner=current.principal().id,
                selected_access=selected_access,
                approved_access=selected_access,
                approved_model=model,
                limits=TurnLimits(requests=1, total_tokens=100, output_tokens=10),
            )

        assert isinstance(admit("acme"), TestModel)
        with pytest.raises(HeinzelError, match="selected AI access"):
            admit("pydantic-ai")  # the factory serves acme, whatever the caller claims
        with pytest.raises(HeinzelError, match="no longer matches"):
            admit("acme", "acme-small")
        with pytest.raises(HeinzelError):
            factory.turn_model(CurrentUser(tmp_path / "other"), "acme-large")
    assert models.built == ["acme-large"]


def test_a_dollar_capped_grant_refuses_an_unpriced_binding_before_building(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=1)
        )
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="hello"))
        grants = ModelGrants(crud)
        snapshot = ConfiguredApiSnapshot(
            current.principal().id, "acme", "acme-large", "plugin:heinzel-acme==1", "f" * 64
        )
        grant = ModelGrant(
            request_id="grant-one",
            revision=1,
            binding=snapshot,
            access="acme",
            model="acme-large",
            requests=2,
            total_tokens=1000,
            output_tokens=100,
            timeout_seconds=30,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
        )
        grants.propose(session, grant)
        grants.approve(session, "grant-one", grants.issue(session, "grant-one", "test-human-decision"))

        class NoKernel:
            def resolve_builtin_service(self, *_args: Any) -> Any:
                raise AssertionError  # refused before any binding is resolved or model built

        with pytest.raises(HeinzelError, match="reports no dollar cost"):
            asyncio.run(InteractiveTurn(crud).run_granted(session, "one", current, NoKernel()))  # type: ignore[arg-type]
        other = crud.new_chat("create").session_id
        crud.accept(other, MessageRequest(request_id="two", text="hello"))
        mismatched = {"request_id": "grant-two", "revision": 2, "binding": replace(snapshot, model="other")}
        grants.propose(other, grant.model_copy(update=mismatched))
        grants.approve(other, "grant-two", grants.issue(other, "grant-two", "test-human-decision"))
        with pytest.raises(HeinzelError, match="cannot be verified"):
            asyncio.run(InteractiveTurn(crud).run_granted(other, "two", current, NoKernel()))  # type: ignore[arg-type]


def test_only_the_pinned_direct_model_is_priced():
    assert priced("pydantic-ai", MODEL, "https://api.openai.com/v1/")
    assert not priced("pydantic-ai", MODEL, BASE)
    assert not priced("acme", MODEL, "https://api.openai.com/v1")
    assert not priced("pydantic-ai", "claude-example-1", "https://api.openai.com/v1")


# Which binding the trusted service resolves


class FakeKernel:
    def __init__(self, access: PydanticAiAccess, plugins: dict[str, AiModelsSource]) -> None:
        self.access = access
        self.plugins = plugins

    def offers_ai_models(self, name: str | None) -> bool:
        return name in self.plugins

    def ai_models(self, name: str) -> AiModelsSource:
        return self.plugins[name]

    def resolve_builtin_service(self, kind: type, owner: str) -> Any:
        assert (kind, owner) == (PydanticAiAccess, "ai.access")
        return self.access


def test_the_trusted_service_resolves_the_selected_kind_of_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HZ_CREDENTIAL_AI_ACCESS_API_KEY", "sk-proxy")
    current = CurrentUser(tmp_path / "identity")
    selected = ["pydantic-ai"]
    pinned = builtin([AccessSettings(model=MODEL)], selected)
    acme = AiModelsSource("acme", "acme.models", "heinzel-acme", "1.0.0", Models())
    kernel = FakeKernel(pinned, {"acme": acme})

    def service(access: PydanticAiAccess) -> TrustedConfiguredApiAccess:
        kernel.access = access
        return TrustedConfiguredApiAccess(current, kernel, lambda: selected[0], lambda: None)  # type: ignore[arg-type]

    with current.local("cli"):
        assert type(service(pinned)._binding()) is ConfiguredApiBinding
        assert service(pinned).capture().endpoint == "https://api.openai.com/v1"
        assert service(pinned).factory() is pinned
        endpoint = builtin([proxied()], selected)
        assert type(service(endpoint)._binding()) is EndpointApiBinding
        assert service(endpoint).capture().endpoint == BASE
        selected[0] = "acme"
        chosen = service(endpoint)
        assert type(chosen._binding()) is PluginApiBinding
        assert chosen.capture().access == "acme"
        assert chosen.factory().access == "acme"
        snapshot = chosen.capture()
        chosen.recheck(snapshot)
        assert isinstance(chosen.lease().build_approved(snapshot), TestModel)
        selected[0] = "pydantic-ai"
        with pytest.raises(HeinzelError, match="changed"):
            chosen.recheck(snapshot)


def test_a_text_reply_from_a_plugin_model_through_the_factory(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    models = Models()
    models.model = lambda model_id: FunctionModel(lambda _m, _i: ModelResponse([TextPart(f"hi from {model_id}")]))  # type: ignore[method-assign]
    factory = plugin(models, ["1"], ["acme"], [None], current)
    with current.local("cli"):
        built = factory.turn_model(current, "acme-large")
    assert isinstance(built, FunctionModel)


def test_connected_model_refuses_another_owner_and_a_non_model(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    models = Models()
    factory = plugin(models, ["1.0.0"], ["acme"], [None], current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        session = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1)).new_chat("c").session_id

        def admit(owner: str) -> Any:
            return connected_model(
                repo,
                current,
                factory,
                session_id=session,
                owner=owner,
                selected_access="acme",
                approved_access="acme",
                approved_model="acme-large",
                limits=TurnLimits(requests=1, total_tokens=100, output_tokens=10),
            )

        with pytest.raises(HeinzelError, match="different authenticated owner"):
            admit("u-" + "b" * 32)
        models.model = lambda _model_id: "not a model"  # type: ignore[method-assign]
        with pytest.raises(HeinzelError):
            admit(current.principal().id)


def test_a_grant_without_its_binding_or_for_another_owner_is_refused(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=1)
        )
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="hello"))
        grants = ModelGrants(crud)
        grant = ModelGrant(
            request_id="grant-one",
            revision=1,
            binding=None,
            access="acme",
            model="acme-large",
            requests=2,
            total_tokens=1000,
            output_tokens=100,
            timeout_seconds=30,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
        )
        grants.propose(session, grant)
        grants.approve(session, "grant-one", grants.issue(session, "grant-one", "test-human-decision"))
        with pytest.raises(HeinzelError, match="no configured access binding"):
            asyncio.run(InteractiveTurn(crud).run_granted(session, "one", current, None))  # type: ignore[arg-type]
        with current.using(STRANGER), pytest.raises(HeinzelError):
            asyncio.run(InteractiveTurn(crud).run_granted(session, "one", current, None))  # type: ignore[arg-type]
