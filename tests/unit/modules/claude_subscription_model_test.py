"""Offline executable fixtures only; fake admission is not real CLI conformance evidence."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from heinzel import HeinzelError
from heinzel.modules.heinzel._claude_subscription_model import (
    CONTRACT_OPTIONS,
    SubscriptionAdmission,
    SubscriptionDeniedError,
)
from heinzel.modules.heinzel._claude_subscription_model import ClaudeSubscriptionModel as _ClaudeSubscriptionModel


class FixtureSlots:
    def reserve(self, *args):
        pass


def fixture_model(*args, **kwargs):
    return _ClaudeSubscriptionModel(*args, slots=FixtureSlots(), turn_id="fixture", **kwargs)


pytestmark = pytest.mark.unit
HELP = " ".join(CONTRACT_OPTIONS)


class FixtureVerifier:
    def __init__(self, allow=True):
        self.allow = allow
        self.seen = []

    def verify(self, executable, digest, version, flags, policy):
        self.seen.append((executable, digest, version, flags, policy))
        return self.allow


def fake_cli(tmp_path: Path, version="2.1.312", extra="", help=HELP) -> Path:  # noqa: A002
    executable = tmp_path / "fake-claude"
    executable.write_text(
        f"""#!{sys.executable}
import json, sys
if sys.argv[1:] == ["--version"]:
    print({version!r} + " (Claude Code)")
    raise SystemExit(0)
if sys.argv[1:] == ["--help"]:
    print({help!r})
    raise SystemExit(0)
assert "--safe-mode" in sys.argv and "--restricted" in sys.argv
assert "--strict-mcp-config" in sys.argv and "--no-session-persistence" in sys.argv
assert sys.argv[sys.argv.index("--tools") + 1] == ""
assert sys.argv[sys.argv.index("--allowed-tools") + 1] == ""
assert sys.argv[sys.argv.index("--setting-sources") + 1] == ""
assert "--json-schema" in sys.argv
{extra}
request = json.load(sys.stdin)
assert "conversation transport" in request["protocol"]
assert "listed output tool" in request["protocol"]
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
    for verifier, current, reason in [
        (FixtureVerifier(allow=False), lambda: policy, "access-changed"),
        (FixtureVerifier(), lambda: replace(policy, revision=2), "access-changed"),
        (FixtureVerifier(), lambda: policy, "version-unsupported"),
    ]:
        model = fixture_model(executable, policy, verifier, current)
        with pytest.raises(SubscriptionDeniedError) as error:
            asyncio.run(model.request(message, None, parameters()))
        assert error.value.reason == reason
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
            if command[-1] == "--help":
                return Outcome(0, " ".join(CONTRACT_OPTIONS), "")
            return Outcome(
                0,
                json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": _wire}),
                "",
            )

        model = fixture_model(executable, policy, FixtureVerifier(), lambda: policy, execute=fake)
        with pytest.raises(HeinzelError, match="cannot verify"):
            asyncio.run(model.request([ModelRequest(parts=[UserPromptPart(content="private")])], None, parameters()))


def _answering(wire, sent=None):
    def fake(command, *, input=None, **_kwargs):  # noqa: A002 - the runner's keyword
        from heinzel.modules.system.runner._process import Outcome

        if command[-1] == "--version":
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        if command[-1] == "--help":
            return Outcome(0, " ".join(CONTRACT_OPTIONS), "")
        if sent is not None:
            sent.append(json.loads(input))
        return Outcome(
            0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": wire}), ""
        )

    return fake


