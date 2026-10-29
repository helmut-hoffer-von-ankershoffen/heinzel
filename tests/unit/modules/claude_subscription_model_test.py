"""Offline executable fixtures only; fake admission is not real CLI conformance evidence."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from heinzel import HeinzelError
from heinzel.modules.heinzel._claude_subscription_model import ClaudeSubscriptionModel as _ClaudeSubscriptionModel
from heinzel.modules.heinzel._claude_subscription_model import SubscriptionAdmission


class FixtureSlots:
    def reserve(self, *args):
        pass


def fixture_model(*args, **kwargs):
    return _ClaudeSubscriptionModel(*args, slots=FixtureSlots(), turn_id="fixture", **kwargs)


pytestmark = pytest.mark.unit


class FixtureVerifier:
    def __init__(self, allow=True):
        self.allow = allow
        self.seen = []

    def verify(self, executable, digest, version, flags, policy):
        self.seen.append((executable, digest, version, flags, policy))
        return self.allow


def fake_cli(tmp_path: Path, version="2.1.312", extra="") -> Path:
    executable = tmp_path / "fake-claude"
    executable.write_text(
        f"""#!{sys.executable}
import json, sys
if sys.argv[1:] == ["--version"]:
    print({version!r} + " (Claude Code)")
    raise SystemExit(0)
assert "--safe-mode" in sys.argv and "--restricted" in sys.argv
assert "--strict-mcp-config" in sys.argv and "--no-session-persistence" in sys.argv
assert sys.argv[sys.argv.index("--tools") + 1] == ""
assert sys.argv[sys.argv.index("--allowed-tools") + 1] == ""
assert sys.argv[sys.argv.index("--setting-sources") + 1] == ""
assert "--json-schema" in sys.argv
{extra}
request = json.load(sys.stdin)
if len(request["messages"]) == 1:
    wire = {{"kind":"tool", "name":"use_tool", "arguments":{{"name":"fixture:read", "arguments":{{}}}}, "text":None}}
else:
    wire = {{"kind":"text", "name":None, "arguments":None, "text":"A local response"}}
print(json.dumps({{"type":"result", "subtype":"success", "is_error":False, "structured_output":wire,
                  "permission_denials":[], "total_cost_usd":999}}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return executable


def admission(**changes):
    return replace(
        SubscriptionAdmission(
            owner="u-" + "a" * 32,
            session="hz-01ARZ3NDEKTSV4RRFFQ69G5FAV",
            revision=1,
            max_requests=2,
            timeout_seconds=5,
            max_output_bytes=16_384,
        ),
        **changes,
    )


def parameters():
    return ModelRequestParameters(function_tools=[ToolDefinition(name="use_tool")], allow_text_output=True)


def test_fake_executable_roundtrip_preserves_unknown_subscription_cost_and_host_tool_boundary(tmp_path):
    verifier = FixtureVerifier()
    policy = admission()
    model = fixture_model(fake_cli(tmp_path), policy, verifier, lambda: policy)
    first = [ModelRequest(parts=[UserPromptPart(content="private question")])]
    response = asyncio.run(model.request(first, None, parameters()))
    assert len(response.parts) == 1
    assert isinstance(response.parts[0], ToolCallPart)
    assert response.parts[0].tool_name == "use_tool"
    assert response.usage.cost is None
    assert response.usage.unmeasured_requests == 1
    followup = [*first, response, ModelRequest(parts=[ToolReturnPart(tool_name="use_tool", content="host result")])]
    final = asyncio.run(model.request(followup, None, parameters()))
    assert isinstance(final.parts[0], TextPart)
    assert final.parts[0].content == "A local response"
    assert final.usage.cost is None
    assert all("private question" not in str(entry[3]) for entry in verifier.seen)
    with pytest.raises(HeinzelError, match="safely admitted"):
        asyncio.run(model.request(first, None, parameters()))


def test_pydantic_agent_roundtrip_dispatches_only_host_tool(tmp_path):
    policy = admission()
    model = fixture_model(fake_cli(tmp_path), policy, FixtureVerifier(), lambda: policy)
    agent = Agent(model)
    calls = []

    @agent.tool_plain
    def use_tool(name: str, arguments: dict[str, object]) -> str:
        calls.append((name, arguments))
        return "host read result"

    result = asyncio.run(agent.run("hello"))
    assert result.output == "A local response"
    assert calls == [("fixture:read", {})]
    assert result.usage.cost is None


def test_unsupported_caps_and_model_settings_fail_closed_before_process(tmp_path):
    executable = fake_cli(tmp_path)
    for policy in (admission(dollar_cap=1.0), admission(total_token_cap=1000)):
        with pytest.raises(HeinzelError, match="cannot be enforced"):
            fixture_model(executable, policy, FixtureVerifier(), lambda policy=policy: policy)
    policy = admission()
    verifier = FixtureVerifier()
    model = fixture_model(executable, policy, verifier, lambda: policy)
    with pytest.raises(HeinzelError, match="safely admitted"):
        asyncio.run(
            model.request([ModelRequest(parts=[UserPromptPart(content="private")])], {"max_tokens": 20}, parameters())
        )
    assert verifier.seen == []


def test_untrusted_or_stale_admission_and_version_refuse_before_prompt(tmp_path):
    sentinel = tmp_path / "ran"
    executable = fake_cli(tmp_path, version="1.0.0", extra=f"open({str(sentinel)!r}, 'w').write('ran')")
    policy = admission()
    message = [ModelRequest(parts=[UserPromptPart(content="secret")])]
    for verifier, current in [
        (FixtureVerifier(allow=False), lambda: policy),
        (FixtureVerifier(), lambda: replace(policy, revision=2)),
        (FixtureVerifier(), lambda: policy),
    ]:
        model = fixture_model(executable, policy, verifier, current)
        with pytest.raises(HeinzelError, match="safely admitted"):
            asyncio.run(model.request(message, None, parameters()))
        assert not sentinel.exists()


def test_closed_result_rejects_unknown_tool_or_extra_fields(tmp_path):
    policy = admission()
    executable = fake_cli(tmp_path)
    for wire in (
        {"kind": "tool", "name": "Bash", "arguments": {"command": "ignored"}, "text": None},
        {"kind": "text", "name": None, "arguments": None, "text": "ok", "unexpected": "x"},
    ):

        def fake(command, *, _wire=wire, **_kwargs):
            from heinzel.modules.system.runner._process import Outcome

            if command[-1] == "--version":
                return Outcome(0, "2.1.312 (Claude Code)\n", "")
            return Outcome(
                0,
                json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": _wire}),
                "",
            )

        model = fixture_model(executable, policy, FixtureVerifier(), lambda: policy, execute=fake)
        with pytest.raises(HeinzelError, match="safely admitted"):
            asyncio.run(model.request([ModelRequest(parts=[UserPromptPart(content="private")])], None, parameters()))


def test_permission_denial_cannot_be_reported_as_success(tmp_path):
    from heinzel.modules.system.runner._process import Outcome

    policy = admission()

    def fake(command, **_kwargs):
        if command[-1] == "--version":
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        return Outcome(
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "permission_denials": [{"tool_name": "Bash"}],
                    "structured_output": {"kind": "text", "name": None, "arguments": None, "text": "ok"},
                }
            ),
            "",
        )

    model = fixture_model(fake_cli(tmp_path), policy, FixtureVerifier(), lambda: policy, execute=fake)
    with pytest.raises(HeinzelError, match="safely admitted"):
        asyncio.run(model.request([ModelRequest(parts=[UserPromptPart(content="private")])], None, parameters()))
