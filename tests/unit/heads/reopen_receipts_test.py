"""Navigation progress excludes credentials and tells browser requests from independent probes."""

import asyncio
import json

import pytest

from tests.e2e._reopen_server import NavigationReceipts

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("navigation", [True, False])
def test_receipts_keep_navigation_progress_and_exclude_request_content(tmp_path, navigation):
    path = tmp_path / "requests.jsonl"
    messages = [
        {"type": "http.response.start", "status": 200, "headers": [(b"x-private", b"PRIVATE_HEADER")]},
        {"type": "http.response.body", "body": b"PRIVATE_BODY", "more_body": True},
        {"type": "http.response.body", "body": b"PRIVATE_FINAL"},
    ]
    delivered = []

    async def app(scope, receive, send):
        for message in messages:
            await send(message)

    async def receive():
        return {"type": "http.request", "body": b"PRIVATE_REQUEST"}

    async def send(message):
        delivered.append(message)

    headers = [(b"authorization", b"Bearer PRIVATE_AUTH")]
    if navigation:
        headers.append((b"sec-fetch-dest", b"document"))
    asyncio.run(
        NavigationReceipts(app, path)(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "query_string": b"token=PRIVATE_QUERY",
                "headers": headers,
                "client": ("127.0.0.1", 12345),
            },
            receive,
            send,
        )
    )
    text = path.read_text(encoding="utf-8")
    assert "PRIVATE" not in text
    records = [json.loads(line) for line in text.splitlines()]
    assert [(record["event"], record["status"]) for record in records] == [
        ("request", None),
        ("response", 200),
        ("finished", None),
    ]
    assert all(set(record) == {"at", "event", "navigation", "client_port", "status"} for record in records)
    assert all(record["navigation"] is navigation and record["client_port"] == 12345 for record in records)
    assert delivered == messages


@pytest.mark.parametrize(
    ("kind", "method", "path"), [("http", "GET", "/private"), ("http", "POST", "/"), ("websocket", None, "/")]
)
def test_other_paths_and_protocols_pass_through_without_receipts(tmp_path, kind, method, path):
    receipt_path = tmp_path / "requests.jsonl"
    scope = {"type": kind, "method": method, "path": path}
    seen = []

    async def app(actual, receive, send):
        seen.append(actual)

    asyncio.run(NavigationReceipts(app, receipt_path)(scope, None, None))

    assert seen == [scope]
    assert not receipt_path.exists()
