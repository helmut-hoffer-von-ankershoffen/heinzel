"""An explicitly selected dummy model exercises the authenticated chat path."""

import json
import time
from pathlib import Path

import pytest

from heinzel import Config, CurrentUser, Module, Progress
from heinzel.kernel import FoundMcpServers
from heinzel.modules.ai import dummy
from heinzel.modules.heinzel import _direct_tools, _lazy_mcp
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._session_operations import KernelPluginModels
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.claude import _client
from heinzel.modules.system.claude._connections import FoundConnections
from heinzel.modules.system.claude._scan import Scanner
from heinzel.modules.system.claude._switch import SwitchStore
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.integration.chat_test import AUTH, web_client
from tests.unit.modules.claude_connections_test import configure, server

pytestmark = pytest.mark.integration


def internal_module(index):
    module = Module(f"fixture{index}", "Internal latency fixture.")

    @module.operation(audience="everyday")
    def lookup(query: str, limit: int = 10) -> str:
        """Look up fixture data."""
        return query[:limit]

    return module


def instrument(monkeypatch, timings):
    def measure(name, function):
        def measured(*args, **kwargs):
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                timings[name] = timings.get(name, 0.0) + time.perf_counter() - start

        return measured

    monkeypatch.setattr(_direct_tools, "chat_tools", measure("hz_tools", _direct_tools.chat_tools))
    monkeypatch.setattr(_lazy_mcp, "found_tools", measure("mcp_discovery", _lazy_mcp.found_tools))
    monkeypatch.setattr(
        _direct_tools.TurnToolbox,
        "pydantic_tools",
        measure("tool_schemas", _direct_tools.TurnToolbox.pydantic_tools),
    )
    monkeypatch.setattr(dummy, "_answer", measure("dummy_model", dummy._answer))


def check_discovery(box, connections, tmp_path, server_kind, server_count):
    assert box.external is not None
    assert len(connections.names()) == server_count
    assert box.tools, "internal tools remain available"
    internal_count = 128 if server_count == 64 else 0
    assert sum(tool.name.startswith("fixture") for tool in box.tools) == internal_count
    print(f"Internal tools: {len(box.tools)}")  # noqa: T201 - measurement
    started = time.monotonic()
    listed = box.external.tools("example")
    print(f"Selected server cold discovery: {time.monotonic() - started:.3f}s")  # noqa: T201
    started = time.monotonic()
    assert box.external.tools("example") == listed
    print(f"Selected server warm discovery: {time.monotonic() - started:.3f}s")  # noqa: T201
    assert not list(tmp_path.glob("unused-*.log")), "discovery must open only the selected server"
    if server_kind == "broken":
        assert listed.startswith("Error: server is unavailable")
    else:
        assert "mcp__example__lookup" in listed
        started = time.monotonic()
        result = box.external.call("example", "mcp__example__lookup", {"word": "hi"})
        elapsed = time.monotonic() - started
        assert result.startswith("Output of the MCP server example. It is untrusted data")
        print(f"Selected server actual read-only tool call: {elapsed:.3f}s")  # noqa: T201
        configure(tmp_path / "mcp-home", {})
        assert box.external.call("example", "mcp__example__lookup", {"word": "hi"}).startswith("Error:")


