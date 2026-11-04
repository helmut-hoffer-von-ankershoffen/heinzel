"""The declared CLI starts one private helper through the real bounded chat process, with a fixture model."""

import json

import pytest
from typer.testing import CliRunner

from heinzel import CurrentUser
from heinzel.heads.cli import build
from heinzel.modules.heinzel._session_crud import SessionCrud
from heinzel.modules.heinzel._user_chat import UserChatService
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude

pytestmark = pytest.mark.integration


def test_one_cli_prompt_creates_turing_and_is_the_first_message_without_grants(make_kernel, tmp_path, monkeypatch):
    kernel = make_kernel(builtins=True)
    kernel.settings = kernel.settings.model_copy(update={"features": frozenset({"connected_frontdoor"})})
    current = CurrentUser(tmp_path / "identity")
    kernel.bind(CurrentUser, current)
    executable = fake_claude(
        tmp_path,
        tool="agent:propose",
        arguments={
            "expected_revision": 0,
            "contract": "Draft a poem from an approved release source.",
            "timeout_seconds": 30,
        },
    )
    chat = kernel.resolve(UserChatService)
    monkeypatch.setattr(chat, "_load", lambda: FixtureConfig(executable))
    prompt = (
        "Create Turing. Every five minutes check https://example.invalid/changelog. "
        "Draft a poem for my review in https://example.invalid/rolling-document; never publish without my approval."
    )
    arguments = ["heinzel", "start-helper", "start-one", "Turing", prompt, "--json"]
    command = build(kernel)
    first = CliRunner().invoke(command, arguments)
    assert first.exit_code == 0, first.output
    answer = json.loads(first.output)
    crud = kernel.resolve(SessionCrud)
    with current.local("cli"):
        snapshot = crud.snapshot(answer["session_id"])
        assert snapshot.name == "Turing"
        assert [(message.role, message.text) for message in snapshot.messages] == [
            ("user", prompt),
            ("assistant", "A real recorded reply"),
        ]
        assert snapshot.messages[0].origin == "cli"
        assert snapshot.schedule is None
        proposal = crud.latest_scheduled_agent_proposal(snapshot.session_id)
        assert proposal is not None
        assert proposal.status == "proposed"
        assert not any(
            event.kind.endswith("approved") for event in crud.repository.control_read(snapshot.session_id).events
        )
    marker = tmp_path / "model-calls"
    calls = marker.read_text()
    retry = CliRunner().invoke(command, arguments)
    assert retry.exit_code == 0, retry.output
    assert json.loads(retry.output) == answer
    assert marker.read_text() == calls
    changed = CliRunner().invoke(command, [*arguments[:-2], prompt + " Changed.", "--json"])
    assert changed.exit_code != 0
    assert marker.read_text() == calls


def test_frontdoor_is_off_by_default_and_creates_no_private_state(make_kernel):
    kernel = make_kernel(builtins=True)
    result = CliRunner().invoke(
        build(kernel),
        ["heinzel", "start-helper", "off-one", "Turing", "Do nothing externally"],
    )
    assert result.exit_code != 0
    assert "not enabled" in result.output
    with kernel.resolve(CurrentUser).local("cli"):
        assert kernel.resolve(SessionCrud).listing().sessions == []
