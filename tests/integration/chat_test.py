"""The chat end to end with a fake runner (ADR 0045): `hz chat` in a terminal, the chat's MCP server, the web stream.

The fake runner behaves like an agent: it streams text through the injected `Progress`, and calls hz tools through
the chat's MCP head in-process, so what it proposes goes through the same tool layer a real agent's calls do. One test
starts the very command line the runner hands the agent, as a real stdio MCP server.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters
from typer.testing import CliRunner

from heinzel import ActionGate, AgentResult, Budget, Module, Progress, TokenUsage, ToolProfile
from heinzel.heads import api, mcp, web
from heinzel.heads._exposure import exposed
from heinzel.heads.cli import build
from heinzel.kernel import Config, Kernel, boot
from heinzel.modules.system.runner.chat._service import server_command

pytestmark = pytest.mark.integration

TOKEN = "chat-token-for-tests"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WORDS = ("Proposed ", "publishing ", "it.")
DRAFT = "I proposed publishing Week 43."
"""Differs from the streamed words, so a test sees which of the two the terminal printed."""


@dataclass
class Script:
    """What the fake agent does on each run of one test: the tools it calls, and how its run ends."""

    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    result: AgentResult | None = None
    kernel: Kernel | None = None


def fake_runner_module(script: Script, ran: list[str]) -> Module:
    module = Module("fakeagent", "A fake agent runner and an operation to act on.")

    class FakeRunner:
        def __init__(self, progress: Progress) -> None:
            self.progress = progress

        def run(
            self, prompt: str, *, contract: str, tools: ToolProfile, timeout: timedelta, budget: Budget
        ) -> AgentResult:
            del contract, timeout, budget
            assert not tools.read_files  # a chat reads through hz only
            script.prompts.append(prompt)
            emit = self.progress.emitter()
            command = tools.mcp_servers["hz"].command
            session = command[command.index("--chat") + 1]
            assert script.kernel is not None
            surface = {item.name: item for item in exposed(script.kernel, mcp.HEAD)}
            for name, arguments in script.calls:
                emit("tool", {"name": name})
                mcp.chat_tool(script.kernel, surface[name], session)(**arguments)
            if script.result is not None:
                return script.result
            for word in WORDS:
                emit("text", {"text": word})
            return AgentResult(
                runner="fake", status="completed", draft=DRAFT, usage=TokenUsage(cost_usd=0.02), duration_seconds=0.1
            )

    module.provides("agent-runner", modes={"read"}, name="fake")(FakeRunner)

    @module.operation(read_only=False)
    def publish(title: str) -> str:
        """Publish something."""
        ran.append(title)
        return f"published {title}"

    return module


@pytest.fixture
def ran() -> list[str]:
    return []


@pytest.fixture
def script() -> Script:
    return Script(calls=[("fakeagent_publish", {"title": "Week 43"})])


@pytest.fixture
def kernel(make_kernel, ran, script):
    config = Config(files=(), data={"capabilities": {"agent_runner": "fake"}})
    script.kernel = make_kernel(builtins=True, fakeagent=fake_runner_module(script, ran), config=config)
    return script.kernel


def hz(kernel: Kernel, *args: str, input: str | None = None):  # noqa: A002 - as CliRunner names it
    return CliRunner().invoke(build(kernel), list(args), input=input)


def test_hz_chat_streams_the_answer_and_runs_a_proposal_only_after_y(kernel, ran):
    result = hz(kernel, "chat", "--context", "contract:weekly", input="Publish it\ny\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "".join(WORDS) in result.output  # streamed as it came
    assert DRAFT not in result.output  # not printed again after streaming
    assert "· fakeagent_publish" in result.output  # the tool call, as it happened
    assert "The agent proposes: hz fakeagent publish --title 'Week 43'" in result.output
    assert "Ran `hz fakeagent publish --title 'Week 43'`" in result.output
    assert ran == ["Week 43"]
    assert "fake · $0.0200" in result.output


def test_hz_chat_continue_resumes_and_anything_but_y_declines(kernel, ran, script):
    first = hz(kernel, "chat", input="Publish it\nn\n/exit\n")
    assert first.exit_code == 0, first.output
    assert "Declined `hz fakeagent publish --title 'Week 43'`." in first.output
    script.calls = []
    again = hz(kernel, "chat", "--continue", input="And now?\n")
    assert again.exit_code == 0, again.output
    assert "Publish it" in again.output  # the conversation so far, shown again
    assert script.prompts[-1].startswith("User: Publish it")
    assert script.prompts[-1].endswith("User: And now?")
    assert ran == []


def test_leaving_at_the_question_keeps_the_action_pending_for_later(kernel, ran):
    left = hz(kernel, "chat", input="Publish it\n")  # Ctrl-D at "Run it? [y/N]"
    assert left.exit_code == 0, left.output
    assert ran == []
    back = hz(kernel, "chat", "--continue", input="y\n/exit\n")
    assert "The agent proposes: hz fakeagent publish --title 'Week 43'" in back.output
    assert ran == ["Week 43"]


def test_a_failed_answer_says_why_and_how_to_fix_it(kernel, script):
    script.calls = []
    script.result = AgentResult(
        runner="claude-code",
        status="failed",
        detail="Claude Code's login expired: run `claude auth login`",
        fix="claude auth login",
        duration_seconds=0.1,
    )
    result = hz(kernel, "chat", input="Hello\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Stopped: Claude Code's login expired" in result.output
    assert "Fix: claude auth login" in result.output


def test_a_message_the_chat_refuses_is_an_error_and_the_chat_goes_on(make_kernel, ran, script):
    config = Config(
        files=(),
        data={"capabilities": {"agent_runner": "fake"}, "system": {"runner": {"chat": {"session_max_usd": 0.005}}}},
    )
    script.kernel = make_kernel(builtins=True, fakeagent=fake_runner_module(script, ran), config=config)
    result = hz(script.kernel, "chat", input="Hello\n/exit\n")
    assert result.exit_code == 0, result.output
    assert "Error: This chat has cost $0.00 of its $0.01 cap" in " ".join(result.output.split())
    assert script.prompts == []


def test_hz_chat_continue_without_a_chat_says_so(kernel):
    result = hz(kernel, "chat", "--continue")
    assert result.exit_code == 1
    assert "no chat to continue" in result.output


def test_chat_open_with_an_event_id_returns_a_session_to_link_to(kernel):
    result = hz(kernel, "chat", "open", "--context", "ev42", "--detail", "Build failed", "--json")
    assert result.exit_code == 0, result.output
    session = json.loads(result.output)
    assert session["context"] == {"kind": "event", "ref": "ev42", "detail": "Build failed"}
    assert len(session["id"]) == 16


def test_chat_remove_deletes_a_session(kernel):
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]
    assert json.loads(hz(kernel, "chat", "remove", session, "--json").output) == []
    assert "There is no chat session" in hz(kernel, "chat", "show", session).output


def test_the_chat_mcp_server_proposes_writes_over_mcp_and_runs_nothing(kernel, ran):
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]

    async def scenario() -> None:
        async with Client(mcp.build(kernel, chat=session)) as client:
            listed = {tool.name for tool in (await client.list_tools()).tools}
            assert "fakeagent_publish" in listed
            assert not any(name.startswith("system_runner_chat") for name in listed)
            reply = await client.call_tool("fakeagent_publish", {"title": "Week 44"})
            assert "Not run" in str(reply.content)

    anyio.run(scenario)
    assert ran == []
    shown = json.loads(hz(kernel, "chat", "show", session, "--json").output)
    assert [(a["command"], a["status"]) for a in shown["actions"]] == [
        ("hz fakeagent publish --title 'Week 44'", "pending")
    ]
    assert isinstance(kernel.resolve(ActionGate), ActionGate)


def test_the_command_the_agent_starts_is_the_gated_chat_server(tmp_path, monkeypatch):
    """The real `python -m heinzel system serve mcp --chat <id> --project <dir>`, as a stdio process."""
    project = tmp_path / "project"
    project.mkdir()
    state = tmp_path / "project-state"
    (project / "hz.toml").write_text(f"[hz]\nstate_dir = {json.dumps(str(state))}\n", encoding="utf-8")
    monkeypatch.delenv("HZ_STATE_DIR")  # so the project file decides where sessions are, in both processes
    monkeypatch.chdir(project)  # the head that opens the chat runs in the project, and so must the server
    kernel = boot()  # as `hz` boots there
    session = json.loads(hz(kernel, "chat", "open", "--json").output)["id"]
    assert (state / "chat" / session).is_dir()
    command = server_command(session, Path.cwd())
    monkeypatch.chdir(tmp_path)  # where the agent starts it: anywhere else

    async def scenario() -> str:
        server = StdioServerParameters(command=command[0], args=command[1:], env=dict(os.environ))
        async with Client(server) as client:
            reply = await client.call_tool("system_config_set", {"key": "org.role", "value": "lead"})
            return str(reply.content)

    assert "Not run" in anyio.run(scenario)
    shown = json.loads(hz(kernel, "chat", "show", session, "--json").output)
    assert [(a["operation"], a["status"]) for a in shown["actions"]] == [("set", "pending")]


def test_serving_a_chat_on_a_port_is_refused(kernel):
    result = hz(kernel, "system", "serve", "mcp", "--chat", "0123456789abcdef", "--transport", "streamable-http")
    assert result.exit_code == 2
    assert "over stdio only" in result.output


def stream_events(body: str) -> list[tuple[str, dict]]:
    return [
        (block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
        for block in body.strip().split("\n\n")
    ]


def web_client(kernel: Kernel, tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return TestClient(web.build(kernel, port=8000, token=TOKEN, static=tmp_path), base_url="http://127.0.0.1:8000")


def test_the_web_head_streams_a_message_as_server_sent_events(kernel, tmp_path):
    client = web_client(kernel, tmp_path)
    opened = client.post("/api/system/runner/chat/open", params={"context": "route:/doctor"}, headers=AUTH).json()
    with client.stream(
        "POST",
        "/api/stream/system/runner/chat/send",
        params={"session": opened["id"], "message": "Publish it"},
        headers=AUTH,
    ) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    events = stream_events(body)
    assert [kind for kind, _ in events] == ["tool", "text", "text", "text", "result"]
    assert "".join(data["text"] for kind, data in events if kind == "text") == "".join(WORDS)
    session = events[-1][1]["data"]
    assert session["actions"][0]["status"] == "pending"
    refused = client.post("/api/stream/system/runner/chat/send", params={"session": opened["id"], "message": "x"})
    assert refused.status_code == 401  # the token guards streams like every other call


def test_the_web_stream_ends_with_the_error_an_operation_raised(kernel, tmp_path):
    body = (
        web_client(kernel, tmp_path)
        .post(
            "/api/stream/system/runner/chat/send", params={"session": "0123456789abcdef", "message": "Hi"}, headers=AUTH
        )
        .text
    )
    assert body.startswith("event: error\n")
    assert "There is no chat session 0123456789abcdef" in body


def test_a_bug_in_a_streamed_operation_ends_the_stream_with_an_error(make_kernel, tmp_path):
    module = Module("buggy", "An operation with a bug.")

    @module.operation(heads={"cli", "web"})
    def crash() -> str:
        """Fail."""
        msg = "secret detail"
        raise RuntimeError(msg)

    body = web_client(make_kernel(builtins=True, buggy=module), tmp_path).post("/api/stream/buggy/crash", headers=AUTH)
    assert stream_events(body.text) == [("error", {"error": "crash failed with a bug; the log of `hz ui` says why."})]


def test_the_unauthenticated_rest_head_has_no_chat(kernel):
    paths = {route.path for route in api.build(kernel).routes}  # type: ignore[attr-defined]
    assert not any("chat" in path or path.startswith("/stream") for path in paths)
