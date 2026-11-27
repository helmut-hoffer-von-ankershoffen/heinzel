"""A plugin's own AI models, the AI access option the kernel derives for them, and declared gaps (ADR 0159)."""

import json
import threading
from typing import ClassVar

import pytest
from pydantic import ValidationError
from pydantic_ai.models.test import TestModel

from heinzel import AccessStatus, AccessTest, Adapters, AiAccess, HeinzelError, Module, NotSupportedError
from heinzel.kernel import (
    AI_MODELS_INTERFACE,
    CAPABILITIES,
    AiModelOptions,
    AiModelsSource,
    Document,
    Documents,
    ModelOffer,
    ModelUnavailableError,
    OperationLet,
)
from heinzel.kernel._kernel import Kernel, ModuleRecord, Origin
from heinzel.kernel.ai._ai_models import (
    EFFORT_BUDGETS,
    OUTPUT_TOKENS,
    PROMPT_CACHE,
    build_model,
    choose,
    effort_settings,
    list_offers,
    model_for_level,
)
from heinzel.kernel.ai._capabilities import declared_unsupported
from heinzel.testing import scaled

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


def test_offers_need_only_an_id_ignore_unknown_fields_and_refuse_unsafe_ids():
    offer = ModelOffer(id="claude-sonnet-4-5")
    assert (offer.label, offer.available, offer.tools, offer.thinking) == ("claude-sonnet-4-5", True, True, None)
    assert ModelOffer(id="team/llama3.1:8b", title="Llama").label == "Llama"
    assert ModelOffer.model_validate({"id": "m", "price": 3, "vision": True}).id == "m", "a newer plugin's fields"
    with pytest.raises(ValidationError):
        offer.title = "changed"  # type: ignore[misc]  # frozen
    for bad in ("", "has space", "-leading", "quote'd", "x" * 201):
        with pytest.raises(ValidationError):
            ModelOffer(id=bad)


def test_the_interface_has_a_version_plugins_can_check():
    major, minor = AI_MODELS_INTERFACE.split(".")
    assert (int(major), int(minor)) >= (1, 0)


def test_choose_the_wanted_or_first_usable_model_and_name_others_when_unavailable():
    offers = Models().models()
    assert choose(offers, None).id == "big"
    assert choose(offers, "old").id == "old"
    with pytest.raises(ModelUnavailableError, match=r"unavailable \(the plugin doesn.t offer it now\)") as missing:
        choose(offers, "new")
    assert missing.value.others == ["big", "old"]
    assert "hz system config set ai.access.plugin_model big" in str(missing.value)
    with pytest.raises(ModelUnavailableError, match="didn't answer when last checked"):
        choose(offers, "gone")
    with pytest.raises(ModelUnavailableError, match="doesn't call tools"):
        choose(offers, "mute")
    with pytest.raises(ModelUnavailableError, match="No other model is offered now"):
        choose([ModelOffer(id="gone", available=False)], None)
    assert ModelUnavailableError("x").others == []


def test_an_effort_the_model_doesnt_take_is_refused_before_the_turn():
    offers = Models().models()
    assert choose(offers, "big", "high").id == "big"
    with pytest.raises(HeinzelError, match="doesn't take the effort max; pick one of low, high, or none"):
        choose(offers, "big", "max")
    assert choose(offers, "old", "max").id == "old", "a budget model with no efforts listed takes any"


def test_effort_maps_to_anthropic_native_settings_and_leaves_room_for_thinking():
    adaptive, budget, plain = (
        ModelOffer(id="a", thinking="adaptive"),
        ModelOffer(id="b", thinking="budget"),
        ModelOffer(id="c"),
    )
    assert effort_settings(adaptive, "high") == {
        "max_tokens": OUTPUT_TOKENS,
        "anthropic_thinking": {"type": "adaptive"},
        "anthropic_effort": "high",
        **PROMPT_CACHE,
    }
    budgeted = effort_settings(budget, "max")
    assert budgeted["anthropic_thinking"] == {"type": "enabled", "budget_tokens": EFFORT_BUDGETS["max"]}
    assert budgeted["max_tokens"] > EFFORT_BUDGETS["max"], "Anthropic refuses budget_tokens >= max_tokens"
    assert budgeted["anthropic_cache_instructions"] is True
    assert effort_settings(plain, "max") == {"max_tokens": OUTPUT_TOKENS, **PROMPT_CACHE}
    assert effort_settings(adaptive, None) == {"max_tokens": OUTPUT_TOKENS, **PROMPT_CACHE}


