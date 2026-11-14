"""A Heinzel's level picks Claude Code's model: the alias passes through the verified flags (ADR 0158)."""

import asyncio

import pytest

from heinzel import CurrentUser, HeinzelError
from heinzel.kernel._claude_code import COMMAND_FLAGS, CONTRACT_OPTIONS, LEVEL_MODELS, MODEL_FLAG, command_flags
from heinzel.modules.heinzel._level_operations import changed
from heinzel.modules.heinzel._user_chat import UserChatService
from heinzel.modules.system.runner._process import run_bounded
from heinzel.modules.system.runner._snapshot import RunnerSnapshotService
from tests.unit.modules.user_chat_test import FixtureConfig, fake_claude, setup_chat

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(("level", "alias"), [("auto", "sonnet"), ("low", "haiku"), ("high", "opus")])
def test_a_claude_code_turn_passes_the_levels_alias_and_records_it(tmp_path, level, alias):
    commands: list[list[str]] = []

    def execute(command, **kwargs):
        commands.append(list(command))
        return run_bounded(command, **kwargs)

    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        if level != "auto":
            now, revision = crud.intelligence(session)
            crud.profile(session, "lvl-1", revision, intelligence=changed(now, level))
        executable = fake_claude(tmp_path)
        service = UserChatService(
            crud, current, RunnerSnapshotService(), load=lambda: FixtureConfig(executable), execute=execute
        )
        answer = asyncio.run(service.answer(session, "hello"))
    binding = answer.message.backend_binding
    assert (binding.backend, binding.model, binding.effort) == ("claude-code", alias, None)
    assert binding.level is not None
    assert (binding.level.asked, binding.level.source) == (level, "declared")
    turns = [command for command in commands if "--json-schema" in command]
    assert turns
    assert all(command[1:] == [*COMMAND_FLAGS, MODEL_FLAG, alias] for command in turns)


def test_only_a_level_alias_passes_through_the_flags():
    assert MODEL_FLAG in CONTRACT_OPTIONS
    assert command_flags(None) == COMMAND_FLAGS
    assert set(LEVEL_MODELS) == {"low", "medium", "high"}
    for alias in LEVEL_MODELS.values():
        assert command_flags(alias)[-2:] == (MODEL_FLAG, alias)
    with pytest.raises(HeinzelError):
        command_flags("claude-opus-5-5")
