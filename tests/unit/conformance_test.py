"""The conformance kit (ADR 0159): what it accepts, what it reports, and that its messages say what to fix."""

import contextlib
import socket
import threading
import warnings
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from heinzel import (
    Check,
    Grant,
    GrantStatus,
    Message,
    ModelOffer,
    ModelUnavailableError,
    Module,
    ModuleSettings,
    NotSupportedError,
    Participant,
    PostedMessage,
    SignInStatus,
    Status,
)
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
    class Misnamed(GoodChat):
        unsupported = frozenset({"publish"})

    class Text(GoodChat):
        unsupported = "post"

    assert failures(check_adapter(Misnamed(), "chat", samples=SAMPLES)) == [
        "Misnamed (chat) unsupported: names no method of chat: publish (a typo?)"
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

    monkeypatch.setattr("heinzel.kernel.ai._ai_models.LIST_TIMEOUT", 0.05)
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


def test_more_than_one_offer_per_level_fails():
    rows: list[object] = [
        ModelOffer(id="a", level="high"),
        ModelOffer(id="b", level="high"),
        ModelOffer(id="c", level="low"),
    ]
    assert failures(check_adapter(Models(rows), "ai-models", name="a")) == [
        "a (ai-models) models: 2 offers are marked level high (a, b); one at most"
    ]


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
    class Misnamed(GoodChat):
        unsupported = frozenset({"publish"})

    with pytest.raises(ConformanceError) as raised, pytest.warns(ConformanceWarning, match="declares no hosts"):
        assert_conformant(chat_module(Misnamed, hosts=()), samples={"chat": SAMPLES})
    text = str(raised.value)
    assert text.startswith("Conformance against heinzel ")
    assert "Failures:\n  - acme-chat (chat) unsupported: names no method of chat: publish" in text
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


class ChatSettings(ModuleSettings):
    api_url: str = "https://chat.acme.example/api"


def good_hosts(settings: ChatSettings) -> list[str]:
    return [settings.api_url]


def bad_hosts(settings: ChatSettings) -> list[str]:
    return ["not a host"] if settings else []


def broken_hosts(settings: ChatSettings) -> list[str]:
    raise KeyError(NO_CACHE if settings else "")


def test_a_hosts_function_is_evaluated_through_the_kernel(make_kernel):
    def hosts_of(function: object, *, with_kernel: bool = True) -> list[str]:
        module = chat_module(GoodChat, hosts=function)
        module.settings(ChatSettings)
        kernel = make_kernel(acme_chat=module) if with_kernel else None
        return [f.message for f in check_module(module, kernel=kernel).findings]

    assert hosts_of(good_hosts) == []
    assert hosts_of(bad_hosts) == ["its hosts function returned no valid host name or URL with default settings"]
    assert hosts_of(broken_hosts) == ["its hosts function failed with default settings: KeyError: 'cache missing'"]
    assert hosts_of(bad_hosts, with_kernel=False) == [], "without a kernel it isn't called"


# --- sign-in and health


READ = Grant(id="chat-read", title="Read your channels", explanation="Lets Heinzels read channels you pick.")
WRITE = Grant(id="chat-write", title="Post for you", explanation="Lets Heinzels post drafts you approve.")


class SignIn:
    """A sign-in provider reading local evidence only."""

    account = "ada@acme.example"
    grants = (READ, WRITE)
    live = False

    def status(self, *, live: bool) -> SignInStatus:
        states = [GrantStatus(grant=grant, state="granted", needed=True) for grant in self.grants]
        return SignInStatus(signed_in=True, account=self.account, grants=states, live=self.live)

    def sign_in(self, grants: frozenset[str], progress: object, stop: threading.Event) -> object:
        raise AssertionError  # never called by the kit

    def sign_out(self) -> str:
        raise AssertionError  # never called by the kit


def signed_in_module(provider: type, checks: object = None) -> Module:
    module = Module("acme-chat", "Acme chat.")
    module.sign_in(grants=[READ, WRITE])(provider)
    if checks is not None:
        module.health(checks)  # type: ignore[arg-type]
    return module


def sign_in_messages(provider: type) -> list[str]:
    return [f"{f.check}: {f.message}" for f in check_module(signed_in_module(provider)).findings]


def test_a_sign_in_status_offline_with_every_grant_passes():
    report = check_module(signed_in_module(SignIn))
    assert report.findings == []
    assert report.checked == ["acme-chat (sign-in)"]


def test_a_sign_in_status_must_name_every_declared_grant_and_no_other():
    class Partial(SignIn):
        grants = (READ, Grant(id="files-read", title="Read files", explanation="Lets it read files."))

    assert sign_in_messages(Partial) == [
        "sign-in: status() leaves out the declared grants chat-write",
        "sign-in: status() reports grants it doesn't declare: files-read",
    ]


def test_a_sign_in_account_must_not_look_like_a_token_and_offline_is_not_live():
    class Leaky(SignIn):
        account = "ghp_" + "a1B2" * 9
        live = True

    assert sign_in_messages(Leaky) == [
        "sign-in: status().account looks like a token or key; name the account",
        "sign-in: status(live=False) says live=True: it must not ask the vendor",
    ]


def test_a_sign_in_status_that_goes_online_fails_even_when_it_swallows_the_error():
    class Online(SignIn):
        def status(self, *, live: bool) -> SignInStatus:
            with contextlib.suppress(OSError):
                socket.create_connection(("login.acme.example", 443), timeout=1)
            return super().status(live=live)

    (message,) = sign_in_messages(Online)
    assert message == "offline: status(live=False) went online: look up 'login.acme.example'"
    assert socket.getaddrinfo("localhost", None), "the network is back after the check"


def test_a_sign_in_status_that_fails_hangs_or_returns_something_else_is_reported(monkeypatch: pytest.MonkeyPatch):
    release = threading.Event()

    class Failing(SignIn):
        def status(self, *, live: bool) -> SignInStatus:
            raise RuntimeError(NO_CACHE)

    class Hanging(SignIn):
        def status(self, *, live: bool) -> SignInStatus:
            release.wait(2)
            return super().status(live=live)

    class Plain(SignIn):
        def status(self, *, live: bool) -> SignInStatus:
            return {"signed_in": True}  # type: ignore[return-value]

    monkeypatch.setattr("heinzel.conformance.OFFLINE_TIMEOUT", 0.05)
    try:
        assert sign_in_messages(Failing) == ["sign-in: status(live=False) failed: RuntimeError: cache missing"]
        assert sign_in_messages(Hanging) == ["sign-in: status(live=False) took longer than 0.05s"]
    finally:
        release.set()
    assert sign_in_messages(Plain) == ["sign-in: status(live=False) returned dict"]


def ok_checks() -> list[Check]:
    return [Check(name="acme chat", status=Status.UP)]


def online_checks() -> list[Check]:
    with contextlib.suppress(OSError):
        socket.getaddrinfo("api.acme.example", 443)
    return []


def wrong_checks() -> list[Check]:
    return ["up"]  # type: ignore[list-item]


def broken_checks() -> list[Check]:
    raise RuntimeError(NO_CACHE)


def test_health_checks_run_offline_and_return_checks():
    def health(checks: object) -> list[str]:
        report = check_module(signed_in_module(SignIn, checks))
        return [f"{f.check}: {f.message}" for f in report.findings]

    assert health(ok_checks) == []
    assert health(online_checks) == ["offline: the health checks went online: look up 'api.acme.example'"]
    assert health(wrong_checks) == ["health: the health checks must return a list of Check, not list[str]"]
    assert health(broken_checks) == ["health: the health checks failed: RuntimeError: cache missing"]


def test_a_live_health_check_is_never_called():
    module = Module("acme-chat", "Acme chat.")
    module.health(live=True)(broken_checks)
    assert failures(check_module(module)) == ["acme-chat build: module acme-chat provides no adapter"]


def test_local_sockets_still_work_offline(tmp_path):
    from heinzel.conformance import offline

    a, b = socket.socketpair()
    with offline() as attempts, a, b:
        a.sendall(b"x")
        assert b.recv(1) == b"x"
    assert attempts == []
