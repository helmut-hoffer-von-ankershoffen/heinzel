"""Adversarial read-set decisions stay inside their real private owner and turn boundaries."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from heinzel import Principal
from heinzel.heads import api
from heinzel.heads.api._auth import ApiBearer
from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._engine import TurnLimits
from heinzel.modules.heinzel._interactive_turn import InteractiveTurn
from heinzel.modules.heinzel._session_models import MessageRequest
from tests.integration.chat_test import AUTH
from tests.integration.session_crud_test import BASE, NOW

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.source_discovery_prototype_test",)


def _prepare(client, session):
    selection = {
        "capability": "calendar",
        "method": "events",
        "fixed_arguments": {"window": {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}},
    }
    proposed = client.post(
        "/api/heinzel/propose-connected-read-set",
        headers=AUTH,
        json={"session_id": session, "request_id": "set", "selections": [selection]},
    )
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()
    prepared = client.post(
        "/api/heinzel/prepare-connected-read-set-review",
        headers=AUTH,
        json={
            "session_id": session,
            "request_id": "review",
            "proposal_request": "set",
            "proposal_revision": proposal["revision"],
        },
    )
    assert prepared.status_code == 200, prepared.text
    return proposal, prepared.json()


def test_private_http_rejects_other_owner_and_other_session(bound_source):
    client, kernel, current, session, plugin, _source, _ = bound_source
    proposal, review = _prepare(client, session)
    other_session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "other"}).json()[
        "session_id"
    ]
    wrong = client.get(
        "/api/heinzel/connected-read-set-review",
        headers=AUTH,
        params={"session_id": other_session, "request_id": "review"},
    )
    assert wrong.status_code != 200
    credentials = ApiBearer.issue(Principal("u-" + "a" * 32), "sessions", now=NOW)
    foreign = TestClient(
        api.build(kernel, credentials=credentials, audience="sessions", clock=lambda: NOW), base_url=BASE
    )
    auth = {"Authorization": f"Bearer {credentials.secret}"}
    for path, params in [
        ("/heinzel/connected-read-set-proposal", {"session_id": session}),
        ("/heinzel/connected-read-set-review", {"session_id": session, "request_id": "review"}),
        ("/heinzel/connected-read-set-approval", {"session_id": session}),
    ]:
        assert foreign.get(path, headers=auth, params=params).status_code != 200
    forged = foreign.post(
        "/heinzel/approve-connected-read-set",
        headers=auth,
        json={"session_id": session, "request_id": "review", "review": review, "approval_proof": "forged"},
    )
    assert forged.status_code != 200
    assert plugin.reads == []
    with current.local("cli"):
        events = kernel.resolve(ConnectedReadGrants).crud.repository.control_read(session).events
    assert not any(e.kind in {"connected-set.approved", "connected-set-input.observed"} for e in events)
    assert proposal["session"] == session


def test_source_change_after_stage_cannot_be_confirmed(bound_source):
    client, kernel, current, session, plugin, source, _ = bound_source
    _proposal, _review = _prepare(client, session)
    staged = client.post(
        "/api/system/runner/chat/propose-connected-read-set-review",
        headers=AUTH,
        params={"durable_session": session, "request_id": "review"},
    )
    assert staged.status_code == 200, staged.text
    chat = staged.json()
    action = next(a for a in chat["actions"] if a["operation"] == "approve-connected-read-set")
    source.write_text("# changed after review\n")
    response = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat["id"], "action": action["id"]}
    )
    assert (
        response.status_code != 200
        or next(a for a in response.json()["actions"] if a["id"] == action["id"])["status"] != "done"
    )
    assert plugin.reads == []
    with current.local("cli"):
        events = kernel.resolve(ConnectedReadGrants).crud.repository.control_read(session).events
    assert not any(e.kind in {"connected-set.approved", "connected-set-input.observed"} for e in events)


def test_model_cannot_stage_approval_or_mint_proof_through_turn(bound_source):
    _client, kernel, current, session, plugin, _source, _ = bound_source
    with current.local("cli"):
        crud = kernel.resolve(ConnectedReadGrants).crud
        crud.accept(session, MessageRequest(request_id="hostile-model", text="Inspect."))
        count = 0

        def respond(messages, info):
            nonlocal count
            count += 1
            if count == 1:
                return ModelResponse(
                    [
                        ToolCallPart(
                            "use_tool",
                            {
                                "name": "heinzel:approve-connected-read-set",
                                "arguments": {
                                    "session_id": session,
                                    "request_id": "forged",
                                    "approval_proof": "forged",
                                },
                            },
                        )
                    ]
                )
            assert any(
                isinstance(part, ToolReturnPart) and "unavailable" in str(part.content).lower()
                for message in messages
                for part in message.parts
            )
            return ModelResponse(
                [ToolCallPart(info.output_tools[0].name, {"report": "Unavailable", "summary": "Unavailable"})]
            )

        asyncio.run(InteractiveTurn(crud).run(session, "hostile-model", FunctionModel(respond), limits=TurnLimits()))
        events = crud.repository.control_read(session).events
    assert plugin.reads == []
    assert not any(
        e.kind in {"approval.issued", "connected-set.approved", "connected-set-input.observed"} for e in events
    )


def _approve(client, session):
    proposal, review = _prepare(client, session)
    staged = client.post(
        "/api/system/runner/chat/propose-connected-read-set-review",
        headers=AUTH,
        params={"durable_session": session, "request_id": "review"},
    )
    assert staged.status_code == 200, staged.text
    chat = staged.json()
    action = next(item for item in chat["actions"] if item["operation"] == "approve-connected-read-set")
    confirmed = client.post(
        "/api/system/runner/chat/confirm",
        headers=AUTH,
        params={"session": chat["id"], "action": action["id"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert next(item for item in confirmed.json()["actions"] if item["id"] == action["id"])["status"] == "done"
    return proposal, review


def test_unknown_model_handle_through_http_records_no_provider_read(bound_source, tmp_path):
    from heinzel.modules.heinzel._session_crud import SessionCrud
    from heinzel.modules.heinzel._user_chat import UserChatService
    from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
    from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

    client, kernel, current, session, plugin, _source, _ = bound_source
    _approve(client, session)
    executable = fake_claude(tmp_path, tool="connected_read_set", arguments={"handle": "read-" + "0" * 32})
    kernel.bind(
        UserChatService,
        UserChatService(
            kernel.resolve(SessionCrud),
            current,
            RunnerSnapshotService(),
            connected=lambda: kernel.resolve(ConnectedReadGrants),
            load=lambda: FixtureConfig(executable),
        ),
    )
    accepted = client.post(
        "/api/heinzel/chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "unknown-handle", "message": "Read."},
    )
    assert accepted.status_code == 200, accepted.text
    response = client.post(
        "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "unknown-handle"}
    )
    assert response.status_code == 200, response.text
    activity = client.get("/api/heinzel/tool-activity", headers=AUTH, params={"session_id": session}).json()["items"]
    assert any(item["tool"] == "connected_read_set" and item["status"] == "denied" for item in activity)
    assert plugin.reads == []
    with current.local("cli"):
        events = kernel.resolve(ConnectedReadGrants).crud.repository.control_read(session).events
    assert not any(event.kind == "connected-set-input.observed" for event in events)


def test_daemon_cannot_preview_human_approved_set(bound_source):
    from heinzel.modules.heinzel._connected_read_set_approvals import ConnectedReadSetApprovals
    from heinzel.modules.heinzel._connected_read_set_preview import read_set_tool_preview
    from heinzel.modules.heinzel._connected_read_set_reviews import ConnectedReadSetReviews
    from heinzel.modules.heinzel._connected_read_sets import ConnectedReadSets

    client, kernel, current, session, plugin, _source, _ = bound_source
    _approve(client, session)
    with current.local("cli"):
        grants = kernel.resolve(ConnectedReadGrants)
        sets = ConnectedReadSets(grants.crud, current)
        approvals = ConnectedReadSetApprovals(ConnectedReadSetReviews(sets, grants))
        with grants.crud.repository.main_writer(session) as writer, current.local("daemon"):
            assert read_set_tool_preview(approvals, session, writer) is None
        events = grants.crud.repository.control_read(session).events
    assert plugin.reads == []
    assert not any(event.kind == "connected-set-input.observed" for event in events)