def test_an_omitted_name_only_ever_means_the_single_output_tool(tmp_path):
    policy = admission()
    executable = fake_cli(tmp_path)
    wire = {"kind": "tool", "name": None, "arguments": {"report": "Done"}, "text": None}
    prompt = [ModelRequest(parts=[UserPromptPart(content="private")])]
    host = ToolDefinition(name="use_tool")
    output = ToolDefinition(name="final_result", kind="output")
    both = ModelRequestParameters(function_tools=[host], output_tools=[output], allow_text_output=False)
    model = fixture_model(executable, policy, FixtureVerifier(), lambda: policy, execute=_answering(wire))
    response = asyncio.run(model.request(prompt, None, both))
    assert [(part.tool_name, part.args) for part in response.parts] == [("final_result", {"report": "Done"})]
    for unnamed in (
        parameters(),
        ModelRequestParameters(
            function_tools=[host], output_tools=[output, ToolDefinition(name="other", kind="output")]
        ),
    ):
        refused = fixture_model(executable, policy, FixtureVerifier(), lambda: policy, execute=_answering(wire))
        request = refused.request(prompt, None, unnamed)
        with pytest.raises(HeinzelError, match="cannot verify"):
            asyncio.run(request)


def test_history_does_not_repeat_the_instructions_of_every_request(tmp_path):
    policy = admission()
    sent = []
    wire = {"kind": "text", "name": None, "arguments": None, "text": "ok"}
    model = fixture_model(fake_cli(tmp_path), policy, FixtureVerifier(), lambda: policy, execute=_answering(wire, sent))
    history = [
        ModelRequest(parts=[UserPromptPart(content="first")], instructions="Long instructions"),
        ModelResponse(parts=[TextPart(content="reply")]),
        ModelRequest(parts=[UserPromptPart(content="second")], instructions="Long instructions"),
    ]
    asyncio.run(model.request(history, None, parameters()))
    assert sent[0]["instructions"] == "Long instructions"
    assert all("instructions" not in message for message in sent[0]["messages"])
    assert len(sent[0]["messages"]) == len(history)


def test_permission_denial_cannot_be_reported_as_success(tmp_path):
    from heinzel.modules.system.runner._process import Outcome

    policy = admission()

    def fake(command, **_kwargs):
        if command[-1] == "--version":
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        if command[-1] == "--help":
            return Outcome(0, " ".join(CONTRACT_OPTIONS), "")
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
    with pytest.raises(HeinzelError, match="cannot verify"):
        asyncio.run(model.request([ModelRequest(parts=[UserPromptPart(content="private")])], None, parameters()))


def test_policy_revoked_during_slot_reservation_prevents_model_launch(tmp_path):
    from heinzel.modules.system.runner._process import Outcome

    policy = admission()
    current = [policy]
    launches = []

    class RevokingSlots:
        def reserve(self, *_args):
            current[0] = replace(policy, revision=2)

    def fake(command, **_kwargs):
        launches.append(command)
        if command[-1] == "--help":
            return Outcome(0, " ".join(CONTRACT_OPTIONS), "")
        return Outcome(0, "2.1.312 (Claude Code)\n", "")

    model = _ClaudeSubscriptionModel(
        fake_cli(tmp_path),
        policy,
        FixtureVerifier(),
        lambda: current[0],
        slots=RevokingSlots(),
        turn_id="fixture",
        execute=fake,
    )
    with pytest.raises(HeinzelError, match="AI access changed"):
        asyncio.run(model.request([ModelRequest(parts=[UserPromptPart(content="private")])], None, parameters()))
    assert [command[-1] for command in launches] == ["--version", "--help"]


def test_cli_reported_error_is_redacted_and_cannot_be_replayed(tmp_path):
    from heinzel.modules.system.runner._process import Outcome

    policy = admission()
    calls = []

    def fake(command, **_kwargs):
        if command[-1] == "--version":
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        if command[-1] == "--help":
            return Outcome(0, " ".join(CONTRACT_OPTIONS), "")
        calls.append(1)
        return Outcome(
            1,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "permission_denials": [],
                    "result": "private provider details",
                }
            ),
            "",
        )

    model = fixture_model(fake_cli(tmp_path), policy, FixtureVerifier(), lambda: policy, execute=fake)
    messages = [ModelRequest(parts=[UserPromptPart(content="private prompt")])]
    with pytest.raises(HeinzelError, match="reported an error") as error:
        asyncio.run(model.request(messages, None, parameters()))
    assert "private" not in str(error.value)
    with pytest.raises(HeinzelError, match="safely admitted"):
        asyncio.run(model.request(messages, None, parameters()))
    assert calls == [1]


