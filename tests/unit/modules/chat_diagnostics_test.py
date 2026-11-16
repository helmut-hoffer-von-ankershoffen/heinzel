"""The interactive diagnostics stream contains counts and offered tools, never conversation text."""

import asyncio
import contextlib
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from heinzel.kernel import FoundMcpDiagnostic, FoundMcpServers
from heinzel.modules.heinzel._chat_diagnostics import TurnDiagnostics, prepare_toolbox

pytestmark = pytest.mark.unit


def test_diagnostics_measure_requests_without_sending_message_contents():
    events = []
    diagnostics = TurnDiagnostics(lambda name, data: events.append((name, data.copy())))
    tools = [
        SimpleNamespace(name="mcp__hz__system_doctor", description="Check health"),
        SimpleNamespace(name="mcp__weather__forecast", description="Get a forecast"),
        SimpleNamespace(name="mcp__Internal__query", description="Query external server"),
    ]
    request = SimpleNamespace(
        model_request_parameters=SimpleNamespace(function_tools=tools),
        messages=["private message"],
        model=SimpleNamespace(context_window=1000),
    )
    response = ModelResponse(parts=[TextPart("private answer")], usage=RequestUsage(input_tokens=12))

    async def handler(value):
        assert value is request
        return response

    assert asyncio.run(diagnostics.wrap_model_request(None, request_context=request, handler=handler)) is response
    event, data = events[-1]
    assert event == "diagnostics"
    assert data["requests"] == 1
    assert data["context"] == {
        "messages": 1,
        "input_tokens": 12,
        "window_tokens": 1000,
        "instruction_chars": None,
        "user_parts": 0,
        "system_parts": 0,
        "tool_results": 0,
        "responses": 0,
    }
    assert [(server["name"], server["internal"]) for server in data["servers"]] == [
        ("Internal", True),
        ("weather", False),
        ("Internal", False),
    ]
    assert "private message" not in repr(data)
    assert "private answer" not in repr(data)


def test_provider_error_is_preserved_and_diagnostic_relay_failure_is_ignored():
    def fail_emit(_name, _data):
        msg = "not delivered"
        raise RuntimeError(msg)

    diagnostics = TurnDiagnostics(fail_emit)
    request = SimpleNamespace(
        model_request_parameters=SimpleNamespace(function_tools=[]),
        messages=[],
        model=SimpleNamespace(context_window=None),
    )

    async def handler(_value):
        msg = "provider failed"
        raise ValueError(msg)

    pending = diagnostics.wrap_model_request(None, request_context=request, handler=handler)
    with pytest.raises(ValueError, match="provider failed"):
        asyncio.run(pending)
    assert diagnostics.requests == 1


def test_passive_connection_observation_reads_canonical_metadata_without_connecting():
    class Service:
        def diagnostics(self):
            return [
                FoundMcpDiagnostic(
                    namespace="weather", scope="user", running=True, known_tool_count=7, enabled=True, available=True
                )
            ]

        def tools(self):
            pytest.fail("diagnostics must not discover tools")

    class Kernel:
        def provides(self, _service):
            return True

        def resolve(self, _service):
            return Service()

    diagnostics = TurnDiagnostics(lambda _name, _data: None)
    diagnostics.observe_connections(Kernel())
    assert diagnostics.status == {"weather": ("user", True)}


def test_conflicting_status_names_are_not_attributed_to_either_server():
    rows = [
        FoundMcpDiagnostic(
            namespace="my_server", scope="user", running=True, known_tool_count=2, enabled=True, available=True
        ),
        FoundMcpDiagnostic(
            namespace="MY_SERVER", scope="project", running=False, known_tool_count=None, enabled=True, available=None
        ),
        FoundMcpDiagnostic(
            namespace="weather", scope="user", running=True, known_tool_count=7, enabled=True, available=True
        ),
    ]
    kernel = SimpleNamespace(
        provides=lambda _service: True, resolve=lambda _service: SimpleNamespace(diagnostics=lambda: rows)
    )
    diagnostics = TurnDiagnostics(lambda _name, _data: None)
    diagnostics.observe_connections(kernel)
    assert diagnostics.status == {"weather": ("user", True)}
    assert [item["name"] for item in diagnostics.servers] == ["weather"]


def test_status_failure_does_not_interrupt_diagnostics():
    def fail():
        msg = "unavailable"
        raise RuntimeError(msg)

    kernel = SimpleNamespace(provides=lambda _service: True, resolve=lambda _service: SimpleNamespace(diagnostics=fail))
    diagnostics = TurnDiagnostics(lambda _name, _data: None)
    diagnostics.observe_connections(kernel)
    assert diagnostics.status == {}
    assert diagnostics.servers == []


