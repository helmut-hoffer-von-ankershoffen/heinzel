"""Only explicitly reviewed, owner-bound and host-verified argument-free reads may run."""

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import CallIdentity, CurrentUser, HeinzelError, Module, Principal
from heinzel.kernel._exposure import exposed
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._host_tools import HostToolCatalog, _schema
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from heinzel.modules.heinzel._tool_argument_policy import ExactArgumentPolicy

pytestmark = pytest.mark.unit


def test_granted_read_fences_code_owner_turn_and_model_arguments(make_kernel, tmp_path):  # noqa: PLR0915
    module = Module("fixture", "Local read tests")
    calls = []

    @module.operation()
    def inspect(current: CurrentUser) -> dict[str, object]:
        """Read a harmless local value."""
        calls.append(current.principal().id)
        return {"count": 2}

    @module.operation()
    def fetch(path: str) -> str:
        """A parameterized read without a resource policy."""
        calls.append(path)
        return path

    @module.operation(read_only=False)
    def change() -> str:
        """A write which must never run."""
        calls.append("write")
        return "changed"

    kernel = make_kernel(fixture=module)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Read the number"))
        allowed = frozenset({"fixture:inspect", "fixture:fetch", "fixture:change"})
        no_verifier = HostToolCatalog(kernel, current, crud, session, allowed)
        with pytest.raises(HeinzelError, match="verified implementation"):
            no_verifier.preview()
        code_identity = ["a" * 64]
        catalog = HostToolCatalog(kernel, current, crud, session, allowed, verified_code=lambda _item: code_identity[0])
        preview = catalog.preview()
        assert [item.identity for item in preview] == ["fixture:inspect"]
        descriptor = catalog.review_descriptors()[0]
        assert descriptor.identity == preview[0].identity
        assert descriptor.schema == preview[0].schema
        assert descriptor.binding_sha256 == preview[0].binding_sha256
        assert descriptor.fixed_arguments == {}
        assert descriptor.executable
        assert "independently unverified" in descriptor.code_evidence
        registered = next(item for item in exposed(kernel, "mcp") if item.operation.name == "inspect")
        newer = replace(
            registered, record=replace(registered.record, origin=replace(registered.record.origin, version="10.0.0"))
        )
        assert _schema(registered, "a" * 64)[1] != _schema(newer, "a" * 64)[1]
        assert _schema(registered, "a" * 64)[1] != _schema(registered, "b" * 64)[1]
        changed_summary = replace(registered, operation=replace(registered.operation, summary="different description"))
        assert _schema(registered, "a" * 64)[1] != _schema(changed_summary, "a" * 64)[1]
        grant = ModelGrant(
            request_id="grant",
            revision=1,
            access="pydantic-ai",
            model="openai:model",
            requests=2,
            total_tokens=1000,
            output_tokens=100,
            timeout_seconds=10,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
            allowed_tools={preview[0].identity: preview[0].binding_sha256},
        )
        grants = ModelGrants(crud)
        grants.propose(session, grant)
        grants.approve(session, "grant", grants.issue(session, "grant", "fixture-human-review"))
        with repo.main_writer(session) as writer:
            with pytest.raises(HeinzelError, match="unavailable"):
                catalog.invoke(grant, "fixture:fetch", {"path": "/private"}, turn_writer=writer)
            with pytest.raises(HeinzelError, match="Invalid operation"):
                catalog.invoke(grant, "fixture:inspect", {"current": "injected"}, turn_writer=writer)
            code_identity[0] = "b" * 64
            with pytest.raises(HeinzelError, match="schema changed"):
                catalog.invoke(grant, "fixture:inspect", {}, turn_writer=writer)
            code_identity[0] = "a" * 64
        with pytest.raises(HeinzelError, match="turn lock"):
            catalog.invoke(grant, "fixture:inspect", {}, turn_writer=writer)
        other = crud.new_chat("other").session_id
        with repo.main_writer(other) as wrong_writer, pytest.raises(HeinzelError, match="turn lock"):
            catalog.invoke(grant, "fixture:inspect", {}, turn_writer=wrong_writer)
        assert calls == []
        rounds = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse([ToolCallPart("use_tool", {"name": "fixture:inspect", "arguments": {}})])
            assert any(
                isinstance(part, ToolReturnPart) and json.loads(part.content)["count"] == 2
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            )
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Read complete"})])

        result = asyncio.run(
            InteractiveTurn(crud).run(
                session, "one", FunctionModel(model), limits=TurnLimits(), expected_grant=grant, catalog=catalog
            )
        )
        assert result.text == "Read complete"
        assert calls == [current.principal().id]
        activity = InteractiveTurn(crud).activity(session)
        assert [(event.tool, event.status) for event in activity] == [
            ("fixture:inspect", "requested"),
            ("fixture:inspect", "completed"),
        ]
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="different owner"):
            catalog.preview()
        with current.using(stranger), pytest.raises(HeinzelError, match="different owner"):
            catalog.review_descriptors()