class Flaky:
    """A plugin whose list works, fails, hangs or comes back with bad rows."""

    def __init__(self) -> None:
        self.mode = "ok"
        self.calls = 0
        self.release = threading.Event()

    def models(self) -> list[object]:
        self.calls += 1
        if self.mode == "fail":
            msg = "proxy down"
            raise RuntimeError(msg)
        if self.mode == "stall":
            self.release.wait(scaled(5))
        if self.mode == "junk":
            return "nope"  # type: ignore[return-value]
        if self.mode == "rows":
            return [{"id": "ok"}, {"id": "has space"}, {"id": "x", "thinking": "quantum"}, {"id": "fine"}]
        return [{"id": "one"}, ModelOffer(id="two"), {"id": "one", "title": "again"}]


def test_listing_keeps_the_last_good_list_when_the_plugin_fails_or_stalls(monkeypatch):
    monkeypatch.setattr("heinzel.kernel.ai._ai_models.LIST_TTL", 0.0)
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
    offers, note = list_offers(source, timeout=0.05)  # unscaled: the product limit, named in the note
    assert [o.id for o in offers] == ["one", "two"]
    assert note.startswith("its model list took longer than 0.05s")
    calls = flaky.calls
    offers, note = list_offers(source, timeout=0.05)  # unscaled: the product limit
    assert flaky.calls == calls, "one call in flight per plugin: a second caller waits for it"
    hung = [t for t in threading.enumerate() if t.name == "ai-models-flaky-test"]
    assert len(hung) == 1
    assert hung[0].daemon, "a hung plugin never blocks exit"
    flaky.release.set()
    hung[0].join(scaled(5))
    junk = Flaky()
    junk.mode = "junk"
    offers, note = list_offers(_source(junk, name="never-good"))
    assert offers == []
    assert note == "its model list failed (TypeError: models() returned str, not a list of ModelOffer)"


class Levels:
    """A plugin that marks levels; `offers` is set per test."""

    def __init__(self, offers: list[ModelOffer]) -> None:
        self.offers = offers

    def models(self) -> list[ModelOffer]:
        return self.offers


def test_model_for_level_picks_the_marked_usable_offer_else_the_first_usable(caplog):
    offers = [
        ModelOffer(id="mute", tools=False, level="high"),
        ModelOffer(id="small", level="low"),
        ModelOffer(id="gone", available=False, level="high"),
        ModelOffer(id="big", level="high"),
        ModelOffer(id="bigger", level="high"),
    ]
    source = _source(Levels(offers), name="levels-test")
    assert model_for_level(source, "low").id == "small"
    assert model_for_level(source, None).id == "small"
    assert model_for_level(source, "medium").id == "small", "none marked: the first usable"
    with caplog.at_level("WARNING"):
        assert model_for_level(source, "high").id == "big", "the unusable are skipped; several marked: the first"
    assert "ai.models.level_ambiguous" in caplog.text
    assert ModelOffer.model_validate({"id": "x", "level": "high"}).level == "high"
    with pytest.raises(ValidationError):
        ModelOffer.model_validate({"id": "x", "level": "max"})
    with pytest.raises(ModelUnavailableError):
        model_for_level(_source(Levels([ModelOffer(id="mute", tools=False)]), name="levels-none"), "low")


