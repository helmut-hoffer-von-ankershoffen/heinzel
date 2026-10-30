"""Authenticated acceptance provenance is observational and never supplied by the caller."""

import json

import anyio
import pytest
from mcp import Client

from heinzel import CallIdentity, CurrentUser, HeinzelError, Principal
from heinzel.heads import mcp
from heinzel.modules.heinzel._session_crud import SessionCrud, _digest
from heinzel.modules.heinzel._session_models import MessageRequest
from heinzel.modules.heinzel._session_repository import PersonalSessionRepository
from tests.integration.chat_test import AUTH, web_client

pytestmark = pytest.mark.integration


def test_actual_web_mcp_daemon_origin_and_exact_cross_head_replay(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "origin-create"})
    assert created.status_code == 200, created.text
    session = created.json()["session_id"]
    request = {"session_id": session, "request_id": "web-message", "message": "private text", "origin": "daemon"}
    assert client.post("/api/heinzel/chat", json=request).status_code == 401
    assert client.post("/api/heinzel/chat", headers=AUTH, json=request).status_code == 422
    request.pop("origin")
    accepted = client.post("/api/heinzel/chat", headers=AUTH, json=request)
    assert accepted.status_code == 200, accepted.text

    async def accept_from_mcp():
        async with Client(mcp.build(kernel)) as caller:
            names = {tool.name for tool in (await caller.list_tools()).tools}
            assert "heinzel_chat" in names
            replay = await caller.call_tool(
                "heinzel_chat", {"session_id": session, "request_id": "web-message", "message": "private text"}
            )
            assert not replay.is_error
            result = await caller.call_tool(
                "heinzel_chat", {"session_id": session, "request_id": "mcp-message", "message": "mcp private"}
            )
            assert not result.is_error

    anyio.run(accept_from_mcp)
    current = kernel.resolve(CurrentUser)
    with current.local("daemon"):
        repo = PersonalSessionRepository(current)
        service = kernel.resolve(SessionCrud)
        service.accept(session, MessageRequest(request_id="daemon-message", text="daemon private"))
        events = [event for event in repo.control_read(session).events if event.kind == "message.accepted"]
        assert [event.data["origin"] for event in events] == ["web-session", "mcp-stdio", "daemon"]
        assert len(events) == 3
        encoded = json.dumps([event.data for event in events])
        assert "private text" not in encoded
        assert "mcp private" not in encoded
        assert "daemon private" not in encoded
        assert "credential_id" not in encoded
        assert "audience" not in encoded
        messages = service.snapshot(session).messages
        assert [message.origin for message in messages] == ["web-session", "mcp-stdio", "daemon"]
        with pytest.raises(HeinzelError):
            service.accept(session, MessageRequest(request_id="web-message", text="changed payload"))
        stranger = CallIdentity(Principal("u-" + "a" * 32), "api-bearer", "private-secret-id", "private", frozenset())
        with current.using(stranger), pytest.raises(HeinzelError):
            service.accept(session, MessageRequest(request_id="foreign", text="no access"))


def test_legacy_journal_origin_is_explicitly_unknown(make_kernel):
    from heinzel.modules.heinzel._session_bodies import immutable_body

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    with current.local("cli"):
        service = kernel.resolve(SessionCrud)
        session = service.new_chat("origin-legacy-create").session_id
        request = MessageRequest(request_id="legacy-message", text="legacy")
        data = request.model_dump(mode="json")
        with service.repository.control_writer(session) as writer:
            body = immutable_body(writer, "inbox", data, control=True)
            writer.append(
                "message.accepted",
                key="message-legacy-message",
                data={"message_id": request.request_id, "request_hash": _digest(data), **body.model_dump(mode="json")},
            )
        assert service.snapshot(session).messages[0].origin == "unknown"
        replay = service.accept(session, request)
        assert replay.inbox_position == service.snapshot(session).messages[0].seq
        assert service.snapshot(session).messages[0].origin == "unknown"
