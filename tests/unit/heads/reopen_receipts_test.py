"""Navigation progress excludes credentials and tells browser requests from independent probes."""

import asyncio
import io
import json
from pathlib import Path

import pytest

from tests.e2e._reopen_server import RECEIPT_TAIL_BYTES, NavigationReceipts, receipt_tail

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


def test_oversized_receipt_file_has_a_fixed_read_budget_and_only_the_last_30_records(tmp_path, monkeypatch):
    path = tmp_path / "requests.jsonl"
    last_records = [json.dumps({"event": "finished", "at": index}) for index in range(40)]
    path.write_bytes(b"old record\n" * 800_000 + ("\n".join(last_records) + "\n").encode())
    reads = []
    original_open = Path.open

    class BoundedFile(io.FileIO):
        def read(self, size=-1):
            assert 0 <= size <= RECEIPT_TAIL_BYTES
            reads.append(size)
            assert sum(reads) <= RECEIPT_TAIL_BYTES
            return super().read(size)

        def readlines(self, hint=-1):
            pytest.fail("Receipt diagnostics must not read an unbounded number of lines")

    def guarded_open(target, *args, **kwargs):
        return BoundedFile(target, "rb") if target == path else original_open(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    result = receipt_tail(path, 0)

    assert result.splitlines() == last_records[-30:]
    assert len(result.encode()) <= RECEIPT_TAIL_BYTES
    assert reads


def test_receipt_tail_excludes_earlier_invocations_and_incomplete_records(tmp_path):
    path = tmp_path / "requests.jsonl"
    earlier = b'{"event":"request","at":1}\n'
    current = b'{"event":"finished","at":2}\n'
    path.write_bytes(earlier + current + b'{"event":"request"')

    assert receipt_tail(path, len(earlier)) == current.decode().strip()
    assert receipt_tail(path, path.stat().st_size) == ""


def test_receipt_larger_than_the_read_budget_is_discarded_as_incomplete(tmp_path):
    path = tmp_path / "requests.jsonl"
    path.write_bytes(b"x" * (RECEIPT_TAIL_BYTES * 2) + b"\n")

    assert receipt_tail(path, 0) == ""


def test_required_reopen_journey_always_enables_isolated_receipts_without_an_opt_in():
    import ast

    source = Path(__file__).parents[2] / "e2e/web_ui_test.py"
    tree = ast.parse(source.read_text())
    journey = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_a_paired_browser_reopens_signed_in_without_a_link_after_hz_ui_restarts"
    )
    # A top-level assignment within the journey cannot depend on an unconfigured CI environment flag.
    assignments = [node for node in journey.body if isinstance(node, ast.AugAssign)]
    assert any(
        isinstance(node.target, ast.Name)
        and node.target.id == "env"
        and isinstance(node.value, ast.Dict)
        and any(isinstance(key, ast.Constant) and key.value == "HZ_REOPEN_RECEIPTS" for key in node.value.keys)
        for node in assignments
    )
