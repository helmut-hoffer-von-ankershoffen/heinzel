"""The conformance kit (ADR 0159): what it accepts, what it reports, and that its messages say what to fix."""

import contextlib
import threading
import warnings
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from heinzel import Message, ModelOffer, ModelUnavailableError, Module, NotSupportedError, Participant, PostedMessage
from heinzel.conformance import (
    UNKNOWN_MODEL_ID,
    ConformanceError,
    ConformanceWarning,
    Report,
    assert_conformant,
    check_adapter,
    check_module,
    versions,
)
from heinzel.kernel import Window

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
WINDOW = Window(since=NOW - timedelta(days=1), until=NOW)
HONEST = ("chat", "messages", "the gateway has no history tool")
WRONG = ("mail", "read")
NO_CHANNEL = "no such channel"
NO_CACHE = "cache missing"
DOWN = "proxy down"
SAMPLES = {"messages": {"channel": "#general", "window": WINDOW}}


def message() -> Message:
    return Message(channel="C1", id="1", author=Participant(name="Ada"), posted_at=NOW, text="hi")


class GoodChat:
    """A chat adapter that reads and writes as the protocol says."""

    def __init__(self) -> None:
        self.posted: list[str] = []

    def messages(self, channel: str, window: Window) -> list[Message]:
        return [message()]

    def post(self, channel: str, text: str, thread: str | None = None) -> PostedMessage:
        self.posted.append(text)
        return PostedMessage(channel=channel, id="2")


def failures(report: Report) -> list[str]:
    return [str(finding) for finding in report.failures]


def test_a_conformant_chat_adapter_passes_and_its_writes_are_never_called():
    adapter = GoodChat()
    report = check_adapter(adapter, "chat", modes={"read", "write"}, name="acme", samples=SAMPLES)
    assert report.findings == []
    assert report.checked == ["acme (chat)"]
    assert adapter.posted == []
    assert "post" not in vars(adapter), "the write method is restored after the read call"


def test_a_read_method_needing_arguments_is_skipped_without_samples_and_says_so():
    report = check_adapter(GoodChat(), "chat")
    assert report.findings == []
    assert report.skipped == ["chat.messages"]
    assert "pass sample arguments in `samples`" in str(report)


def test_a_wrong_signature_names_the_method_and_both_parameter_lists():
    class Odd(GoodChat):
        def messages(self, channel: str) -> list[Message]:  # type: ignore[override]
            return []

    assert failures(check_adapter(Odd(), "chat", name="odd")) == [
        "odd (chat) shape: messages takes (channel), the protocol Chat takes (channel, window)"
    ]


def test_a_missing_method_fails_unless_it_is_declared_unsupported():
    class NoPost:
        def messages(self, channel: str, window: Window) -> list[Message]:
            return []

    (missing,) = failures(check_adapter(NoPost(), "chat", modes={"read", "write"}, samples=SAMPLES))
    assert "post is missing (mode write)" in missing
    assert "list it in `unsupported`" in missing

    class ReadOnly(GoodChat):
        unsupported = frozenset({"post"})

    assert failures(check_adapter(ReadOnly(), "chat", samples=SAMPLES)) == []


def test_unsupported_must_name_methods_of_the_capability_in_a_set():
    class Typo(GoodChat):
        unsupported = frozenset({"psot"})

    class Text(GoodChat):
        unsupported = "post"

    assert failures(check_adapter(Typo(), "chat", samples=SAMPLES)) == [
        "Typo (chat) unsupported: names no method of chat: psot (a typo?)"
    ]
    assert "must be a set of method names" in failures(check_adapter(Text(), "chat", samples=SAMPLES))[0]


def test_a_mode_whose_every_method_is_unsupported_is_reported():
    class NoWrites(GoodChat):
        unsupported = frozenset({"post"})

    (finding,) = failures(check_adapter(NoWrites(), "chat", modes={"read", "write"}, samples=SAMPLES))
    assert "declares mode write but lists every write method as unsupported" in finding


def test_a_read_method_that_writes_fails_even_when_it_swallows_the_error():
    class Sneaky(GoodChat):
        def messages(self, channel: str, window: Window) -> list[Message]:
            with contextlib.suppress(RuntimeError):
                self.post(channel, "read receipt")
            return []

    (finding,) = failures(check_adapter(Sneaky(), "chat", modes={"read", "write"}, samples=SAMPLES))
    assert finding == "Sneaky (chat) read-only: read method messages called post, which writes"


def test_a_read_method_that_changes_the_writes_counter_fails():
    backend: list[str] = []

    class Counting(GoodChat):
        def messages(self, channel: str, window: Window) -> list[Message]:
            backend.append("marked read")
            return []

    (finding,) = failures(check_adapter(Counting(), "chat", samples=SAMPLES, writes=lambda: len(backend)))
    assert "your `writes` counter changed" in finding


