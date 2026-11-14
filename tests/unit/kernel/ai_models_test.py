"""A plugin's own AI models, the AI access option the kernel derives for them, and declared gaps (ADR 0159)."""

import threading
from typing import ClassVar

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from heinzel import AccessStatus, AccessTest, Adapters, AiAccess, HeinzelError, Module, NotSupportedError
from heinzel.kernel import (
    CAPABILITIES,
    AiModelOptions,
    AiModelsSource,
    Document,
    Documents,
    ModelOffer,
    ModelUnavailableError,
    OperationLet,
)
from heinzel.kernel._ai_models import (
    EFFORT_BUDGETS,
    build_model,
    choose,
    effort_settings,
    list_offers,
)
from heinzel.kernel._capabilities import declared_unsupported

pytestmark = pytest.mark.unit


class Models:
    """A plugin's models: two answer, one didn't when last probed, one calls no tools."""

    title = "Acme models"
    built: ClassVar[list[str]] = []

    def models(self) -> list[ModelOffer]:
        return [
            ModelOffer(id="big", thinking="adaptive", efforts=["low", "high"]),
            ModelOffer(id="old", thinking="budget"),
            ModelOffer(id="gone", available=False),
            ModelOffer(id="mute", tools=False),
        ]

    def model(self, model_id: str) -> TestModel:
        self.built.append(model_id)
        return TestModel(custom_output_text="OK")


class Options(AiModelOptions):
    """Stands in for the `ai` module's option builder."""

    def option(self, name: str, source):
        class Option:
            def status(self) -> AccessStatus:
                found = source()
                return AccessStatus(title=name, summary=found.distribution, installed=True)

            def test(self) -> AccessTest:
                return AccessTest(ok=True)

        return Option()


def _source(adapter: object, name: str = "acme") -> AiModelsSource:
    return AiModelsSource(name=name, module=name, distribution="heinzel-acme", version="1.0.0", adapter=adapter)  # type: ignore[arg-type]


def _operation(kernel, name: str) -> OperationLet:
    return next(let for _record, let in kernel.lets(OperationLet) if let.name == name)


def _kernel(make_kernel, *, own_access: bool = False):
    acme = Module("acme", "Acme's models through its proxy.")
    acme.provides("ai-models", modes={"read"}, hosts=["ai.acme.invalid"])(Models)
    if own_access:

        class Own:
            def status(self) -> AccessStatus:
                return AccessStatus(title="Own", summary="", installed=True)

            def test(self) -> AccessTest:
                return AccessTest(ok=True)

        acme.provides("ai-access", modes={"read"})(Own)
    opts = Module("opts", "Builds AI access options.", inspects=["ai-access:read"])
    opts.service(AiModelOptions, Options)
    seen: dict[str, AccessStatus] = {}

    @opts.operation()
    def look(options: Adapters[AiAccess]) -> dict[str, str]:
        """List the options."""
        seen.update({name: options[name].status() for name in options})
        return {name: status.title for name, status in seen.items()}

    return make_kernel(acme=acme, opts=opts), seen


def test_ai_models_is_a_read_capability_chosen_as_an_access_option():
    spec = CAPABILITIES["ai-models"]
    assert spec.methods == {"models": "read", "model": "read"}
    assert not spec.automatic
    assert not spec.listed


def test_an_ai_models_adapter_becomes_an_access_option_of_its_name(make_kernel):
    kernel, seen = _kernel(make_kernel)
    assert kernel.invoke(_operation(kernel, "look")) == {"acme": "acme"}
    assert seen["acme"].summary == "heinzel-test"
    installed = kernel._capabilities.installed("ai-access")
    assert installed == ["acme"]
    adapter = kernel._capabilities.adapters["ai-access", "acme"][0]
    assert adapter.module == "acme"
    assert adapter.let.hosts == ("ai.acme.invalid",)
    source = kernel.ai_models("acme")
    assert source.distribution == "heinzel-test"
    assert isinstance(source.adapter, Models)
    assert kernel.ai_models("acme").adapter is source.adapter, "built once"
    assert kernel.offers_ai_models("acme")
    assert not kernel.offers_ai_models("claude-code")
    assert not kernel.offers_ai_models(None)


def test_a_plugin_that_provides_its_own_access_option_keeps_it(make_kernel):
    kernel, _seen = _kernel(make_kernel, own_access=True)
    adapters = kernel._capabilities.adapters["ai-access", "acme"]
    assert len(adapters) == 1
    assert adapters[0].let.factory.__name__ == "Own"


def test_an_unknown_ai_models_adapter_is_a_clear_error(make_kernel):
    kernel, _seen = _kernel(make_kernel)
    with pytest.raises(HeinzelError, match="No ai-models adapter called 'nope' is installed"):
        kernel.ai_models("nope")


def test_offers_need_only_an_id_and_refuse_unsafe_ids():
    assert ModelOffer(id="claude-sonnet-4-5").label == "claude-sonnet-4-5"
    assert ModelOffer(id="team/llama3.1:8b", title="Llama").label == "Llama"
    for bad in ("", "has space", "-leading", "quote'd", "x" * 201):
        with pytest.raises(ValidationError):
            ModelOffer(id=bad)


