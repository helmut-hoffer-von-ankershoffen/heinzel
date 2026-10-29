"""Explicit reviewed grant is the only path to a host-selected model turn."""

import asyncio
from datetime import datetime

import httpx2
import pydantic_ai.models
import pytest
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from heinzel import CallIdentity, Config, Credentials, CurrentUser, HeinzelError, Principal, Settings
from heinzel.kernel._credentials import EnvironmentBackend
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._pydantic import API_KEY, PydanticAiAccess
from heinzel.modules.heinzel import _interactive_turn
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._model_grants import ModelGrant, ModelGrants
from heinzel.modules.heinzel._priced_model import CHECKED
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_models import MessageRequest, SessionCosts
from heinzel.modules.heinzel._session_operations import _SessionApprovalIssuer
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository

pytestmark = pytest.mark.unit


def test_approved_owner_binding_drives_fake_transport_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    monkeypatch.setattr(pydantic_ai.models, "ALLOW_MODEL_REQUESTS", True)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return CHECKED

    monkeypatch.setattr(_interactive_turn, "datetime", FixedDatetime)
    current = CurrentUser(tmp_path / "identity")
    calls = []

    def reply(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/input_tokens"):
            return httpx2.Response(200, json={"object": "response.input_tokens", "input_tokens": 100})
        return httpx2.Response(
            200,
            json={
                "id": "resp-test",
                "object": "response",
                "created_at": 1793300000,
                "status": "completed",
                "model": "gpt-4.1-mini-2025-04-14",
                "output": [
                    {
                        "id": "msg-test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": '{"report":"A durable answer"}', "annotations": []}
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "total_tokens": 110,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(reply))
    model = OpenAIResponsesModel(
        "gpt-4.1-mini-2025-04-14", provider=OpenAIProvider(api_key="test-only", http_client=client)
    )

    access = PydanticAiAccess(
        AccessSettings(model="openai:gpt-4.1-mini-2025-04-14"),
        Credentials(EnvironmentBackend(), [("ai.access", API_KEY)]),
        Config.empty().settings(Settings, "hz"),
        build=lambda *_: model,
        installed=lambda _: True,
        binding=lambda: "pydantic-ai",
        current_settings=lambda: AccessSettings(model="openai:gpt-4.1-mini-2025-04-14"),
    )
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        crud.accept(session, MessageRequest(request_id="one", text="Hello"))
        grants = ModelGrants(crud)
        grant = ModelGrant(
            request_id="grant-one",
            revision=1,
            access="pydantic-ai",
            model="openai:gpt-4.1-mini-2025-04-14",
            requests=2,
            total_tokens=2000,
            output_tokens=200,
            timeout_seconds=30,
            message_cap_usd="0.2",
            session_cap_usd="2",
            owner_cap_usd="3",
        )
        grants.propose(session, grant)
        with pytest.raises(HeinzelError, match="no approved"):
            asyncio.run(InteractiveTurn(crud).run_granted(session, "one", current, access))
        issuer = _SessionApprovalIssuer(crud, None)
        arguments = {"session_id": session, "request_id": "grant-one"}
        assert issuer.review("heinzel", "approve-model-grant", arguments) == grant.model_dump(mode="json")
        proof = issuer.issue("heinzel", "approve-model-grant", arguments, "test-human-decision")
        assert grants.approve(session, "grant-one", proof) == grant
        assert grants.approve(session, "grant-one", proof) == grant
        reopened = SessionCrud(
            PersonalSessionRepository(current), lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2)
        )
        assert ModelGrants(reopened).approved(session) == grant
        result = asyncio.run(InteractiveTurn(reopened).run_granted(session, "one", current, access))
        assert result.text == "A durable answer"
        assert calls == ["/v1/responses/input_tokens", "/v1/responses"]
        replay = asyncio.run(InteractiveTurn(reopened).run_granted(session, "one", current, access))
        assert replay == result
        assert calls == ["/v1/responses/input_tokens", "/v1/responses"]
        stranger = CallIdentity(
            Principal("u-" + "a" * 32), "api-bearer", "other", "private", frozenset({"private:read"})
        )
        with current.using(stranger), pytest.raises(HeinzelError):
            ModelGrants(reopened).approved(session)


def test_stale_or_forged_grant_never_authorizes(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        repo = PersonalSessionRepository(current)
        crud = SessionCrud(repo, lambda: SessionCosts(session_max_usd=2, message_max_usd=0.2))
        session = crud.new_chat("create").session_id
        grants = ModelGrants(crud)
        one = ModelGrant(
            request_id="one",
            revision=1,
            access="pydantic-ai",
            model="openai:model",
            requests=1,
            total_tokens=100,
            output_tokens=10,
            timeout_seconds=10,
            message_cap_usd="0.1",
            session_cap_usd="1",
            owner_cap_usd="2",
        )
        competing = one.model_copy(update={"request_id": "competing"})
        grants.propose(session, one)
        grants.propose(session, competing)
        with pytest.raises(HeinzelError, match="invalid or unavailable"):
            grants.approve(session, "one", "x" * 43)
        first_proof = grants.issue(session, "one", "test-human-decision")
        stale_proof = grants.issue(session, "competing", "test-human-decision")
        grants.approve(session, "one", first_proof)
        with pytest.raises(HeinzelError, match="changed"):
            grants.approve(session, "competing", stale_proof)
        assert grants.approved(session) == one
