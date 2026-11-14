"""The chat capability's self channel: optional methods, hz's trusted view and its bounds (ADR 0160)."""

import threading
from typing import ClassVar

import pytest
from pydantic import ValidationError

from heinzel import HeinzelError, Module, NotSupportedError, SelfAnswer, SelfMessage
from heinzel.kernel import (
    CAPABILITIES,
    SELF_OPTION_LENGTH,
    SELF_OPTIONS,
    Message,
    OperationLet,
    PostedMessage,
    Window,
)
from heinzel.kernel._capabilities import Capability, declared_unsupported
from heinzel.kernel._protocols import Chat
from heinzel.kernel._self_chat import (
    MAX_ANSWER,
    MAX_SELF_TEXT,
    SelfChat,
    checked_answer,
    self_context,
    self_options,
    self_text,
)

pytestmark = pytest.mark.unit


class SelfOnly:
    """An adapter that supports only messages to oneself."""

    unsupported = ("messages", "post")
    sent: ClassVar[list[dict[str, object]]] = []

    def notify_self(self, text: str, *, thread: str | None = None, context: str | None = None) -> SelfMessage:
        self.sent.append({"text": text, "thread": thread, "context": context})
        return SelfMessage(id="m\u200b1", thread=thread or "t-1")

    def ask_self(  # noqa: PLR0913 - the protocol's shape
        self,
        question: str,
        *,
        options=(),
        timeout: float,
        thread: str | None = None,
        context: str | None = None,
        cancel: threading.Event | None = None,
    ) -> SelfAnswer:
        self.sent.append({"question": question, "options": options, "timeout": timeout, "cancel": cancel})
        return SelfAnswer(thread="t-1", text="", option=options[0]) if options else SelfAnswer(text="fine")


class ChannelsOnly:
    """A chat adapter from before ADR 0160: channels, no self methods, nothing declared."""

    def messages(self, channel: str, window: Window) -> list[Message]:
        del channel, window
        return []

    def post(self, channel: str, text: str, thread: str | None = None) -> PostedMessage:
        del text, thread
        return PostedMessage(channel=channel, id="1")


def _kernel(make_kernel, adapter: type, *, modes: frozenset[str] = frozenset({"read", "write"})):
    chat = Module("acmechat", "Acme chat.")
    chat.provides("chat", modes=set(modes))(adapter)
    return make_kernel(acmechat=chat)


def test_chat_has_optional_self_methods_that_write():
    spec = CAPABILITIES["chat"]
    assert spec.methods["notify_self"] == "write"
    assert spec.methods["ask_self"] == "write"
    assert spec.optional == {"notify_self", "ask_self"}
    assert spec.core_only == {"notify_self", "ask_self"}
    assert callable(Chat.notify_self)
    assert callable(Chat.ask_self)


def test_optional_methods_must_be_methods_of_the_capability():
    with pytest.raises(ValueError, match="optional and core-only"):
        Capability(name="x", protocol=Chat, summary="", methods={"post": "write"}, optional=frozenset({"nope"}))


def test_an_adapter_without_the_new_methods_still_loads_and_declares_them_unsupported(make_kernel):
    kernel = _kernel(make_kernel, ChannelsOnly)
    assert declared_unsupported(ChannelsOnly, CAPABILITIES["chat"]) == {"notify_self", "ask_self"}
    assert kernel.self_chat() is None, "it serves neither"


def test_a_self_only_adapter_serves_the_self_channel(make_kernel):
    kernel = _kernel(make_kernel, SelfOnly)
    channel = kernel.self_chat()
    assert channel is not None
    assert (channel.adapter, channel.notifies, channel.asks) == ("acmechat", True, True)
    assert channel.serves("notifications")
    assert channel.serves("questions")
    assert declared_unsupported(SelfOnly, CAPABILITIES["chat"]) == {"messages", "post"}


def test_a_read_only_chat_adapter_has_no_self_channel(make_kernel):
    assert _kernel(make_kernel, SelfOnly, modes=frozenset({"read"})).self_chat() is None


def test_no_chat_adapter_no_self_channel(make_kernel):
    assert make_kernel().self_chat() is None


def test_notify_cleans_and_bounds_what_goes_out_and_what_comes_back():
    SelfOnly.sent.clear()
    channel = SelfChat(adapter="acmechat", notifies=True, asks=False, build=SelfOnly)
    sent = channel.notify("Hi\x07 there " + "x" * MAX_SELF_TEXT, thread=" t-9 ", context="  my\nschedule " + "y" * 99)
    assert sent.id == "m1", "the returned id is cleaned"
    call = SelfOnly.sent[-1]
    assert call["thread"] == "t-9"
    assert len(str(call["text"])) == MAX_SELF_TEXT
    assert str(call["text"]).endswith("…")
    assert "\x07" not in str(call["text"])
    assert str(call["context"]).startswith("my schedule y")
    with pytest.raises(NotSupportedError):
        channel.ask("Why?", timeout=5)


