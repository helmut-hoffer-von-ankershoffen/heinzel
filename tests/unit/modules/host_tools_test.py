"""Only host-selected registered reads with an exact owner-approved grant can run."""

import asyncio
import json
from dataclasses import replace
from typing import Annotated

import pytest
from pydantic import Field
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

pytestmark = pytest.mark.unit


def test_granted_registered_read_executes_with_owner_and_writes_never_execute(make_kernel, tmp_path):  # noqa: PLR0915
    module = Module("fixture", "A local test service")
    calls = []

    @module.operation()
    def inspect(current: CurrentUser, amount: Annotated[int, Field(ge=1, le=3)]) -> dict[str, object]:
        """Read a harmless local value."""
        calls.append((current.principal().id, amount))
        return {"count": amount}

    @module.operation(read_only=False)
    def change(amount: int) -> str:
        """A write which must never run."""
        calls.append(("write", amount))
        return "changed"

    kernel = make_kernel(fixture=module)
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    with current.local("cli"):
        crud = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=1)
        )
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Read the number"))
        catalog = HostToolCatalog(kernel, current, crud, session, frozenset({"fixture:inspect", "fixture:change"}))
        preview = catalog.preview()
        assert [item.identity for item in preview] == ["fixture:inspect"]
        registered = next(item for item in exposed(kernel, "mcp") if item.operation.name == "inspect")
        newer = replace(
            registered, record=replace(registered.record, origin=replace(registered.record.origin, version="10.0.0"))
        )
        assert _schema(registered)[1] != _schema(newer)[1]
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
        assert catalog.approved_preview(session, grant) == preview
        with pytest.raises(HeinzelError, match="unavailable"):
            catalog.invoke(grant, "fixture:change", {"amount": 1})
        with pytest.raises(HeinzelError, match="unavailable"):
            catalog.invoke(grant, "unknown:read", {})
        with pytest.raises(HeinzelError, match="Invalid operation"):
            catalog.invoke(grant, "fixture:inspect", {"amount": 9})
        with pytest.raises(HeinzelError, match="Invalid operation"):
            catalog.invoke(grant, "fixture:inspect", {"amount": 1, "current": "injected"})
        assert calls == []
        requests = []

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            requests.append(1)
            if len(requests) == 1:
                assert "fixture:inspect" in str(info.instructions)
                return ModelResponse(
                    [ToolCallPart("use_tool", {"name": "fixture:inspect", "arguments": {"amount": 2}})]
                )
            assert any(
                isinstance(part, ToolReturnPart) and json.loads(part.content)["count"] == 2
                for msg in messages
                for part in msg.parts
                if isinstance(part, ToolReturnPart)
            )
            return ModelResponse([ToolCallPart(info.output_tools[0].name, {"report": "Read complete"})])

        result = asyncio.run(
            InteractiveTurn(crud).run(
                session, "one", FunctionModel(model), limits=TurnLimits(), expected_grant=grant, catalog=catalog
            )
        )
        assert result.text == "Read complete"
        assert calls == [(current.principal().id, 2)]
        activity = InteractiveTurn(crud).activity(session)
        assert activity[-1].tool == "hz.read"
        assert activity[-1].status == "completed"
        next_grant = grant.model_copy(update={"request_id": "next", "revision": 2, "allowed_tools": {}})
        grants.propose(session, next_grant)
        grants.approve(session, "next", grants.issue(session, "next", "fixture-human-review"))
        with pytest.raises(HeinzelError, match="grant changed"):
            catalog.invoke(grant, "fixture:inspect", {"amount": 1})
        assert catalog.approved_preview(session, next_grant) == []
        with pytest.raises(HeinzelError, match="unavailable"):
            catalog.invoke(next_grant, "fixture:inspect", {"amount": 1})
        changed_schema = next_grant.model_copy(
            update={"request_id": "third", "revision": 3, "allowed_tools": {"fixture:inspect": "0" * 64}}
        )
        grants.propose(session, changed_schema)
        grants.approve(session, "third", grants.issue(session, "third", "fixture-human-review"))
        with pytest.raises(HeinzelError, match="unavailable or has changed"):
            catalog.approved_preview(session, changed_schema)
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError, match="different owner"):
            catalog.preview()
        assert len(calls) == 1


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
        catalog = HostToolCatalog(kernel, current, crud, session, frozenset({"system:modules"}))
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