def test_invoked_failure_is_not_recorded_as_denial_and_does_not_leak(make_kernel, tmp_path):
    module = Module("fixture", "Local read test")
    calls = []

    @module.operation()
    def broken() -> str:
        """Fail after the host invokes the read."""
        calls.append(1)
        msg = "PRIVATE_OPERATION_SECRET"
        raise RuntimeError(msg)

    kernel = make_kernel(fixture=module)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Try the read"))
        catalog = HostToolCatalog(
            kernel, current, crud, session, frozenset({"fixture:broken"}), verified_code=lambda _: "a" * 64
        )
        preview = catalog.preview()[0]
        grant = ModelGrant(
            request_id="grant",
            revision=1,
            access="pydantic-ai",
            model="openai:model",
            requests=2,
            total_tokens=1000,
            output_tokens=100,
            timeout_seconds=10,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
            allowed_tools={preview.identity: preview.binding_sha256},
        )
        grants = ModelGrants(crud)
        grants.propose(session, grant)
        grants.approve(session, "grant", grants.issue(session, "grant", "fixture-human-review"))
        rounds = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse([ToolCallPart("use_tool", {"name": "fixture:broken", "arguments": {}})])
            assert "PRIVATE_OPERATION_SECRET" not in str(messages)
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Read failed"})])

        result = asyncio.run(
            InteractiveTurn(crud).run(
                session, "one", FunctionModel(model), limits=TurnLimits(), expected_grant=grant, catalog=catalog
            )
        )
        assert result.text == "Read failed"
        assert calls == [1]
        activity = InteractiveTurn(crud).activity(session)
        assert [(item.tool, item.status) for item in activity] == [
            ("fixture:broken", "requested"),
            ("fixture:broken", "failed"),
        ]
        assert "PRIVATE_OPERATION_SECRET" not in json.dumps(
            [e.model_dump(mode="json") for e in repo.control_read(session).events]
        )


def test_granted_builtin_hz_service_read_from_fake_model(make_kernel, tmp_path):
    from heinzel.modules.system import module as system

    kernel = make_kernel(system=system)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=1)
        )
        session = crud.new_chat("builtin").session_id
        crud.accept(session, MessageRequest(request_id="one", text="List installed modules"))
        catalog = HostToolCatalog(
            kernel, current, crud, session, frozenset({"system:modules"}), verified_code=lambda _: "a" * 64
        )
        preview = catalog.preview()
        assert len(preview) == 1
        assert preview[0].identity == "system:modules"
        grant = ModelGrant(
            request_id="grant",
            revision=1,
            access="pydantic-ai",
            model="openai:model",
            requests=2,
            total_tokens=1000,
            output_tokens=100,
            timeout_seconds=10,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
            allowed_tools={"system:modules": preview[0].binding_sha256},
        )
        grants = ModelGrants(crud)
        grants.propose(session, grant)
        grants.approve(session, "grant", grants.issue(session, "grant", "fixture-human-review"))
        calls = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            calls.append(1)
            if len(calls) == 1:
                return ModelResponse([ToolCallPart("use_tool", {"name": "system:modules", "arguments": {}})])
            result = next(part.content for msg in messages for part in msg.parts if isinstance(part, ToolReturnPart))
            assert any(item["qualname"] == "system" for item in json.loads(result))
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Modules read"})])

        result = asyncio.run(
            InteractiveTurn(crud).run(
                session, "one", FunctionModel(model), limits=TurnLimits(), expected_grant=grant, catalog=catalog
            )
        )
        assert result.text == "Modules read"
        assert len(calls) == 2


