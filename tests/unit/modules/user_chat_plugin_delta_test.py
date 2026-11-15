"""The Head A delta review's probes, kept as tests (ADR 0159): an effort on a model without thinking."""

import asyncio

import pytest

from heinzel import CurrentUser, ModelOffer
from tests.unit.modules.user_chat_plugin_test import REPLY, Plugin, Settings, service
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit


class Plain(Plugin):
    """A plugin whose model takes no effort (thinking unset), the ADR's 'maps effort itself' case."""

    def models(self) -> list[ModelOffer]:
        return [ModelOffer(id="plain")]


def test_probe_d1_an_effort_setting_on_a_model_without_thinking_does_not_abort_the_turn(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, Plain(), Settings(effort="high"), [])
        replied = asyncio.run(chat.answer(session, "hello"))
    assert replied.message.text == REPLY


def test_probe_d1b_same_with_the_model_pinned(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        chat = service(crud, current, Plain(), Settings(model="plain", effort="medium"), [])
        replied = asyncio.run(chat.answer(session, "hello"))
    assert replied.message.text == REPLY