def test_ask_checks_bounds_and_passes_cancel():
    channel = SelfChat(adapter="acmechat", notifies=False, asks=True, build=SelfOnly)
    cancel = threading.Event()
    answer = channel.ask("Which?", options=["Yes", "No"], timeout=30, cancel=cancel)
    assert (answer.option, answer.text, answer.answered_by_option) == ("Yes", "Yes", True)
    assert SelfOnly.sent[-1]["cancel"] is cancel
    assert channel.ask("Why?", timeout=30).text == "fine"
    with pytest.raises(HeinzelError, match="between 1 and"):
        channel.ask("Why?", timeout=0)
    with pytest.raises(NotSupportedError):
        channel.notify("hello")


def test_options_are_bounded_distinct_labels():
    assert self_options([" a\nb ", "c"]) == ("a b", "c")
    with pytest.raises(HeinzelError, match="at most"):
        self_options([str(n) for n in range(SELF_OPTIONS + 1)])
    for bad in (["a", "a"], [""], ["x" * (SELF_OPTION_LENGTH + 1)]):
        with pytest.raises(HeinzelError, match="distinct label"):
            self_options(bad)
    with pytest.raises(HeinzelError, match="not one string"):
        self_options("ab")  # type: ignore[arg-type]


def test_an_answer_must_name_an_offered_option_and_is_cleaned():
    assert checked_answer(SelfAnswer(text="  ok" + chr(0x202E) + " "), (), "a").text == "ok"
    assert checked_answer(SelfAnswer(timed_out=True, thread="t"), (), "a") == SelfAnswer(timed_out=True, thread="t")
    with pytest.raises(HeinzelError, match="wasn't offered"):
        checked_answer(SelfAnswer(option="Delete everything", text="x"), ("Yes",), "a")
    with pytest.raises(HeinzelError, match="SelfAnswer"):
        checked_answer({"text": "hi"}, (), "a")


def test_self_answer_is_consistent():
    with pytest.raises(ValidationError):
        SelfAnswer(timed_out=True, text="late")
    with pytest.raises(ValidationError):
        SelfAnswer(text="  ")
    assert not SelfAnswer(text="hi").answered_by_option


def test_text_and_context_bounds():
    with pytest.raises(HeinzelError, match="needs text"):
        self_text(" \u200b ")
    assert self_context(None) is None
    assert self_context(" \n ") is None


def test_an_adapter_answering_the_wrong_type_is_refused():
    class Wrong:
        def notify_self(self, text, *, thread=None, context=None):
            del text, thread, context
            return {"id": "1"}

        def ask_self(self, question, **kwargs):
            del question, kwargs
            return "yes"

    channel = SelfChat(adapter="w", notifies=True, asks=True, build=Wrong)
    with pytest.raises(HeinzelError, match="SelfMessage"):
        channel.notify("hello")
    with pytest.raises(HeinzelError, match="SelfAnswer"):
        channel.ask("Why?", timeout=3)


def test_a_self_only_adapter_builds_without_channel_methods(make_kernel):
    kernel = _kernel(make_kernel, SelfOnly)
    _adapter, instance = kernel._capabilities.instance("chat", "acmechat")
    assert isinstance(instance, SelfOnly)


def test_the_self_only_dummy_passes_the_conformance_check():
    from heinzel.testing import assert_adapter

    assert_adapter(SelfOnly, "chat", modes={"read", "write"})
    assert_adapter(ChannelsOnly, "chat", modes={"read", "write"})


def test_no_operation_can_reach_the_self_methods_through_its_handle(make_kernel):
    """A publish operation with chat:write could otherwise run its own 'approval' in the owner's direct messages."""
    SelfOnly.sent.clear()
    probe = Module("probe", "Tries to talk to the owner itself.")

    @probe.operation(read_only=False, requires=["chat:read,write"])
    def nag(chat: Chat) -> str:
        """Ask the owner to approve something."""
        for call in (
            lambda: chat.ask_self("Approve the write?", options=("Approve", "Reject"), timeout=5),
            lambda: chat.notify_self("Approved."),
        ):
            with pytest.raises(HeinzelError, match="Only hz itself calls chat"):
                call()
        return "refused"

    chat = Module("acmechat", "Acme chat.")
    chat.provides("chat", modes={"read", "write"})(SelfOnly)
    kernel = make_kernel(acmechat=chat, probe=probe)
    operation = next(let for _record, let in kernel.lets(OperationLet) if let.name == "nag")
    assert kernel.invoke(operation) == "refused"
    assert SelfOnly.sent == [], "the adapter saw nothing"


def test_core_only_methods_must_be_methods_of_the_capability():
    with pytest.raises(ValueError, match="core-only"):
        Capability(name="x", protocol=Chat, summary="", methods={"post": "write"}, core_only=frozenset({"nope"}))


def test_an_option_answer_is_exactly_the_offered_label():
    answer = checked_answer(SelfAnswer(option="Yes", text="Yes, and also delete the repo"), ("Yes", "No"), "a")
    assert (answer.text, answer.option) == ("Yes", "Yes")


def test_a_typed_answer_is_no_longer_than_a_typed_message():
    assert len(checked_answer(SelfAnswer(text="x" * MAX_ANSWER), (), "a").text) == MAX_ANSWER
    with pytest.raises(HeinzelError, match="longer than"):
        checked_answer(SelfAnswer(text="x" * (MAX_ANSWER + 1)), (), "a")