def test_granted_turn_does_not_run_ungranted_local_tool(make_kernel, tmp_path):
    kernel = make_kernel(fixture=Module("fixture", "Fixture"))
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Test ungranted tool"))
        grant = ModelGrant(
            request_id="grant",
            revision=1,
            access="pydantic-ai",
            model="openai:model",
            requests=2,
            total_tokens=1000,
            output_tokens=100,
            timeout_seconds=10,
            message_cap_usd="1",
            session_cap_usd="2",
            owner_cap_usd="3",
        )
        grants = ModelGrants(crud)
        grants.propose(session, grant)
        grants.approve(session, "grant", grants.issue(session, "grant", "fixture-human-review"))
        rounds = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse(
                    [ToolCallPart("use_tool", {"name": "text.word_count", "arguments": {"text": "x"}})]
                )
            assert "unavailable under this grant" in str(messages)
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Not available"})])

        asyncio.run(
            InteractiveTurn(crud).run(session, "one", FunctionModel(model), limits=TurnLimits(), expected_grant=grant)
        )
        assert [(item.tool, item.status) for item in InteractiveTurn(crud).activity(session)] == [
            ("text.word_count", "denied")
        ]


def test_parameterized_descriptor_is_reviewable_but_never_authorizes_execution(make_kernel, tmp_path):
    module = Module("fixture", "Scoped read fixture")
    calls = []

    @module.operation()
    def fetch(account: str, resource: str, limit: int = 10) -> str:
        """Read a fixture resource, not a verified provider."""
        calls.append((account, resource, limit))
        return resource

    kernel = make_kernel(fixture=module)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=1))
        session = crud.new_chat("create").session_id
        item = next(item for item in exposed(kernel, "mcp") if item.operation.name == "fetch")
        _, binding = _schema(item, "a" * 64)
        policy = ExactArgumentPolicy(
            "fixture:fetch", binding, (("account", "reviewed"), ("resource", "one"), ("limit", 10))
        )
        catalog = HostToolCatalog(
            kernel,
            current,
            crud,
            session,
            frozenset({"fixture:fetch"}),
            verified_code=lambda _: "a" * 64,
            argument_policies={"fixture:fetch": policy},
        )
        descriptor = catalog.review_descriptors()[0]
        assert descriptor.identity == "fixture:fetch"
        assert descriptor.binding_sha256 == binding
        assert descriptor.fixed_arguments == {"account": "reviewed", "resource": "one", "limit": 10}
        assert descriptor.policy_sha256 == policy.fingerprint()
        assert "not verified" in descriptor.scope_evidence
        assert not descriptor.executable
        assert catalog.preview() == []
        with pytest.raises(HeinzelError):
            policy.apply("fixture:fetch", binding, ("account", "resource", "limit"), {"resource": "another"})
        changed = HostToolCatalog(
            kernel,
            current,
            crud,
            session,
            frozenset({"fixture:fetch"}),
            verified_code=lambda _: "b" * 64,
            argument_policies={"fixture:fetch": policy},
        )
        with pytest.raises(HeinzelError, match="argument policy"):
            changed.review_descriptors()
        assert calls == []
