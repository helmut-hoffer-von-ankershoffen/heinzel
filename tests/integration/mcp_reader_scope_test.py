"""Reader authorization across a real loopback MCP HTTP connection."""

import socket
import threading
import time

import anyio
import httpx2
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from heinzel import Module
from heinzel.heads import mcp
from heinzel.heads._mcp_tokens import McpTokens

pytestmark = pytest.mark.integration


def test_loopback_reader_cannot_invoke_write(make_kernel, tmp_path):
    changes = []
    notes = Module("notes", "Fictional test operations.")

    @notes.operation
    def show() -> str:
        """Read the fictional note."""
        return "fictional note"

    @notes.operation(read_only=False)
    def publish(title: str) -> str:
        """Publish a fictional note."""
        changes.append(title)
        return f"published {title}"

    kernel = make_kernel(builtins=True, notes=notes)
    tokens = McpTokens(tmp_path / "tokens")
    _, reader = tokens.create("fictional-reader", "read")
    _, writer = tokens.create("fictional-writer", "write")
    head, security = mcp.http_server(kernel, "127.0.0.1", tokens=tokens)
    app = head.streamable_http_app(transport_security=security)
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/mcp"
        runner = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
        thread = threading.Thread(target=runner.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not runner.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert runner.started

            anyio.run(exercise, url, reader, writer, changes)
        finally:
            runner.should_exit = True
            thread.join(5)
            assert not thread.is_alive()
    finally:
        listener.close()


async def exercise(url, reader, writer, changes):
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {reader}"}, trust_env=False) as client,
        streamable_http_client(url, http_client=client) as (read, write, *_),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        shown = await session.call_tool("notes_show", {})
        assert not shown.is_error
        assert shown.content[0].text == "fictional note"
        refused = await session.call_tool("notes_publish", {"title": "first"})
        assert refused.is_error
        assert "may only read" in refused.content[0].text
        assert changes == []
    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {writer}"}, trust_env=False) as client,
        streamable_http_client(url, http_client=client) as (read, write, *_),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("notes_publish", {"title": "second"})
        assert not result.is_error
        assert result.content[0].text == "published second"
        assert changes == ["second"]