def test_not_supported_is_accepted_when_it_names_its_own_capability_and_method():
    class Honest(GoodChat):
        def messages(self, channel: str, window: Window) -> list[Message]:
            raise NotSupportedError(*HONEST)

    class Confused(GoodChat):
        def messages(self, channel: str, window: Window) -> list[Message]:
            raise NotSupportedError(*WRONG)

    assert failures(check_adapter(Honest(), "chat", samples=SAMPLES)) == []
    (finding,) = failures(check_adapter(Confused(), "chat", samples=SAMPLES))
    assert "it must name its own capability and method: NotSupportedError('chat', 'messages', reason)" in finding


def test_a_failing_sample_call_and_a_wrong_return_type_are_reported():
    class Broken(GoodChat):
        def messages(self, channel: str, window: Window) -> list[Message]:
            raise ValueError(NO_CHANNEL)

    class Wrong(GoodChat):
        def messages(self, channel: str, window: Window) -> list[Message]:
            return [{"text": "hi"}]  # type: ignore[list-item]

    assert failures(check_adapter(Broken(), "chat", samples=SAMPLES)) == [
        "Broken (chat) call: messages(**{'channel': '#general', 'window': "
        + repr(WINDOW)
        + "}) failed: ValueError: no such channel"
    ]
    (finding,) = failures(check_adapter(Wrong(), "chat", samples=SAMPLES))
    assert finding.startswith("Wrong (chat) call: messages returned list, not list[Message]")


def test_a_read_call_that_hangs_fails_after_the_timeout():
    release = threading.Event()

    class Slow(GoodChat):
        def messages(self, channel: str, window: Window) -> list[Message]:
            release.wait(2)
            return []

    try:
        (finding,) = failures(check_adapter(Slow(), "chat", samples=SAMPLES, timeout=0.05))
    finally:
        release.set()
    assert finding == "Slow (chat) call: messages didn't answer within 0.05s"


def test_an_unknown_capability_is_refused():
    with pytest.raises(ValueError, match="Unknown capability 'mails'"):
        check_adapter(GoodChat(), "mails")


# --- ai-models


class Models:
    """An ai-models adapter on Pydantic AI's test model."""

    title = "Acme AI"

    def __init__(self, offers: list[object] | None = None) -> None:
        self.offers = offers if offers is not None else [ModelOffer(id="acme-large"), ModelOffer(id="acme-small")]

    def models(self) -> list[ModelOffer]:
        return self.offers  # type: ignore[return-value]

    def model(self, model_id: str) -> object:
        if model_id not in {o.id for o in self.offers if isinstance(o, ModelOffer) and o.available}:
            raise ModelUnavailableError(model_id)
        return TestModel(custom_output_text="OK")


def test_a_conformant_ai_models_adapter_passes_with_one_live_request():
    report = check_adapter(Models(), "ai-models", name="acme", live_request=True)
    assert report.findings == []
    assert "ai-models interface" in str(report)


def test_models_that_hang_fail_quoting_the_contract(monkeypatch: pytest.MonkeyPatch):
    release = threading.Event()

    class Slow(Models):
        def models(self) -> list[ModelOffer]:
            release.wait(2)
            return []

    monkeypatch.setattr("heinzel.kernel._ai_models.LIST_TIMEOUT", 0.05)
    try:
        (finding,) = failures(check_adapter(Slow(), "ai-models", name="acme"))
    finally:
        release.set()
    assert finding.startswith('acme (ai-models) models: models() took longer than 0.05s. The contract says: "')


def test_models_must_return_a_list_and_may_not_fail():
    class NotAList(Models):
        def models(self) -> list[ModelOffer]:
            return ModelOffer(id="one")  # type: ignore[return-value]

    class Failing(Models):
        def models(self) -> list[ModelOffer]:
            raise OSError(NO_CACHE)

    assert failures(check_adapter(NotAList(), "ai-models", name="a")) == [
        "a (ai-models) models: models() returned ModelOffer, not a list"
    ]
    assert failures(check_adapter(Failing(), "ai-models", name="a")) == [
        "a (ai-models) models: models() failed: OSError: cache missing"
    ]


def test_an_invalid_row_warns_as_core_skips_it_and_a_repeated_id_fails():
    rows: list[object] = [{"id": "has space"}, ModelOffer(id="acme-large"), {"id": "acme-large"}]
    report = check_adapter(Models(rows), "ai-models", name="a")
    assert str(report.warnings[0]).startswith("a (ai-models) models: row 0 isn't a valid ModelOffer (id:")
    assert "Core skips it" in str(report.warnings[0])
    assert failures(report) == ["a (ai-models) models: the id acme-large is offered twice; ids must be unique"]


def test_no_valid_offer_warns_that_model_was_not_checked():
    report = check_adapter(Models([]), "ai-models", name="a")
    assert report.failures == []
    assert [f.message for f in report.warnings] == ["models() offered no valid model, so model() wasn't checked"]