@pytest.mark.parametrize(
    ("status", "code", "is_limit"),
    [
        (429, "usage_limit_reached", True),
        ("429", "usage_limit_reached", True),
        (429, "other", False),
        ("429.0", "usage_limit_reached", False),
        (True, "usage_limit_reached", False),
    ],
    ids=["integer", "string", "other-code", "other-status", "boolean"],
)
def test_usage_limit_classification_is_exact_and_redacted(tmp_path, status, code, is_limit):
    from heinzel.modules.heinzel._claude_subscription_model import SubscriptionUsageLimitError
    from heinzel.modules.system.runner._process import Outcome

    policy = admission()

    def fake(command, **_kwargs):
        if command[-1] == "--version":
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        if command[-1] == "--help":
            return Outcome(0, " ".join(CONTRACT_OPTIONS), "")
        return Outcome(
            1,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "api_error_status": status,
                    "api_error": code,
                    "result": "private details",
                }
            ),
            "",
        )

    model = fixture_model(fake_cli(tmp_path), policy, FixtureVerifier(), lambda: policy, execute=fake)
    with pytest.raises(HeinzelError) as error:
        asyncio.run(model.request([ModelRequest(parts=[UserPromptPart(content="private")])], None, parameters()))
    assert isinstance(error.value, SubscriptionUsageLimitError) is is_limit
    assert "private" not in str(error.value)
    if is_limit:
        assert "provider settings" in str(error.value)
        assert "will not retry automatically" in str(error.value)


def test_modern_retry_feedback_and_retried_tool_return_are_transported(tmp_path):
    from pydantic_ai.messages import RetryFeedbackPart, RetryInfo

    from heinzel.kernel import ProcessOutcome

    captured = []

    def fake(command, **kwargs):
        if command[-1] == "--version":
            return ProcessOutcome(0, "2.1.312 (Claude Code)\n", "")
        if command[-1] == "--help":
            return ProcessOutcome(0, " ".join(CONTRACT_OPTIONS), "")
        captured.append(json.loads(kwargs["input"]))
        return ProcessOutcome(
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": {"kind": "text", "name": None, "arguments": None, "text": "fixed"},
                }
            ),
            "",
        )

    policy = admission()
    model = fixture_model(fake_cli(tmp_path), policy, FixtureVerifier(), lambda: policy, execute=fake)
    parts = [
        UserPromptPart(content="question"),
        ToolReturnPart(
            tool_name="use_tool", content="retry tool", outcome="retried", retry=RetryInfo(cause="model_retry")
        ),
        RetryFeedbackPart(content="retry output", retry=RetryInfo(cause="model_retry")),
    ]
    response = asyncio.run(model.request([ModelRequest(parts=parts)], None, parameters()))
    assert response.parts[0].content == "fixed"
    sent = captured[0]["messages"][0]["parts"]
    assert sent[1]["part_kind"] == "tool-return"
    assert sent[1]["outcome"] == "retried"
    assert sent[2]["part_kind"] == "retry-feedback"
    assert sent[2]["content"] == "retry output"


REAL_2_1_321_OPTIONS = (Path(__file__).parents[2] / "fixtures/claude_code/2.1.321-options.txt").read_text(
    encoding="utf-8"
)


def _message():
    return [ModelRequest(parts=[UserPromptPart(content="private prompt")])]


def test_a_newer_reviewed_release_with_its_real_options_is_admitted(tmp_path):
    policy = admission()
    verifier = FixtureVerifier()
    executable = fake_cli(tmp_path, version="2.1.321", help=REAL_2_1_321_OPTIONS)
    model = fixture_model(executable, policy, verifier, lambda: policy)
    response = asyncio.run(model.request(_message(), None, parameters()))
    assert isinstance(response.parts[0], ToolCallPart)
    assert [seen[2] for seen in verifier.seen] == ["2.1.312 to before 2.2.0", "2.1.321"]


