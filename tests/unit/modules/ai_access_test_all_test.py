"""`hz ai access test --all`: one tiny request per model, bounded, recorded and kept for the picker (ADR 0159)."""

import asyncio
import threading
import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import AccessStatus, AccessTest, HeinzelError, NotSupportedError, Progress, UsageEntry, UsageLog
from heinzel.kernel import AiModelsSource, ModelOffer
from heinzel.kernel._ai_models import list_offers
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._plugin_models import PROBE_TOKENS, PluginModelsAccess
from heinzel.modules.ai.access._service import MAX_MODELS_TESTED, AccessService

pytestmark = pytest.mark.unit

RELEASE = threading.Event()
"""Lets a hung plugin thread go once its test is over."""


async def _answers(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    assert info.model_settings is not None
    assert info.model_settings.get("max_tokens") == PROBE_TOKENS, "a tiny request"
    return ModelResponse(parts=[TextPart("OK")])


async def _raises(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    msg = "proxy said no"
    raise RuntimeError(msg)


async def _hangs(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    await asyncio.sleep(60)
    raise AssertionError


class Models:
    """One answers, one raises, one hangs, one the plugin marks unavailable."""

    def __init__(self) -> None:
        self.built: list[str] = []

    def models(self) -> list[ModelOffer]:
        return [
            ModelOffer(id="ok"),
            ModelOffer(id="boom"),
            ModelOffer(id="slow"),
            ModelOffer(id="gone", available=False),
        ]

    def model(self, model_id: str) -> FunctionModel:
        self.built.append(model_id)
        function = {"ok": _answers, "boom": _raises, "slow": _hangs}[model_id]
        return FunctionModel(function, model_name=model_id)


class Stuck(Models):
    """Its own `test` blocks the thread it runs on, as a careless plugin's synchronous call does."""

    def test(self, model_id: str) -> AccessTest:
        RELEASE.wait(30)
        return AccessTest(ok=True, detail="late")


def option(tmp_path, adapter: Models) -> tuple[PluginModelsAccess, AiModelsSource]:
    folder = tmp_path / "ai" / "plugin-models"
    found = AiModelsSource(
        "acme", "acme", "heinzel-acme", "1.0.0", adapter, folder / "acme.json", folder / "acme.unanswered.json"
    )  # type: ignore[arg-type]
    return PluginModelsAccess("acme", lambda: found, AccessSettings), found


def marked(found: AiModelsSource) -> dict[str, bool]:
    return {o.id: o.available for o in list_offers(found)[0]}


def test_each_model_answers_fails_or_times_out_and_is_kept_for_the_picker(tmp_path):
    access, found = option(tmp_path, Models())
    answered = access.test_model("ok", 2.0)
    assert (answered.ok, answered.detail, answered.model, answered.skipped) == (True, "OK", "ok", False)
    assert answered.input_tokens
    assert answered.output_tokens
    failed = access.test_model("boom", 2.0)
    assert (failed.ok, failed.model) == (False, "boom")
    assert failed.detail == "RuntimeError: proxy said no"
    started = time.monotonic()
    hung = access.test_model("slow", 0.3)
    assert time.monotonic() - started < 2.5, "bounded by its timeout"
    assert (hung.ok, hung.model) == (False, "slow")
    assert "TimeoutError" in hung.detail or "no answer within" in hung.detail
    assert marked(found) == {"ok": True, "boom": False, "slow": False, "gone": False}
    assert access.status().unavailable_models == ["boom", "slow", "gone"], "the picker marks them"
    assert access.status().model == "ok", "the default is one that answered"


def test_a_model_the_plugin_marks_unavailable_or_no_longer_offers_is_skipped(tmp_path):
    adapter = Models()
    access, found = option(tmp_path, adapter)
    for model_id, why in [("gone", "the plugin marks it unavailable"), ("nope", "the plugin doesn't offer it now")]:
        result = access.test_model(model_id, 1.0)
        assert (result.ok, result.skipped, result.detail) == (False, True, f"{why}; not tested")
    assert adapter.built == [], "no request, no model built"
    assert found.unanswered is not None
    assert not found.unanswered.exists(), "nothing learned, nothing kept"


def test_a_later_answer_clears_the_mark(tmp_path, monkeypatch):
    adapter = Models()
    access, found = option(tmp_path, adapter)
    access.test_model("boom", 1.0)
    assert marked(found)["boom"] is False
    monkeypatch.setattr(adapter, "model", lambda model_id: FunctionModel(_answers, model_name=model_id))
    assert access.test_model("boom", 1.0).ok
    assert marked(found)["boom"] is True


def test_a_plugins_own_test_that_blocks_its_thread_is_cut_off(tmp_path):
    RELEASE.clear()
    access, found = option(tmp_path, Stuck())
    try:
        started = time.monotonic()
        result = access.test_model("ok", 0.2)
        assert time.monotonic() - started < 2.5
        assert (result.ok, result.detail) == (False, "no answer within 0.2s")
        assert marked(found)["ok"] is False
    finally:
        RELEASE.set()


class Recorded(UsageLog):
    def __init__(self) -> None:
        self.entries: list[UsageEntry] = []

    def record(self, entry: UsageEntry) -> None:
        self.entries.append(entry)


class Steps(Progress):
    def __init__(self) -> None:
        self.steps: list[str] = []

    @contextmanager
    def task(self, description: str, total: float | None = None):
        self.steps.append(description)
        yield lambda _amount: None


class Fake:
    """An option with `test_model`: each model's answer is given."""

    def __init__(self, answers: dict[str, AccessTest | BaseException]) -> None:
        self.answers = answers
        self.asked: list[tuple[str, float]] = []

    def status(self) -> AccessStatus:
        return AccessStatus(title="Acme", summary="", installed=True, models=list(self.answers))

    def test(self) -> AccessTest:
        raise AssertionError

    def test_model(self, model_id: str, timeout: float) -> AccessTest:
        self.asked.append((model_id, timeout))
        answer = self.answers[model_id]
        if isinstance(answer, BaseException):
            raise answer
        return answer


def service(tmp_path, options: dict[str, object]) -> tuple[AccessService, Recorded]:
    usage = Recorded()
    settings = Mock(state_dir=tmp_path)
    return AccessService(options, Mock(), Mock(), settings, usage), usage  # type: ignore[arg-type]


def test_test_all_asks_each_model_in_turn_records_tokens_and_keeps_a_summary(tmp_path):
    fake = Fake(
        {
            "a": AccessTest(ok=True, detail="OK", model="a", input_tokens=10, output_tokens=1, duration_seconds=0.5),
            "b": AccessTest(ok=False, detail="ModelHTTPError: 404", model="b", duration_seconds=0.25),
            "c": RuntimeError("plugin broke"),
            "d": AccessTest(ok=False, detail="the plugin marks it unavailable; not tested", skipped=True),
        }
    )
    access, usage = service(tmp_path, {"acme": fake})
    steps = Steps()
    report = access.test_all("acme", {"acme": fake}, steps)  # type: ignore[arg-type]
    assert steps.steps == ["Testing a (1/4)", "Testing b (2/4)", "Testing c (3/4)", "Testing d (4/4)"]
    assert [t for _, t in fake.asked] == [15.0] * 4
    assert [(r.model, r.outcome) for r in report.models] == [
        ("a", "answered"),
        ("b", "unanswered"),
        ("c", "unanswered"),
        ("d", "skipped"),
    ]
    assert report.models[2].detail == "its test failed (RuntimeError: plugin broke)"
    assert (report.ok, report.input_tokens, report.output_tokens, report.duration_seconds) == (True, 10, 1, 0.75)
    assert report.detail == "1 of 3 models answered; 2 marked unavailable until a test answers; 1 skipped"
    assert [(e.cause, e.access, e.model, e.tokens, e.cost_usd) for e in usage.entries] == [
        ("test", "acme", "a", 11, None),
        ("test", "acme", "b", 0, None),
        ("test", "acme", "c", 0, None),
    ], "one record per request sent, tokens only"
    assert access._tests.load()["acme"].detail == report.detail, "kept as the option's last test"


def test_test_all_is_capped_per_run(tmp_path):
    answers: dict[str, AccessTest | BaseException] = {
        f"m{i}": AccessTest(ok=True, detail="OK") for i in range(MAX_MODELS_TESTED + 3)
    }
    fake = Fake(answers)
    access, _usage = service(tmp_path, {"acme": fake})
    report = access.test_all("acme", {"acme": fake}, Progress())  # type: ignore[arg-type]
    assert len(fake.asked) == MAX_MODELS_TESTED
    assert report.untested == [f"m{i}" for i in range(MAX_MODELS_TESTED, MAX_MODELS_TESTED + 3)]
    assert report.detail.endswith(f"3 not tested: at most {MAX_MODELS_TESTED} per run")


def test_test_all_says_when_an_option_cant_test_its_models_or_names_none(tmp_path):
    fake = Fake({"a": NotSupportedError("ai-access", "test_model")})
    access, usage = service(tmp_path, {"acme": fake})
    with pytest.raises(HeinzelError, match="Acme can't test its models one by one"):
        access.test_all("acme", {"acme": fake}, Progress())  # type: ignore[arg-type]
    assert usage.entries == []
    empty = Fake({})
    access, _ = service(tmp_path, {"acme": empty})
    with pytest.raises(HeinzelError, match="Acme names no models to test"):
        access.test_all("acme", {"acme": empty}, Progress())  # type: ignore[arg-type]


def test_test_all_stops_on_ctrl_c_with_what_it_learned_recorded(tmp_path):
    fake = Fake(
        {"a": AccessTest(ok=True, detail="OK", input_tokens=3), "b": KeyboardInterrupt(), "c": AccessTest(ok=True)}
    )
    access, usage = service(tmp_path, {"acme": fake})
    with pytest.raises(KeyboardInterrupt):
        access.test_all("acme", {"acme": fake}, Progress())  # type: ignore[arg-type]
    assert [e.model for e in usage.entries] == ["a"]
    assert [m for m, _ in fake.asked] == ["a", "b"]


def _blocks(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    RELEASE.wait(30)
    return ModelResponse(parts=[TextPart("late")])


class Blocking(Models):
    """Its model blocks the thread its request runs on, so even the event loop's timeout can't end it."""

    def model(self, model_id: str) -> FunctionModel:
        return FunctionModel(_blocks, model_name=model_id)


class Own(Models):
    """Its own live check answers."""

    def test(self, model_id: str) -> AccessTest:
        return AccessTest(ok=True, detail="pong", input_tokens=4, output_tokens=1)


class Broken(Models):
    """Builds something that isn't a Pydantic AI model."""

    def model(self, model_id: str) -> object:  # type: ignore[override]
        return object()


def test_a_model_that_blocks_its_request_thread_is_cut_off(tmp_path):
    RELEASE.clear()
    access, found = option(tmp_path, Blocking())
    try:
        result = access.test_model("ok", 0.2)
        assert (result.ok, result.detail, result.model) == (False, "no answer within 0.2s", "ok")
        assert marked(found)["ok"] is False
    finally:
        RELEASE.set()


def test_the_plugins_own_check_counts_and_a_model_it_cant_build_is_unanswered(tmp_path):
    access, found = option(tmp_path, Own())
    result = access.test_model("ok", 1.0)
    assert (result.ok, result.detail, result.model, result.input_tokens) == (True, "pong", "ok", 4)
    access, found = option(tmp_path / "broken", Broken())
    result = access.test_model("ok", 1.0)
    assert result.ok is False
    assert "returned object for ok, not a Pydantic AI model" in result.detail
    assert marked(found)["ok"] is False


def test_a_plugin_that_cant_be_loaded_skips_its_test(tmp_path):
    def gone() -> AiModelsSource:
        msg = "the plugin isn't installed"
        raise HeinzelError(msg)

    result = PluginModelsAccess("acme", gone, AccessSettings).test_model("ok", 1.0)
    assert (result.ok, result.skipped, result.detail) == (False, True, "the plugin isn't installed")


def test_test_all_says_when_the_options_models_cant_be_listed(tmp_path):
    class Failing(Fake):
        def status(self) -> AccessStatus:
            msg = "proxy down"
            raise RuntimeError(msg)

    fake = Failing({})
    access, _ = service(tmp_path, {"acme": fake})
    with pytest.raises(HeinzelError, match=r"Couldn't list the models of acme: its status failed \(RuntimeError"):
        access.test_all("acme", {"acme": fake}, Progress())  # type: ignore[arg-type]


def test_the_terminal_shows_each_model_and_the_tokens_spent(tmp_path):
    from rich.console import Console

    from heinzel.modules.ai.access import render_test

    fake = Fake(
        {
            "a": AccessTest(ok=True, detail="OK", input_tokens=10, output_tokens=1, duration_seconds=0.5),
            "b": AccessTest(ok=False, detail="no answer within 15s", duration_seconds=15),
            "c": AccessTest(ok=False, detail="the plugin marks it unavailable; not tested", skipped=True),
        }
    )
    access, _ = service(tmp_path, {"acme": fake})
    report = access.test_all("acme", {"acme": fake}, Progress())  # type: ignore[arg-type]
    console = Console(width=200, record=True)
    console.print(render_test(report))
    text = console.export_text()
    rows = [line.split() for line in text.splitlines()]
    assert ["a", "answered", "0.5s", "10", "in,", "1", "out", "OK"] in rows
    assert ["b", "unanswered", "15.0s", "-", "no", "answer", "within", "15s"] in rows
    assert ["c", "skipped", "0.0s", "-", "the", "plugin", "marks", "it", "unavailable;", "not", "tested"] in rows
    assert text.splitlines()[-1] == (
        "acme: 1 of 2 models answered; 1 marked unavailable until a test answers; 1 skipped; tokens: 10 in, 1 out"
    )


def test_a_library_failure_before_the_request_is_a_failed_check(tmp_path, monkeypatch):
    import pydantic_ai

    def refuse(*args: object, **kwargs: object) -> object:
        msg = "unsupported model"
        raise TypeError(msg)

    monkeypatch.setattr(pydantic_ai, "Agent", refuse)
    access, found = option(tmp_path, Models())
    result = access.test_model("ok", 1.0)
    assert (result.ok, result.detail, result.model) == (False, "TypeError: unsupported model", "ok")
    assert marked(found)["ok"] is False
