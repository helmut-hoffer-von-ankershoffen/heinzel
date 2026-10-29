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
