"""The shared Claude Code admission: every refusal is typed, plain and keeps no CLI output (ADR 0050)."""

import json
from pathlib import Path

import pytest

from heinzel.kernel._bounded_process import ProcessOutcome as Outcome
from heinzel.kernel._claude_code import (
    CONTRACT_OPTIONS,
    SubscriptionDeniedError,
    check_access,
    prove_contract,
    require_subscription_login,
    structured_result,
    supported_version,
)

pytestmark = pytest.mark.unit
HELP = " ".join(CONTRACT_OPTIONS)


def _reason(call):
    with pytest.raises(SubscriptionDeniedError) as error:
        call()
    assert "private" not in str(error.value)
    return error.value.reason


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (Outcome(None, "", ""), "timeout"),
        (Outcome(1, "2.1.321 (Claude Code)", "private"), "process-failed"),
        (Outcome(0, "2.1.321 (Claude Code)", "", truncated=True), "process-failed"),
    ],
)
def test_a_version_probe_that_did_not_finish_is_typed(outcome, reason):
    assert _reason(lambda: supported_version(outcome)) == reason


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (Outcome(None, HELP, ""), "timeout"),
        (Outcome(2, HELP, "private"), "contract-unproven"),
        (Outcome(0, HELP, "", truncated=True), "contract-unproven"),
    ],
)
def test_a_help_probe_that_did_not_finish_proves_nothing(outcome, reason):
    assert _reason(lambda: prove_contract(outcome, "2.1.321")) == reason


def _success(**fields):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, **fields})


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (Outcome(0, _success(), "", truncated=True), "output-invalid"),
        (Outcome(3, _success(result="private"), "private"), "process-failed"),
        (Outcome(1, "private crash", "private"), "process-failed"),
        (Outcome(0, _success(permission_denials=[{"tool": "Bash"}]), ""), "output-invalid"),
    ],
)
def test_a_result_that_cannot_be_verified_is_typed(outcome, reason):
    assert _reason(lambda: structured_result(outcome)) == reason


def test_a_login_status_that_cannot_run_is_unconfirmed(tmp_path):
    def refuse(*_args, **_kwargs):
        raise PermissionError(13, "private", "/private/claude")

    assert _reason(lambda: require_subscription_login(tmp_path / "claude", refuse)) == "login-unconfirmed"


def _cli(tmp_path: Path) -> Path:
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    return executable


def _answering(executable: Path, wire: object, *, update: bool = False):
    def execute(command, **_kwargs):
        if command[-1] == "--version":
            return Outcome(0, "2.1.321 (Claude Code)\n", "")
        if command[-1] == "--help":
            if update:
                executable.write_text("#!/bin/sh\n# updated\n", encoding="utf-8")
            return Outcome(0, HELP, "")
        return Outcome(0, _success(structured_output=wire), "")

    return execute


def test_the_access_check_refuses_a_missing_changed_or_non_text_answer(tmp_path):
    executable = _cli(tmp_path)
    text = {"kind": "text", "name": None, "arguments": None, "text": "OK"}
    assert _reason(lambda: check_access(tmp_path / "gone", _answering(executable, text), "Reply OK", 5)) == (
        "unavailable"
    )
    updating = _answering(executable, text, update=True)
    assert _reason(lambda: check_access(executable, updating, "Reply OK", 5)) == "executable-changed"
    tool = {"kind": "tool", "name": "use_tool", "arguments": {}, "text": None}
    assert _reason(lambda: check_access(executable, _answering(executable, tool), "Reply OK", 5)) == "output-invalid"


def test_the_access_check_refuses_an_executable_that_vanishes_after_its_help(tmp_path):
    executable = _cli(tmp_path)
    text = {"kind": "text", "name": None, "arguments": None, "text": "OK"}
    answer = _answering(executable, text)

    def vanishing(command, **kwargs):
        outcome = answer(command, **kwargs)
        if command[-1] == "--help":
            executable.unlink()
        return outcome

    assert _reason(lambda: check_access(executable, vanishing, "Reply OK", 5)) == "unavailable"