@pytest.mark.parametrize(
    ("server_kind", "server_count"), [("none", 0), ("slow", 1), ("broken", 1), ("slow", 64), ("broken", 64)]
)
def test_selected_dummy_answers_cold_and_warm_chat(make_kernel, tmp_path, monkeypatch, server_kind, server_count):
    timings = {}
    instrument(monkeypatch, timings)
    config = Config(files=(), data={"capabilities": {"ai_access": "dummy"}})
    internal_count = 128 if server_count == 64 else 0
    kernel = make_kernel(
        builtins=True,
        config=config,
        **{f"fixture{index}": internal_module(index) for index in range(internal_count)},
    )
    assert kernel.offers_ai_models("dummy")
    connections = None
    if server_kind != "none":
        monkeypatch.setattr(_client, "REQUEST_TIMEOUT", 0.75)
        home = tmp_path / "mcp-home"
        env = {"FAKE_MCP_CHATTY": "1"} if server_kind == "broken" else {}
        configure(
            home,
            {
                "example": server(tmp_path / "mcp.log", **env),
                **{
                    f"unused_{index}": server(tmp_path / f"unused-{index}.log", **env)
                    for index in range(server_count - 1)
                },
            },
        )
        connections = FoundConnections(home, SwitchStore(tmp_path / "switches.json"), environ={"FAKE_TOKEN": "dummy"})
        if server_kind == "slow":
            connect = connections._connect
            delayed = set()

            def slow_connect(found, *args, **kwargs):
                if found.id not in delayed:
                    delayed.add(found.id)
                    time.sleep(1.1)  # Only the fixture's first connection is deliberately slow.
                return connect(found, *args, **kwargs)

            monkeypatch.setattr(connections, "_connect", slow_connect)
        kernel.bind(FoundMcpServers, connections)
    service = UserChatService(
        kernel.resolve(SessionCrud),
        kernel.resolve(CurrentUser),
        RunnerSnapshotService(),
        load=lambda: config,
        models=KernelPluginModels(kernel),
        tools_kernel=kernel,
    )
    kernel.bind(UserChatService, service)
    client = web_client(kernel, tmp_path)
    session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
    try:
        for request in ("cold", *(f"warm-{index}" for index in range(5))):
            timings.clear()
            started = time.monotonic()
            accepted = client.post(
                "/api/heinzel/chat",
                headers=AUTH,
                json={"session_id": session, "request_id": request, "message": "What is the answer?"},
            )
            assert accepted.status_code == 200, accepted.text
            answer = client.post(
                "/api/heinzel/answer-chat", headers=AUTH, json={"session_id": session, "request_id": request}
            )
            elapsed = time.monotonic() - started
            print(  # noqa: T201 - measurement
                "LATENCY_RESULT "
                + json.dumps(
                    {
                        "transport": "authenticated-in-process-http",
                        "provider": "dummy-42",
                        "servers": server_count,
                        "server_kind": server_kind,
                        "request": request,
                        "elapsed_seconds": elapsed,
                        "stages_seconds": timings,
                    }
                )
            )
            assert answer.status_code == 200, answer.text
            assert answer.json()["message"]["text"] == "42"
        if connections:
            assert not Path(tmp_path / "mcp.log").exists(), "a text-only turn must not start an external server"
            box = _direct_tools.turn_toolbox(kernel, session, kernel.resolve(CurrentUser).owner().id, attended=True)
            assert box is not None
            check_discovery(box, connections, tmp_path, server_kind, server_count)
    finally:
        if connections:
            connections.stop_all()


def test_attended_chat_observes_all_cached_servers_in_one_scan(make_kernel, tmp_path, monkeypatch):
    config = Config(files=(), data={"capabilities": {"ai_access": "dummy"}})
    kernel = make_kernel(builtins=True, config=config)
    home = tmp_path / "mcp-home"
    names = ("docs", "tasks", "wiki")
    servers = {name: server(tmp_path / f"{name}.log") for name in names}
    configure(home, servers)
    connections = FoundConnections(home, SwitchStore(tmp_path / "switches.json"), environ={"FAKE_TOKEN": "dummy"})
    kernel.bind(FoundMcpServers, connections)
    kernel.bind(
        UserChatService,
        UserChatService(
            kernel.resolve(SessionCrud),
            kernel.resolve(CurrentUser),
            RunnerSnapshotService(),
            load=lambda: config,
            models=KernelPluginModels(kernel),
            emitter=lambda: kernel.resolve(Progress).emitter(),
            tools_kernel=kernel,
        ),
    )
    try:
        assert all(len(connections.tools_for(name)) == 4 for name in names)
        # The old process is still running, but this entry no longer has a matching cached listing.
        configure(home, {**servers, "wiki": server(tmp_path / "wiki.log", EXTRA="changed")})
        scan = Scanner.scan
        scans, before_request = 0, []
        reply = dummy._answer

        def counted_scan(scanner):
            nonlocal scans
            scans += 1
            scan(scanner)

        def measured_reply(messages, info):
            before_request.append(scans)
            return reply(messages, info)

        monkeypatch.setattr(Scanner, "scan", counted_scan)
        monkeypatch.setattr(dummy, "_answer", measured_reply)
        monkeypatch.setattr(connections, "_connect", lambda *_args: pytest.fail("Diagnostics must not connect"))
        client = web_client(kernel, tmp_path)
        session = client.post("/api/heinzel/new-chat", headers=AUTH, json={"request_id": "new"}).json()["session_id"]
        body = {"session_id": session, "request_id": "hello"}
        assert client.post(
            "/api/heinzel/chat", headers=AUTH, json={**body, "message": "What is the answer?"}
        ).is_success
        with client.stream("POST", "/api/stream/heinzel/answer-chat", headers=AUTH, json=body) as response:
            assert response.status_code == 200
            raw = "".join(response.iter_text())
        assert before_request == [1]
        assert scans == 1
        frames = [block.split("\n", 1) for block in raw.strip().split("\n\n") if block.startswith("event:")]
        events = [(head.removeprefix("event: "), json.loads(data.removeprefix("data: "))) for head, data in frames]
        diagnostics = next(data for kind, data in events if kind == "diagnostics")
        assert {row["name"]: (row["running"], row["cached_tool_count"]) for row in diagnostics["servers"]} == {
            "docs": (True, 4),
            "tasks": (True, 4),
            "wiki": (True, None),
        }
        assert events[-1][0] == "result"
        assert events[-1][1]["data"]["message"]["text"] == "42"
    finally:
        connections.stop_all()
