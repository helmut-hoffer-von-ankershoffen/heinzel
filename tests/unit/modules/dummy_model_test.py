"""The explicitly chosen local dummy model goes through the real chat engine."""

import asyncio
import time

import pytest

from heinzel import CurrentUser, ModelUnavailableError
from heinzel.kernel import AiModelsSource
from heinzel.modules.ai.dummy import DummyModels
from tests.unit.modules.user_chat_plugin_test import Models, Settings, service
from tests.unit.modules.user_chat_test import setup_chat

pytestmark = pytest.mark.unit


class DummySource(Models):
    def offers(self, access: str) -> bool:
        return access == "dummy"

    def source(self, access: str) -> AiModelsSource:
        return AiModelsSource(
            name=access, module="ai.dummy", distribution="heinzel", version="test", adapter=self.plugin
        )


def test_dummy_is_explicit_and_only_offers_42():
    dummy = DummyModels()
    assert [offer.id for offer in dummy.models()] == ["dummy-42"]
    with pytest.raises(ModelUnavailableError):
        dummy.model("other")


def test_dummy_chat_turn_answers_42(tmp_path):
    current = CurrentUser(tmp_path / "identity")
    with current.local("cli"):
        current, crud, session = setup_chat(current)
        dummy = DummyModels()
        chat = service(crud, current, dummy, Settings(access="dummy"), [], models=DummySource(dummy))
        start = time.monotonic()
        replied = asyncio.run(chat.answer(session, "hello"))
        print(f"cold chat service: {time.monotonic() - start:.3f}s")  # noqa: T201 - measurement
        assert replied.message.text == "42"