def test_the_last_good_ids_survive_a_restart_with_a_broken_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr("heinzel.kernel.ai._ai_models.LIST_TTL", 0.0)
    kept = tmp_path / "ai" / "plugin-models" / "acme.json"
    good = Levels([ModelOffer(id="big", title="Big", level="high"), ModelOffer(id="gone", available=False)])
    offers, _note = list_offers(AiModelsSource("acme", "acme", "heinzel-acme", "1.0.0", good, kept))  # type: ignore[arg-type]
    assert [o.id for o in offers] == ["big", "gone"]
    assert kept.stat().st_mode & 0o777 == 0o600
    assert json.loads(kept.read_text()) == [
        {"id": "big", "tools": True, "available": True, "level": "high"},
        {"id": "gone", "tools": True, "available": False, "level": None},
    ]
    stamp = kept.stat().st_mtime_ns
    list_offers(AiModelsSource("acme", "acme", "heinzel-acme", "1.0.0", good, kept))  # type: ignore[arg-type]
    assert kept.stat().st_mtime_ns == stamp, "an unchanged list isn't written again"
    broken = Flaky()
    broken.mode = "fail"
    restarted = AiModelsSource("acme", "acme", "heinzel-acme", "1.0.0", broken, kept)  # type: ignore[arg-type]
    offers, note = list_offers(restarted)
    assert [(o.id, o.level, o.available, o.title) for o in offers] == [
        ("big", "high", True, ""),
        ("gone", None, False, ""),
    ]
    assert note == "its model list failed (RuntimeError: proxy down); showing the list kept from before the restart"
    kept.write_text("{not json")
    offers, note = list_offers(AiModelsSource("acme", "acme", "heinzel-acme", "1.0.0", Flaky(), kept))  # type: ignore[arg-type]
    assert [o.id for o in offers] == ["one", "two"], "a working plugin overwrites a corrupt file"
    kept.write_text("{not json")
    other = Flaky()
    other.mode = "fail"
    offers, note = list_offers(AiModelsSource("acme", "acme", "heinzel-acme", "1.0.0", other, kept))  # type: ignore[arg-type]
    assert (offers, note) == ([], "its model list failed (RuntimeError: proxy down)"), "a corrupt file is ignored"
    blocked = tmp_path / "file"
    blocked.write_text("")
    offers, _note = list_offers(AiModelsSource("acme", "acme", "heinzel-acme", "1.0.0", Flaky(), blocked / "a.json"))  # type: ignore[arg-type]
    assert [o.id for o in offers] == ["one", "two"], "a failing write never fails the list"


def test_one_bad_row_is_skipped_not_the_whole_list():
    flaky = Flaky()
    flaky.mode = "rows"
    offers, note = list_offers(_source(flaky, name="rows-test"))
    assert [o.id for o in offers] == ["ok", "fine"]
    assert note.startswith("2 offers skipped: ")


def test_a_fresh_list_is_reused_for_a_few_seconds():
    flaky = Flaky()
    source = _source(flaky, name="ttl-test")
    list_offers(source)
    list_offers(source)
    assert flaky.calls == 1


def test_ai_models_named_like_another_plugins_access_option_are_skipped(settings):
    claimer = Module("claimer", "Names its models like Claude Code.")
    claimer.provides("ai-models", modes={"read"}, hosts=["x.invalid"], name="claude-code")(Models)

    class Code:
        def status(self) -> AccessStatus:
            return AccessStatus(title="Claude Code", summary="", installed=True)

        def test(self) -> AccessTest:
            return AccessTest(ok=True)

    runner = Module("runner", "The real Claude Code option.")
    runner.provides("ai-access", modes={"read"}, name="claude-code")(Code)
    records = [
        ModuleRecord("claimer", claimer, Origin("heinzel-claimer", "1.0.0", builtin=False)),
        ModuleRecord("runner", runner, Origin("heinzel", "0.0.0", builtin=True)),
    ]
    kernel = Kernel(settings, records)
    assert not kernel.offers_ai_models("claude-code")
    assert "claude-code" in kernel.errors["claimer"]


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
    # `search` is optional (ADR 0161): an adapter without it doesn't support it, declared or not.
    assert declared_unsupported(Docs, spec) == {"replace_section", "search"}
    assert declared_unsupported(Docs(), spec) == {"replace_section", "search"}

    class Odd:
        unsupported = "replace_section"

    assert declared_unsupported(Odd, spec) == {"search"}
    assert declared_unsupported(object(), spec) == {"search"}


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