def test_unavailable_diagnostic_metadata_cannot_fail_a_provider_request():
    diagnostics = TurnDiagnostics(lambda _name, _data: None)
    response = ModelResponse(parts=[TextPart("answer")])

    async def handler(_value):
        return response

    assert asyncio.run(diagnostics.wrap_model_request(None, request_context=object(), handler=handler)) is response


def test_connection_status_obeys_toolbox_owner_boundary(monkeypatch):
    from heinzel.kernel import CurrentUser, FoundMcpServers, McpChatSessions
    from heinzel.modules.heinzel import _direct_tools

    class User:
        def using(self, _value):
            return contextlib.nullcontext()

        def local_read(self, _value):
            return contextlib.nullcontext()

        def principal(self):
            return SimpleNamespace(id="owner")

    class Kernel:
        def provides(self, service):
            return service in {McpChatSessions, FoundMcpServers}

        def resolve(self, service):
            if service is CurrentUser:
                return User()
            if service is McpChatSessions:
                return SimpleNamespace(heinzel_actions=lambda _session: "actions")
            return SimpleNamespace(
                diagnostics=lambda: [
                    FoundMcpDiagnostic(
                        namespace="private",
                        scope="user",
                        running=True,
                        known_tool_count=3,
                        enabled=True,
                        available=True,
                    )
                ]
            )

    monkeypatch.setattr(_direct_tools, "chat_tools", lambda *args, **kwargs: [])
    for principal, expected in [("owner", True), ("other", False)]:
        events = []
        toolbox, diagnostic = asyncio.run(
            prepare_toolbox(
                Kernel(),
                "session",
                principal,
                attended=True,
                emit=lambda _name, data, events=events: events.append(data),
                started=0,
            )
        )
        # The owner argument identifies the turn's principal; installation owner is supplied by CurrentUser.
        assert (toolbox is not None) is expected
        assert bool(diagnostic.status) is expected
        assert ("private" in repr(events)) is expected


def test_tool_source_mapping_handles_double_underscores_and_reserved_name():
    diagnostics = TurnDiagnostics(lambda _name, _data: None)
    diagnostics.tool_sources = {"mcp__my__server__query": ("my__server", "query")}
    diagnostics.status = {"my__server": ("user", True), "hz": ("project", False)}
    request = SimpleNamespace(
        model_request_parameters=SimpleNamespace(
            function_tools=[
                SimpleNamespace(name="mcp__my__server__query", description="Query"),
                SimpleNamespace(name="mcp__hz__doctor", description="Doctor"),
            ]
        ),
        messages=[],
        model=SimpleNamespace(context_window=None),
    )
    diagnostics.capture(request)
    assert [(item["name"], item["internal"], item["offered"]) for item in diagnostics.servers] == [
        ("my__server", False, True),
        ("Internal", True, True),
        ("hz", False, False),
    ]


def test_missing_passive_metadata_never_falls_back_to_discovery():
    class Provider(FoundMcpServers):
        def tools(self):
            pytest.fail("passive diagnostics may not discover tools")

    diagnostics = TurnDiagnostics(lambda _name, _data: None)
    diagnostics.observe_connections(
        SimpleNamespace(provides=lambda _service: True, resolve=lambda _service: Provider())
    )
    assert diagnostics.servers == []


def test_lazy_gateway_is_internal_and_cached_count_is_not_offered_count():
    diagnostics = TurnDiagnostics(lambda _name, _data: None)
    diagnostics.status = {"external": ("user", False), "weather": ("user", True)}
    diagnostics.cached_counts = {"external": None, "weather": 7}
    diagnostics.capture(
        SimpleNamespace(
            model_request_parameters=SimpleNamespace(
                function_tools=[
                    SimpleNamespace(name="mcp__external__tools", description="Discover tools"),
                ]
            ),
            messages=[],
            model=SimpleNamespace(context_window=None),
        )
    )
    assert [(s["name"], s["internal"], s["offered"]) for s in diagnostics.servers] == [
        ("Internal", True, True),
        ("external", False, False),
        ("weather", False, False),
    ]
    assert diagnostics.servers[0]["tools"][0]["name"] == "external.tools"
    assert diagnostics.servers[2]["tool_count"] is None
    assert diagnostics.servers[2]["cached_tool_count"] == 7


def test_concurrent_tool_timings_are_categorized_without_private_payloads():
    from concurrent.futures import ThreadPoolExecutor

    events = []
    diagnostics = TurnDiagnostics(lambda _name, data: events.append(data))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: diagnostics.observe_tool("mcp__hz__doctor", 2), range(40)))
    diagnostics.observe_tool("mcp__external__tools", 3)
    diagnostics.observe_tool("mcp__external__schema", 4)
    diagnostics.observe_tool("mcp__external__call", 5)
    assert events[-1]["internal_tool_ms"] == 80
    assert events[-1]["mcp_discovery_ms"] == 7
    assert events[-1]["mcp_tool_ms"] == 5
    assert "doctor" not in repr(events)