def test_model_must_return_a_pydantic_ai_model():
    class Plain(Models):
        def model(self, model_id: str) -> object:
            return "a model, honestly"

    (finding,) = failures(check_adapter(Plain(), "ai-models", name="a"))
    assert finding.startswith("a (ai-models) model: model('acme-large') returned str, not a pydantic_ai.models.Model")


def test_an_unknown_or_unavailable_id_should_be_refused_and_any_heinzel_error_will_do():
    class Lenient(Models):
        def model(self, model_id: str) -> object:
            return TestModel()

    offers = [ModelOffer(id="up"), ModelOffer(id="down", available=False)]
    report = check_adapter(Lenient(offers), "ai-models", name="a")
    assert report.failures == []
    assert [f.message.split(";")[0] for f in report.warnings] == [
        f"model({UNKNOWN_MODEL_ID!r}) for an unknown id returned TestModel",
        "model('down') for an unavailable id returned TestModel",
    ]
    assert "ModelUnavailableError" in report.warnings[0].message


def test_a_failing_live_request_fails():
    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ConnectionError(DOWN)

    class Down(Models):
        def model(self, model_id: str) -> object:
            if model_id != "acme-large":
                raise ModelUnavailableError(model_id)
            return FunctionModel(broken)

    (finding,) = failures(check_adapter(Down(), "ai-models", name="a", live_request=True))
    assert finding == "a (ai-models) request: one request through acme-large failed: ConnectionError: proxy down"

    class Up(Down):
        def model(self, model_id: str) -> object:
            super().model(model_id)
            return FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("OK")]))

    assert check_adapter(Up(), "ai-models", live_request=True).failures == []


def test_title_and_the_optional_test_method_have_their_shape():
    class Odd(Models):
        title = 42  # type: ignore[assignment]

        def test(self) -> None:
            return None

    assert failures(check_adapter(Odd(), "ai-models", name="a")) == [
        "a (ai-models) shape: `title` must be a plain str",
        "a (ai-models) shape: the optional `test` must be a method test(self, model_id: str)",
    ]


# --- modules, the assertion and the fixture


def chat_module(factory: object, hosts: object = ("chat.acme.example",)) -> Module:
    module = Module("acme-chat", "Acme chat.")
    module.provides("chat", modes={"read", "write"}, hosts=hosts, name="acme-chat")(factory)  # type: ignore[arg-type]
    return module


def test_check_module_builds_each_adapter_and_checks_its_hosts():
    report = check_module(chat_module(GoodChat, hosts=()), samples={"chat": SAMPLES})
    assert report.checked == ["acme-chat (chat)"]
    assert report.failures == []
    assert [f.check for f in report.warnings] == ["hosts"]


def test_check_module_reports_a_factory_it_cannot_build_with_the_hint_to_pass_a_kernel():
    def needs_settings(token: str) -> GoodChat:
        return GoodChat()

    (finding,) = failures(check_module(chat_module(needs_settings)))
    assert finding.startswith("acme-chat (chat) build: couldn't build it: TypeError:")
    assert finding.endswith("Pass kernel= (the hz_kernel fixture) to inject settings.")


def test_check_module_reports_a_module_without_adapters():
    assert failures(check_module(Module("empty", "Nothing."))) == ["empty build: module empty provides no adapter"]


def test_assert_conformant_raises_listing_every_failure_and_warns_the_rest():
    class Typo(GoodChat):
        unsupported = frozenset({"psot"})

    with pytest.raises(ConformanceError) as raised, pytest.warns(ConformanceWarning, match="declares no hosts"):
        assert_conformant(chat_module(Typo, hosts=()), samples={"chat": SAMPLES})
    text = str(raised.value)
    assert text.startswith("Conformance against heinzel ")
    assert "Failures:\n  - acme-chat (chat) unsupported: names no method of chat: psot" in text
    assert "Warnings:\n  - acme-chat (chat) hosts: declares no hosts" in text


def test_assert_conformant_warns_without_failing_and_needs_a_capability_for_an_adapter():
    with pytest.warns(ConformanceWarning, match="declares no hosts"):
        assert_conformant(chat_module(GoodChat, hosts=()))
    with pytest.raises(ValueError, match="name the capability"):
        assert_conformant(GoodChat())
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert assert_conformant(GoodChat(), "chat").checked == ["GoodChat (chat)"]


def test_the_fixture_builds_through_the_kernel(hz_conformance):
    report = hz_conformance(chat_module(GoodChat), samples={"chat": SAMPLES})
    assert report.findings == []
    assert hz_conformance(Models(), "ai-models").findings == []


def test_versions_name_core_the_interface_pydantic_ai_and_httpx2():
    assert set(versions()) == {"heinzel", "ai-models interface", "pydantic-ai", "httpx2"}
