"""Owner-private real chat operations with a fake Claude executable and no external calls."""

import pytest

from heinzel import CurrentUser
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from tests.integration.chat_test import AUTH, web_client
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.integration


def test_private_answer_and_activity_operations_use_durable_request(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(tmp_path)
    kernel.bind(UserChatService, UserChatService(crud, current, load=lambda: FixtureConfig(executable)))
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"})
    assert created.status_code == 200, created.text
    session = created.json()["session_id"]
    accepted = client.post(
        "/api/heinzel/chat", headers=AUTH, json={"session_id": session, "request_id": "hello", "message": "Hello"}
    )
    assert accepted.status_code == 200, accepted.text
    request = {"session_id": session, "request_id": "hello"}
    assert client.post("/api/heinzel/answer-chat", json=request).status_code == 401
    answer = client.post("/api/heinzel/answer-chat", headers=AUTH, json=request)
    assert answer.status_code == 200, answer.text
    assert answer.json()["message"]["text"] == "A real recorded reply"
    again = client.post("/api/heinzel/answer-chat", headers=AUTH, json=request)
    assert again.status_code == 200
    assert again.json() == answer.json()
    activity = client.get("/api/heinzel/tool-activity", headers=AUTH, params={"session_id": session})
    assert activity.status_code == 200
    assert activity.json()["items"][0]["status"] == "denied"
    assert (tmp_path / "model-calls").read_text() == "xx"


def test_profile_suggestion_is_owner_private_and_never_applied_by_model(make_kernel, tmp_path):
    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(
        tmp_path,
        tool="profile:propose",
        arguments={
            "expected_revision": 1,
            "name": "Suggested",
            "appearance": {"age": "grown", "build": "round"},
        },
    )
    kernel.bind(UserChatService, UserChatService(crud, current, load=lambda: FixtureConfig(executable)))
    client = web_client(kernel, tmp_path)
    created = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new", "name": "Original"})
    assert created.status_code == 200, created.text
    session = created.json()["session_id"]
    accepted = client.post(
        "/api/heinzel/chat",
        headers=AUTH,
        json={"session_id": session, "request_id": "change-look", "message": "Suggest a different look"},
    )
    assert accepted.status_code == 200, accepted.text
    answered = client.post(
        "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": "change-look"}
    )
    assert answered.status_code == 200, answered.text
    suggestion = client.get("/api/heinzel/latest-profile-proposal", headers=AUTH, params={"session_id": session})
    assert suggestion.status_code == 200, suggestion.text
    assert suggestion.json()["name"] == "Suggested"
    assert suggestion.json()["status"] == "proposed"
    unchanged = client.get("/api/heinzel/session", headers=AUTH, params={"session_id": session})
    assert unchanged.json()["name"] == "Original"
    assert unchanged.json()["profile_revision"] == 1
    assert client.get("/api/heinzel/latest-profile-proposal", params={"session_id": session}).status_code == 401


def test_subscription_quota_error_has_safe_code_and_is_not_replayed(make_kernel, tmp_path):
    import json

    from heinzel.modules.system.runner._process import Outcome

    kernel = make_kernel(builtins=True)
    current = kernel.resolve(CurrentUser)
    crud = kernel.resolve(SessionCrud)
    executable = fake_claude(tmp_path)
    calls = []

    def execute(command, **_kwargs):
        if command[1:] == ["auth", "status", "--json"]:
            return Outcome(
                0, json.dumps({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"}), ""
            )
        if command[1:] == ["--version"]:
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        calls.append(1)
        return Outcome(
            1,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": 429,
                    "api_error": "usage_limit_reached",
                    "result": "sensitive provider details",
                }
            ),
            "",
        )

    kernel.bind(
        UserChatService, UserChatService(crud, current, load=lambda: FixtureConfig(executable), execute=execute)
    )
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "quota"}).json()["session_id"]
    client.post("/api/heinzel/chat", headers=AUTH, json={"session_id": session, "request_id": "hello", "message": "Hi"})
    body = {"session_id": session, "request_id": "hello"}
    response = client.post("/api/heinzel/answer-chat", headers=AUTH, json=body)
    assert response.status_code == 400
    assert response.json()["code"] == "subscription_usage_limit"
    assert "provider settings" in response.json()["error"]
    assert "sensitive" not in response.text
    retry = client.post("/api/heinzel/answer-chat", headers=AUTH, json=body)
    assert retry.status_code == 400
    assert calls == [1]
