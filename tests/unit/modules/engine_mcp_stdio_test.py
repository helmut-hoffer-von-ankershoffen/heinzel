"""The engine's minimal MCP stdio client against small scripted servers: answers, refusals and failures (ADR 0159)."""

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest

from heinzel.modules.ai.access import _mcp_stdio
from heinzel.modules.ai.access._mcp_stdio import McpError, StdioMcp, tool_name

pytestmark = pytest.mark.unit

PRELUDE = textwrap.dedent(
    """
    import json, sys, time
    def send(m):
        sys.stdout.write(json.dumps(m) + "\\n"); sys.stdout.flush()
    def reply(m, result):
        send({"jsonrpc": "2.0", "id": m["id"], "result": result})
    """
)

SERVER = PRELUDE + textwrap.dedent(
    """
    for line in sys.stdin:
        m = json.loads(line)
        method = m.get("method")
        if method == "initialize":
            send(["not", "an", "object"])
            send({"jsonrpc": "2.0", "id": 999, "result": {}})
            reply(m, {"capabilities": {}})
        elif method == "tools/list":
            cursor = m["params"].get("cursor")
            tools = [{"name": "second"}] if cursor else [{"name": "first"}, {"no": "name"}]
            reply(m, {"tools": tools, **({} if cursor else {"nextCursor": "page-2"})})
        elif method == "tools/call":
            name = m["params"]["name"]
            if name == "fails":
                reply(m, {"isError": True, "content": [{"type": "text", "text": "bad input"}]})
            elif name == "mixed":
                reply(m, {"content": [{"type": "image", "data": "x"}, {"type": "text", "text": "caption"}]})
            elif name == "structured":
                reply(m, {"content": [], "structuredContent": {"n": 1}})
            elif name == "huge":
                reply(m, {"content": [{"type": "text", "text": "x" * 200}]})
            elif name == "refused":
                send({"jsonrpc": "2.0", "id": m["id"], "error": {"code": -1, "message": "no such tool"}})
            elif name == "odd":
                send({"jsonrpc": "2.0", "id": m["id"], "result": "not an object"})
            elif name == "slow":
                time.sleep(5)
            elif name == "quit":
                sys.exit(0)
            elif name == "flood":
                sys.stdout.write("y" * 300 + "\\n"); sys.stdout.flush()
    """
)


def server(tmp_path: Path, body: str = SERVER) -> StdioMcp:
    script = tmp_path / "server.py"
    script.write_text(body, encoding="utf-8")
    return StdioMcp("chat", [sys.executable, str(script)], {"EXTRA": "1"}, tmp_path)


async def started(tmp_path: Path, body: str = SERVER) -> StdioMcp:
    mcp = server(tmp_path, body)
    await mcp.start()
    return mcp


def test_tools_are_listed_across_pages_and_named_as_claude_code_names_them(tmp_path: Path) -> None:
    async def go() -> list[str]:
        mcp = await started(tmp_path)
        try:
            return [t["name"] for t in await mcp.tools()]
        finally:
            await mcp.stop()

    assert asyncio.run(go()) == ["first", "second"]
    assert tool_name("chat", "hz.search/all") == "mcp__chat__hz_search_all"
    assert len(tool_name("chat", "x" * 300)) == 128


def test_calls_return_text_and_say_what_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_mcp_stdio, "MAX_RESULT_CHARS", 50)

    async def go() -> list[str]:
        mcp = await started(tmp_path)
        try:
            return [await mcp.call(name, {}) for name in ("fails", "mixed", "structured", "huge", "odd")]
        finally:
            await mcp.stop()

    fails, mixed, structured, huge, odd = asyncio.run(go())
    assert fails == "Error: bad input"
    assert mixed == "[image content]\ncaption"
    assert structured == '{"n": 1}'
    assert huge == "x" * 50 + "\n(cut short)"
    assert odd == '""'


def test_a_refusal_a_stopped_server_and_a_flood_are_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_mcp_stdio, "MAX_MESSAGE_BYTES", 100)

    async def go() -> list[str]:
        problems = []
        for name in ("refused", "flood", "quit"):
            mcp = await started(tmp_path)
            try:
                await mcp.call(name, {})
            except McpError as exc:
                problems.append(str(exc))
            finally:
                await mcp.stop()
        return problems

    refused, flood, quit_ = asyncio.run(go())
    assert refused == "the MCP server chat refused: no such tool"
    assert flood == "the MCP server chat sent a message over 100 bytes"
    assert quit_ == "the MCP server chat stopped"


def test_a_silent_server_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_mcp_stdio, "REQUEST_TIMEOUT", 0.3)

    async def go() -> str:
        mcp = await started(tmp_path)
        try:
            await mcp.call("slow", {})
        except McpError as exc:
            return str(exc)
        finally:
            await mcp.stop()
        return ""

    assert asyncio.run(go()) == "the MCP server chat didn't answer tools/call within 0.3s"


def test_a_server_that_can_t_start_or_lists_nothing_is_an_error(tmp_path: Path) -> None:
    lists_nothing = PRELUDE + textwrap.dedent(
        """
        for line in sys.stdin:
            m = json.loads(line)
            if "id" in m:
                reply(m, {})
        """
    )

    async def go() -> list[str]:
        problems = []
        missing = StdioMcp("chat", [str(tmp_path / "no-such-server")], {}, tmp_path)
        try:
            await missing.start()
        except McpError as exc:
            problems.append(str(exc))
        mcp = await started(tmp_path, lists_nothing)
        try:
            await mcp.tools()
        except McpError as exc:
            problems.append(str(exc))
        finally:
            await mcp.stop()
        try:
            await mcp.call("x", {})
        except McpError as exc:
            problems.append(str(exc))
        return problems

    cannot, nothing, stopped = asyncio.run(go())
    assert cannot.startswith("the MCP server chat couldn't start")
    assert nothing == "the MCP server chat listed no tools"
    assert stopped == "the MCP server chat is not running"


def test_a_server_that_ignores_its_closed_input_is_terminated_then_killed(tmp_path: Path) -> None:
    stubborn = PRELUDE + textwrap.dedent(
        """
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        m = json.loads(sys.stdin.readline())
        reply(m, {})
        time.sleep(60)
        """
    )

    async def go() -> int | None:
        mcp = await started(tmp_path, stubborn)
        process = mcp._process
        await mcp.stop()
        await mcp.stop()  # a second stop is a no-op
        return process.returncode if process else None

    assert asyncio.run(go()) == -9