@pytest.mark.parametrize("version", ["2.1.311", "2.2.0", "3.0.0", "2.1.321-beta", "2.1.0321", "Claude Code"])
def test_a_release_outside_the_reviewed_range_is_refused_before_the_prompt(tmp_path, version):
    sentinel = tmp_path / "ran"
    executable = fake_cli(tmp_path, version=version, extra=f"open({str(sentinel)!r}, 'w').write('ran')")
    policy = admission()
    model = fixture_model(executable, policy, FixtureVerifier(), lambda: policy)
    with pytest.raises(SubscriptionDeniedError) as error:
        asyncio.run(model.request(_message(), None, parameters()))
    assert error.value.reason == "version-unsupported"
    action = "Update Claude Code" if version == "2.1.311" else "Update Heinzel"
    assert str(error.value).endswith(f"{action}, then send the message again.")
    assert not sentinel.exists()


def test_the_option_contract_is_proven_once_per_digest_and_again_after_an_update(tmp_path):
    from heinzel.modules.system.runner._process import Outcome

    executable = fake_cli(tmp_path)
    helps = []

    def fake(command, **_kwargs):
        if command[-1] == "--version":
            return Outcome(0, "2.1.312 (Claude Code)\n", "")
        if command[-1] == "--help":
            helps.append(1)
            return Outcome(0, " ".join(CONTRACT_OPTIONS), "")
        wire = {"kind": "text", "name": None, "arguments": None, "text": "fine"}
        return Outcome(
            0, json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": wire}), ""
        )

    def ask():
        policy = admission()
        asyncio.run(
            fixture_model(executable, policy, FixtureVerifier(), lambda: policy, execute=fake).request(
                _message(), None, parameters()
            )
        )

    ask()
    ask()
    assert len(helps) == 1
    executable.write_text(executable.read_text(encoding="utf-8") + "\n# updated\n", encoding="utf-8")
    ask()
    assert len(helps) == 2


def _result(**fields):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, **fields})


@pytest.mark.parametrize(
    ("reason", "version", "helped", "answer", "words"),
    [
        ("contract-unproven", "2.1.321", "-p --output-format --json-schema", None, "safety option"),
        ("timeout", "2.1.312", None, (None, "", ""), "did not answer in time"),
        ("process-failed", "2.1.312", None, (1, "private crash", "private"), "stopped without an answer"),
        ("output-invalid", "2.1.312", None, (0, "private text", ""), "cannot verify"),
        ("output-invalid", "2.1.312", None, (0, _result(structured_output={"kind": "x"}), ""), "cannot verify"),
        ("cli-error", "2.1.312", None, (1, _result(is_error=True, result="private body"), ""), "reported an error"),
    ],
    ids=["contract", "timeout", "process", "not-json", "schema", "cli-error"],
)
def test_every_refusal_keeps_a_typed_reason_and_plain_words_but_never_the_body(  # noqa: PLR0913, PLR0917 - one parametrized case
    tmp_path, caplog, reason, version, helped, answer, words
):
    from heinzel.modules.system.runner._process import Outcome

    def fake(command, **_kwargs):
        if command[-1] == "--version":
            return Outcome(0, f"{version} (Claude Code)\n", "")
        if command[-1] == "--help":
            return Outcome(0, helped or " ".join(CONTRACT_OPTIONS), "")
        return Outcome(*answer)

    policy = admission()
    model = fixture_model(fake_cli(tmp_path), policy, FixtureVerifier(), lambda: policy, execute=fake)
    with caplog.at_level("WARNING"), pytest.raises(SubscriptionDeniedError) as error:
        asyncio.run(model.request(_message(), None, parameters()))
    assert error.value.reason == reason
    assert words in str(error.value)
    assert "private" not in str(error.value)
    assert "private" not in caplog.text
    assert f"refused: {reason}" in caplog.text