def test_choose_the_wanted_or_first_usable_model_and_name_others_when_unavailable():
    offers = Models().models()
    assert choose(offers, None).id == "big"
    assert choose(offers, "old").id == "old"
    with pytest.raises(ModelUnavailableError, match=r"unavailable \(the plugin doesn.t offer it now\)") as missing:
        choose(offers, "new")
    assert missing.value.others == ["big", "old"]
    assert "hz system config set ai.access.plugin_model big" in str(missing.value)
    with pytest.raises(ModelUnavailableError, match="didn't answer when the plugin last checked"):
        choose(offers, "gone")
    with pytest.raises(ModelUnavailableError, match="doesn't call tools"):
        choose(offers, "mute")
    with pytest.raises(ModelUnavailableError, match="No other model is offered now"):
        choose([ModelOffer(id="gone", available=False)], None)


def test_effort_maps_to_anthropic_native_settings_per_model():
    adaptive, budget, plain = (
        ModelOffer(id="a", thinking="adaptive"),
        ModelOffer(id="b", thinking="budget"),
        ModelOffer(id="c"),
    )
    assert effort_settings(adaptive, "high") == {"anthropic_thinking": {"type": "adaptive"}, "anthropic_effort": "high"}
    assert effort_settings(budget, "low") == {
        "anthropic_thinking": {"type": "enabled", "budget_tokens": EFFORT_BUDGETS["low"]}
    }
    assert effort_settings(plain, "max") == {}
    assert effort_settings(adaptive, None) == {}


def test_listing_keeps_the_last_good_list_when_the_plugin_fails_or_stalls():
    class Flaky:
        def __init__(self) -> None:
            self.mode = "ok"
            self.release = threading.Event()

        def models(self) -> list[object]:
            if self.mode == "fail":
                msg = "proxy down"
                raise RuntimeError(msg)
            if self.mode == "stall":
                self.release.wait(5)
            if self.mode == "junk":
                return "nope"  # type: ignore[return-value]
            return [{"id": "one"}, ModelOffer(id="two"), {"id": "one", "title": "again"}]

    flaky = Flaky()
    source = _source(flaky, name="flaky-test")
    offers, note = list_offers(source)
    assert [o.id for o in offers] == ["one", "two"]
    assert note == ""
    flaky.mode = "fail"
    offers, note = list_offers(source)
    assert [o.id for o in offers] == ["one", "two"]
    assert note == "its model list failed (RuntimeError: proxy down); showing the last list"
    flaky.mode = "stall"
    offers, note = list_offers(source, timeout=0.05)
    flaky.release.set()
    assert [o.id for o in offers] == ["one", "two"]
    assert note.startswith("its model list took longer than 0.05s")
    flaky.mode = "junk"
    offers, note = list_offers(_source(flaky, name="never-good"))
    assert offers == []
    assert note == "its model list failed (TypeError: models() returned str, not a list of ModelOffer)"


def test_build_model_needs_a_pydantic_ai_model_and_wraps_plugin_failures():
    offer = ModelOffer(id="big")
    assert isinstance(build_model(_source(Models()), offer), TestModel)

    class Wrong:
        def model(self, model_id: str) -> object:
            return object()

    with pytest.raises(HeinzelError, match="returned object for big, not a Pydantic AI model"):
        build_model(_source(Wrong()), offer)

    class Broken:
        def model(self, model_id: str) -> object:
            msg = "no route"
            raise ConnectionError(msg)

    with pytest.raises(HeinzelError, match="couldn't build the model big: ConnectionError: no route"):
        build_model(_source(Broken()), offer)

    class Refusing:
        def model(self, model_id: str) -> object:
            raise ModelUnavailableError(model_id, ["other"])

    with pytest.raises(ModelUnavailableError):
        build_model(_source(Refusing()), offer)


class Docs:
    """A documents connector that can read but not replace a section."""

    unsupported: ClassVar[frozenset[str]] = frozenset({"replace_section", "not-a-method"})

    def read(self, ref: str) -> Document:
        return Document(ref=ref, title="T", text="x")


def test_declared_gaps_are_methods_of_the_capability_only():
    spec = CAPABILITIES["documents"]
    assert declared_unsupported(Docs, spec) == {"replace_section"}
    assert declared_unsupported(Docs(), spec) == {"replace_section"}

    class Odd:
        unsupported = "replace_section"

    assert declared_unsupported(Odd, spec) == frozenset()
    assert declared_unsupported(object(), spec) == frozenset()


def test_a_declared_gap_needs_no_method_and_its_handle_says_not_supported(make_kernel):
    docs = Module("docs", "Reads documents, can't replace sections.")
    docs.provides("documents", modes={"read", "write"}, hosts=["docs.invalid"])(Docs)
    user = Module("user", "Reads and writes documents.")
    results: dict[str, object] = {}

    @user.operation(read_only=False, requires=["documents:read,write"])
    def edit(documents: Documents) -> str:
        """Edit a document."""
        results["read"] = documents.read("r").title  # type: ignore[attr-defined]
        documents.replace_section("r", "m", "t")  # type: ignore[attr-defined]
        return "done"

    kernel = make_kernel(docs=docs, user=user)
    assert "docs" not in kernel.errors, kernel.errors
    with pytest.raises(NotSupportedError, match=r"This documents connector can.t replace section\.") as refused:
        kernel.invoke(_operation(kernel, "edit"))
    assert (refused.value.capability, refused.value.method) == ("documents", "replace_section")
    assert results["read"] == "T"


def test_not_supported_says_which_method_and_why():
    error = NotSupportedError("mail", "draft", "only drafts made here")
    assert str(error) == "This mail connector can't draft: only drafts made here."
    assert isinstance(error, HeinzelError)
