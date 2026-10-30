"""Owner-reviewed fixed read sets pass through the real private confirmation and turn boundary."""

import json

import pytest

from heinzel.modules.heinzel._connected_grants import ConnectedReadGrants
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.integration.chat_test import AUTH
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.integration
pytest_plugins = ("tests.integration.source_discovery_prototype_test",)


def test_read_set_context_is_owner_private(bound_source):
    client, _kernel, current, session, _plugin, _source, _sources = bound_source
    context = client.get("/api/heinzel/connected-read-set-context", headers=AUTH, params={"session_id": session})
    assert context.status_code == 200, context.text
    with current.local("cli"):
        assert context.json() == {"session_id": session, "owner": current.owner().id}
    assert client.get("/api/heinzel/connected-read-set-context", params={"session_id": session}).status_code == 401


def test_owner_reviews_set_confirms_and_reads_one_host_selected_handle(bound_source, tmp_path):
    client, kernel, current, session, plugin, _source, _sources = bound_source
    window = {"since": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"}
    body = {
        "session_id": session,
        "request_id": "set-proposal",
        "selections": [{"capability": "calendar", "method": "events", "fixed_arguments": {"window": window}}],
    }
    proposed = client.post("/api/heinzel/propose-connected-read-set", headers=AUTH, json=body)
    assert proposed.status_code == 200, proposed.text
    proposal = proposed.json()
    assert plugin.reads == []
    prepared = client.post(
        "/api/heinzel/prepare-connected-read-set-review",
        headers=AUTH,
        json={
            "session_id": session,
            "request_id": "set-review",
            "proposal_request": "set-proposal",
            "proposal_revision": proposal["revision"],
        },
    )
    assert prepared.status_code == 200, prepared.text
    review = prepared.json()
    denied = client.post(
        "/api/heinzel/approve-connected-read-set",
        headers=AUTH,
        json={"session_id": session, "request_id": "set-review", "review": review, "approval_proof": "invalid"},
    )
    assert denied.status_code != 200
    assert plugin.reads == []
    stage = client.post(
        "/api/system/runner/chat/propose-connected-read-set-review",
        headers=AUTH,
        params={"durable_session": session, "request_id": "set-review"},
    )
    assert stage.status_code == 200, stage.text
    chat = stage.json()
    action = next(item for item in chat["actions"] if item["operation"] == "approve-connected-read-set")
    assert action["arguments"]["review"] == review
    assert "approval_proof" not in action["arguments"]
    confirmed = client.post(
        "/api/system/runner/chat/confirm", headers=AUTH, params={"session": chat["id"], "action": action["id"]}
    )
    assert confirmed.status_code == 200, confirmed.text
    assert next(item for item in confirmed.json()["actions"] if item["id"] == action["id"])["status"] == "done"
    assert plugin.reads == []
    projected = client.get("/api/heinzel/connected-read-set-approval", headers=AUTH, params={"session_id": session})
    assert projected.status_code == 200, projected.text
    assert projected.json() == review
    handle = proposal["entries"][0]["handle"]
    executable = fake_claude(tmp_path, tool="connected_read_set", arguments={"handle": handle})
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
        json={"session_id": session, "request_id": "set-turn", "message": "Read the approved source."},
    )
    assert accepted.status_code == 200, accepted.text
    answer = client.post(
        "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "set-turn"}
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["message"]["text"] == "A real recorded reply"
    assert plugin.reads == [True]
    payload = json.loads((tmp_path / "prompt-input").read_text())
    assert handle in json.dumps(payload)
    activity = client.get("/api/heinzel/tool-activity", headers=AUTH, params={"session_id": session})
    assert activity.status_code == 200, activity.text
    assert [item["status"] for item in activity.json()["items"] if item["tool"] == "connected_read_set"] == [
        "requested",
        "completed",
    ]
    assert handle not in activity.text
    with current.local("cli"):
        grants = kernel.resolve(ConnectedReadGrants)
        events = grants.crud.repository.control_read(session).events
        receipt = next(event for event in events if event.kind == "connected-set-input.observed")
        requested = next(
            event
            for event in events
            if event.kind == "interactive.tool"
            and event.data.get("tool") == "connected_read_set"
            and event.data.get("status") == "requested"
        )
        assert requested.data["handle"] == handle
        assert requested.turn == receipt.turn
        assert grants.tool_schema(session) is None


def test_ordinary_chat_can_suggest_bounded_set_without_provider_access(bound_source, tmp_path):
    client, kernel, current, session, plugin, _source, _sources = bound_source
    suggestions = [
        {"capability": "calendar", "method": "events", "fixed_arguments": {"window": "example"}},
        {"capability": "documents", "method": "read", "fixed_arguments": {"id": "example"}},
    ]
    executable = fake_claude(
        tmp_path, tool="source-set:propose", arguments={"expected_revision": 0, "entries": suggestions}
    )
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
        json={"session_id": session, "request_id": "suggest", "message": "Suggest the sources."},
    )
    assert accepted.status_code == 200, accepted.text
    answer = client.post(
        "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "suggest"}
    )
    assert answer.status_code == 200, answer.text
    suggestion = client.get(
        "/api/heinzel/latest-source-set-suggestion-view", headers=AUTH, params={"session_id": session}
    )
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["entries"] == suggestions
    assert plugin.reads == []
    proposal = client.get("/api/heinzel/connected-read-set-proposal", headers=AUTH, params={"session_id": session})
    assert proposal.status_code == 200
    assert proposal.json() is None
