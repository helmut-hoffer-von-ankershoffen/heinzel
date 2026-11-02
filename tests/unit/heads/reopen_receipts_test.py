"""The opt-in browser diagnostic never records credentials or request content."""

import asyncio
import json

import pytest

from tests.e2e._reopen_server import Receipts

pytestmark = pytest.mark.unit


def test_request_receipts_keep_progress_and_drop_headers_queries_and_bodies(tmp_path):
    path = tmp_path / "requests.jsonl"
    messages = [
        {"type": "http.response.start", "status": 200, "headers": [(b"x-private", b"PRIVATE_HEADER")]},
        {"type": "http.response.body", "body": b"PRIVATE_BODY"},
    ]
    delivered = []

    async def app(scope, receive, send):
        for message in messages:
            await send(message)

    async def receive():
        return {"type": "http.request", "body": b"PRIVATE_REQUEST"}

    async def send(message):
        delivered.append(message)

    asyncio.run(
        Receipts(app, path)(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "query_string": b"token=PRIVATE_QUERY",
                "headers": [(b"authorization", b"Bearer PRIVATE_AUTH")],
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
    assert all(set(record) == {"at", "event", "method", "path", "client_port", "status"} for record in records)
    assert delivered == messages
