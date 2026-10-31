"""A stale selected revision cannot reach the synthetic conditional writer."""

import pytest
from fastapi.testclient import TestClient

from tests.integration.captured_write_chat_test import _STATE, _kernel_and_client

pytestmark = pytest.mark.integration


def test_stale_before_human_confirmation_has_no_effect(tmp_path, monkeypatch):
    _STATE.update(revision="one", writes=0, opens=0, lose_ack=False)
    _kernel, _current, app = _kernel_and_client(tmp_path, monkeypatch)
    auth = {"Authorization": "Bearer fixture-token"}
    with TestClient(app, base_url="http://localhost") as client:
        session = client.post("/api/heinzel/new-chat", headers=auth, json={"request_id": "new"}).json()["session_id"]
        proposal = {
            "session_id": session,
            "request_id": "draft-one",
            "account": "account-one",
            "source": "source-one",
            "reference": "fixture://document/one",
            "operation": "insert-draft",
            "section": "summary",
            "base_revision": "one",
            "write_scope": "fixture-write",
            "content": "private exact replacement",
        }
        assert (
            client.post("/api/heinzel/propose-captured-write", headers=auth, json={"proposal": proposal}).status_code
            == 200
        )
        staged = client.post(
            "/api/system/runner/chat/propose-captured-write",
            headers=auth,
            params={"durable_session": session, "request_id": "draft-one"},
        ).json()
        action = next(item for item in staged["actions"] if item["operation"] == "execute-captured-write")
        _STATE["revision"] = "changed"
        reply = client.post(
            "/api/system/runner/chat/confirm", headers=auth, params={"session": staged["id"], "action": action["id"]}
        )
        assert reply.status_code == 400
        assert _STATE["writes"] == 0
        outcome = client.get(
            "/api/heinzel/captured-write-status",
            headers=auth,
            params={"session_id": session, "request_id": "draft-one"},
        )
        assert outcome.status_code == 200
        assert outcome.json() == {"status": "draft", "receipt": None}
