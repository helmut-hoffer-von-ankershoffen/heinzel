"""The web app's notice stream (ADR 0041): server-sent events behind the session token, and the notify operations."""

import json

import pytest
from fastapi.testclient import TestClient

from heinzel import Delivery, NoticeKind, Notifications
from heinzel.heads import api, web
from heinzel.heads._exposure import exposed

pytestmark = pytest.mark.integration

TOKEN = "s3ssion-token-for-tests"
BASE = "http://127.0.0.1:8000"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
STREAM = "/api/system/notify/stream"


@pytest.fixture
def kernel(make_kernel):
    return make_kernel(builtins=True)


@pytest.fixture
def client(kernel):
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=None), base_url=BASE)


def events(text: str) -> list[tuple[str, dict]]:
    found = []
    for block in text.split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(":"))
        if "event" in lines:
            found.append((lines["event"], json.loads(lines["data"])))
    return found


def emit(kernel, key: str = "draft:me:weekly"):
    return kernel.resolve(Notifications).emit(
        NoticeKind.DRAFT, "Draft ready", "Read it.", route="/contracts/me%3Aweekly", command="hz", key=key, urgent=True
    )


def test_the_stream_needs_the_token_and_refuses_other_sites(client):
    for headers in ({}, {"Authorization": "Bearer wrong"}):
        assert client.get(STREAM, params={"once": True}, headers=headers).status_code == 401
    assert client.get(f"{STREAM}?once=true&token={TOKEN}").status_code == 401  # never a token in the URL
    assert (
        client.get(STREAM, params={"once": True}, headers={**AUTH, "Origin": "http://evil.example.test"}).status_code
        == 403
    )
    assert (
        client.get(STREAM, params={"once": True}, headers={**AUTH, "Sec-Fetch-Site": "cross-site"}).status_code == 403
    )


def test_a_tab_that_pops_up_gets_the_due_notices_and_holds_the_daemon_back(client, kernel):
    notice = emit(kernel)
    response = client.get(STREAM, params={"once": True, "popups": True}, headers=AUTH)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    found = events(response.text)
    assert ("notice", json.loads(notice.model_dump_json())) in found
    assert ("changed", {"latest": notice.id}) in found
    assert kernel.resolve(Notifications).post_native() == 0  # the open tab shows it


def test_a_tab_that_cant_pop_up_only_hears_that_the_list_changed(client, kernel):
    notice = emit(kernel)
    found = events(client.get(STREAM, params={"once": True}, headers=AUTH).text)
    assert found == [("changed", {"latest": notice.id})]
    assert kernel.resolve(Notifications).store.watched_at() is None


def test_the_app_acknowledges_what_it_showed_and_lists_notices(client, kernel):
    notice = emit(kernel)
    listed = client.get("/api/system/notify/list", headers=AUTH).json()
    assert [n["id"] for n in listed["notices"]] == [notice.id]
    acked = client.post("/api/system/notify/ack", params={"ids": notice.id}, headers=AUTH).json()
    assert acked == {"delivered": [notice.id]}
    assert kernel.resolve(Notifications).recent()[0].delivered_by is Delivery.WEB
    assert client.post("/api/system/notify/ack", params={"ids": "a b"}, headers=AUTH).status_code == 400


def test_notices_are_not_for_the_unauthenticated_rest_head(kernel):
    paths = {item.path for item in exposed(kernel, api.HEAD)}
    assert not any(path[:2] == ("system", "notify") for path in paths)
    rest = TestClient(api.build(kernel, port=8000), base_url=BASE)
    assert rest.get("/system/notify/list").status_code == 404
    assert rest.get("/system/notify/stream").status_code == 404