def test_unavailable_changed_and_refused_requests_say_what_happened(tmp_path):
    from heinzel.modules.system.runner._process import Outcome

    policy = admission()
    missing = fixture_model(tmp_path / "gone", policy, FixtureVerifier(), lambda: policy)
    with pytest.raises(SubscriptionDeniedError) as error:
        asyncio.run(missing.request(_message(), None, parameters()))
    assert error.value.reason == "unavailable"

    executable = fake_cli(tmp_path)

    def updating(command, **_kwargs):
        if command[-1] == "--help":
            return Outcome(0, HELP, "")
        executable.write_text(executable.read_text(encoding="utf-8") + "\n# updated\n", encoding="utf-8")
        return Outcome(0, "2.1.312 (Claude Code)\n", "")

    changed = fixture_model(executable, policy, FixtureVerifier(), lambda: policy, execute=updating)
    with pytest.raises(SubscriptionDeniedError) as error:
        asyncio.run(changed.request(_message(), None, parameters()))
    assert (error.value.reason, "probably an update" in str(error.value)) == ("executable-changed", True)

    refused = fixture_model(executable, policy, FixtureVerifier(), lambda: policy)
    with pytest.raises(SubscriptionDeniedError) as error:
        asyncio.run(refused.request(_message(), {"max_tokens": 1}, parameters()))
    assert error.value.reason == "refused"


def test_the_access_check_runs_the_same_admission_as_chat(tmp_path):
    from heinzel.modules.heinzel._claude_subscription_model import COMMAND_FLAGS, check_access
    from heinzel.modules.system.runner._process import Outcome

    executable = fake_cli(tmp_path)
    seen = []

    def fake(command, *, input=None, **_kwargs):  # noqa: A002 - the runner's keyword
        seen.append(command[1:])
        if command[-1] == "--version":
            return Outcome(0, "2.1.321 (Claude Code)\n", "")
        if command[-1] == "--help":
            return Outcome(0, REAL_2_1_321_OPTIONS, "")
        assert json.loads(input)["allow_text"] is True
        return Outcome(
            0, _result(structured_output={"kind": "text", "name": None, "arguments": None, "text": "OK"}), ""
        )

    assert check_access(executable, fake, "Reply OK", 5) == ("2.1.321", "OK")
    assert seen == [["--version"], ["--help"], list(COMMAND_FLAGS)]
    with pytest.raises(SubscriptionDeniedError) as error:
        check_access(executable, lambda command, **_: Outcome(0, "2.2.1 (Claude Code)\n", ""), "Reply OK", 5)
    assert error.value.reason == "version-unsupported"


def test_an_option_named_only_in_another_options_description_is_not_proven():
    from heinzel.modules.heinzel._claude_subscription_model import prove_contract
    from heinzel.modules.system.runner._process import Outcome

    prove_contract(Outcome(0, REAL_2_1_321_OPTIONS, ""), "2.1.321")
    lines = REAL_2_1_321_OPTIONS.splitlines()
    for option in ("--strict-mcp-config", "--tools", "-p"):
        kept = [line for line in lines if not line.lstrip().startswith((f"{option} ", f"{option},"))]
        text = "\n".join([*kept, f"  --restricted                          Restricted mode, like {option}"])
        with pytest.raises(SubscriptionDeniedError) as error:
            prove_contract(Outcome(0, text, ""), "2.1.321")
        assert error.value.reason == "contract-unproven"
    described = "  --other   " + " ".join(CONTRACT_OPTIONS)
    with pytest.raises(SubscriptionDeniedError):
        prove_contract(Outcome(0, described, ""), "2.1.321")


def test_a_process_that_cannot_start_is_unavailable_without_errno_or_path(tmp_path):
    from heinzel.modules.heinzel._claude_subscription_model import check_access

    executable = fake_cli(tmp_path)

    def refuse(command, **_kwargs):
        raise PermissionError(13, "Permission denied", "/private/path/claude")

    with pytest.raises(SubscriptionDeniedError) as error:
        check_access(executable, refuse, "Reply OK", 5)
    assert error.value.reason == "unavailable"
    assert "private" not in str(error.value)
    assert error.value.__cause__ is None
    policy = admission()
    model = fixture_model(executable, policy, FixtureVerifier(), lambda: policy, execute=refuse)
    with pytest.raises(SubscriptionDeniedError) as error:
        asyncio.run(model.request(_message(), None, parameters()))
    assert error.value.reason == "unavailable"
    assert "private" not in str(error.value)


def test_a_cli_error_says_what_to_do():
    words = str(SubscriptionDeniedError("cli-error"))
    assert words.endswith("then send the message again if it is still needed.")
