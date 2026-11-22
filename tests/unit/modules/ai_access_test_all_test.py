"""`hz ai access test --all`: one tiny request per model, bounded, recorded and kept for the picker (ADR 0159)."""

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from heinzel import AccessStatus, AccessTest, HeinzelError, NotSupportedError, Progress, UsageEntry, UsageLog
from heinzel.kernel import AiModelsSource, ModelOffer
from heinzel.kernel.ai._ai_models import list_offers
from heinzel.modules.ai.access._models import AccessSettings
from heinzel.modules.ai.access._plugin_models import PROBE_TOKENS, PluginModelsAccess
from heinzel.modules.ai.access._service import MAX_MODELS_TESTED, AccessService
from heinzel.testing import scaled

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
        RELEASE.wait(scaled(30))
        return AccessTest(ok=True, detail="late")


def option(tmp_path, adapter: Models, timeout: float = 2.0) -> tuple[PluginModelsAccess, AiModelsSource]:
    folder = tmp_path / "ai" / "plugin-models"
    found = AiModelsSource(
        "acme", "acme", "heinzel-acme", "1.0.0", adapter, folder / "acme.json", folder / "acme.unanswered.json"
    )  # type: ignore[arg-type]
    return PluginModelsAccess("acme", lambda: found, AccessSettings, model_timeout=timeout), found


def marked(found: AiModelsSource) -> dict[str, bool]:
    return {o.id: o.available for o in list_offers(found)[0]}


def test_each_model_answers_fails_or_times_out_and_is_kept_for_the_picker(tmp_path):
    adapter = Models()
    access, found = option(tmp_path, adapter)
    answered = access.test_model("ok")
    assert (answered.ok, answered.detail, answered.model, answered.skipped) == (True, "OK", "ok", False)
    assert answered.input_tokens
    assert answered.output_tokens
    failed = access.test_model("boom")
    assert (failed.ok, failed.model) == (False, "boom")
    assert failed.detail == "RuntimeError"
    started = time.monotonic()
    hung = option(tmp_path, adapter, timeout=0.3)[0].test_model(
        "slow"
    )  # unscaled: the product limit, named in the answer
    assert time.monotonic() - started < 2.5, "bounded by its timeout"
    assert (hung.ok, hung.model) == (False, "slow")
    assert hung.detail == "no answer within 0.3s"
    assert marked(found) == {"ok": True, "boom": False, "slow": False, "gone": False}
    assert access.status().unavailable_models == ["boom", "slow", "gone"], "the picker marks them"
    assert access.status().model == "ok", "the default is one that answered"


def test_a_model_the_plugin_marks_unavailable_or_no_longer_offers_is_skipped(tmp_path):
    adapter = Models()
    access, found = option(tmp_path, adapter)
    for model_id, why in [("gone", "the plugin marks it unavailable"), ("nope", "the plugin doesn't offer it now")]:
        result = access.test_model(model_id)
        assert (result.ok, result.skipped, result.detail) == (False, True, f"{why}; not tested")
    assert adapter.built == [], "no request, no model built"
    assert found.unanswered is not None
    assert not found.unanswered.exists(), "nothing learned, nothing kept"


def test_a_later_answer_clears_the_mark(tmp_path, monkeypatch):
    adapter = Models()
    access, found = option(tmp_path, adapter)
    access.test_model("boom")
    assert marked(found)["boom"] is False
    monkeypatch.setattr(adapter, "model", lambda model_id: FunctionModel(_answers, model_name=model_id))
    assert access.test_model("boom").ok
    assert marked(found)["boom"] is True


def test_a_plugins_own_test_that_blocks_its_thread_is_cut_off(tmp_path):
    RELEASE.clear()
    access, found = option(tmp_path, Stuck(), timeout=0.2)  # unscaled: the product limit, named in the answer
    try:
        started = time.monotonic()
        result = access.test_model("ok")
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
        self.asked: list[str] = []

    def status(self) -> AccessStatus:
        return AccessStatus(title="Acme", summary="", installed=True, models=list(self.answers))

    def test(self) -> AccessTest:
        raise AssertionError

    def test_model(self, model_id: str) -> AccessTest:
        self.asked.append(model_id)
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
    assert fake.asked == ["a", "b", "c", "d"], "one model at a time; the option bounds each itself"
    assert [(r.model, r.outcome) for r in report.models] == [
        ("a", "answered"),
        ("b", "unanswered"),
        ("c", "unanswered"),
        ("d", "skipped"),
    ]
    assert report.models[2].detail == "its test failed (RuntimeError)"
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
    assert fake.asked == ["a", "b"]


def _blocks(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    RELEASE.wait(scaled(30))
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
    access, found = option(tmp_path, Blocking(), timeout=0.2)  # unscaled: the product limit, named in the answer
    try:
        result = access.test_model("ok")
        assert (result.ok, result.detail, result.model) == (False, "no answer within 0.2s", "ok")
        assert marked(found)["ok"] is False
    finally:
        RELEASE.set()


def test_the_plugins_own_check_counts_and_a_model_it_cant_build_is_unanswered(tmp_path):
    access, found = option(tmp_path, Own())
    result = access.test_model("ok")
    assert (result.ok, result.detail, result.model, result.input_tokens) == (True, "pong", "ok", 4)
    access, found = option(tmp_path / "broken", Broken())
    result = access.test_model("ok")
    assert result.ok is False
    assert "returned object for ok, not a Pydantic AI model" in result.detail
    assert marked(found)["ok"] is False


def test_a_plugin_that_cant_be_loaded_skips_its_test(tmp_path):
    def gone() -> AiModelsSource:
        msg = "the plugin isn't installed"
        raise HeinzelError(msg)

    result = PluginModelsAccess("acme", gone, AccessSettings).test_model("ok")
    assert (result.ok, result.skipped, result.detail) == (False, True, "the plugin isn't installed")


def test_test_all_says_when_the_options_models_cant_be_listed(tmp_path):
    class Failing(Fake):
        def status(self) -> AccessStatus:
            msg = "proxy down"
            raise RuntimeError(msg)

    fake = Failing({})
    access, _ = service(tmp_path, {"acme": fake})
    with pytest.raises(HeinzelError, match=r"Couldn't list the models of acme: its status failed \(RuntimeError\)$"):
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
    result = access.test_model("ok")
    assert (result.ok, result.detail, result.model) == (False, "TypeError", "ok")
    assert marked(found)["ok"] is False


class OwnSkips(Models):
    """Its own check says it didn't ask the model (the review's probe)."""

    def test(self, model_id: str) -> dict[str, object]:
        return {"ok": False, "detail": "x" * 10_000, "skipped": True}


class OwnExits(Models):
    """Its own check raises what isn't an `Exception` (the review's probe)."""

    def test(self, model_id: str) -> AccessTest:
        raise SystemExit(3)


def test_a_plugins_own_skip_marks_nothing_and_is_reported_skipped(tmp_path):
    access, found = option(tmp_path, OwnSkips())
    result = access.test_model("ok")
    assert (result.ok, result.skipped, len(result.detail), result.model) == (False, True, 300, "ok")
    assert marked(found)["ok"] is True, "nothing was learned, so nothing is marked"
    assert found.unanswered is not None
    assert not found.unanswered.exists()
    service_, usage = service(tmp_path, {"acme": access})
    report = service_.test_all("acme", {"acme": access}, Progress())  # type: ignore[arg-type]
    assert [(r.model, r.outcome) for r in report.models if r.model == "ok"] == [("ok", "skipped")]
    assert [e.model for e in usage.entries if e.model == "ok"] == [], "no request, no usage"


def test_a_plugins_own_check_that_exits_is_a_failed_check_with_its_cause(tmp_path, capsys):
    access, found = option(tmp_path, OwnExits())
    result = access.test_model("ok")
    assert (result.ok, result.detail, result.model) == (False, "SystemExit", "ok")
    assert marked(found)["ok"] is False
    assert "Traceback" not in capsys.readouterr().err


def test_marks_of_models_the_plugin_dropped_are_pruned_and_never_crowd_out_new_ones(tmp_path):
    from heinzel.kernel.ai._ai_models import mark_answered

    access, found = option(tmp_path, Models())
    for i in range(600):
        mark_answered(found, f"a{i:04d}", answered=False)
    access.test_model("boom")
    assert found.unanswered is not None
    assert json.loads(found.unanswered.read_text()) == ["boom"], "only ids the plugin offers now are kept"


def test_cut_off_requests_are_bounded_across_runs(tmp_path, monkeypatch):
    from heinzel.modules.ai.access import _plugin_models

    adapter = Models()
    access, _found = option(tmp_path, adapter)
    monkeypatch.setattr(_plugin_models, "_lingering", lambda: _plugin_models.MAX_LINGERING)
    result = access.test_model("ok")
    assert (result.skipped, result.detail) == (
        True,
        "24 earlier requests still haven't returned; try again later; not tested",
    )
    assert adapter.built == []


def test_a_second_run_is_refused_while_one_is_going_on(tmp_path):
    started, release = threading.Event(), threading.Event()

    class Slow(Fake):
        def test_model(self, model_id: str) -> AccessTest:
            started.set()
            release.wait(scaled(10))
            return AccessTest(ok=True, detail="OK")

    slow = Slow({"a": AccessTest(ok=True)})
    first, _ = service(tmp_path, {"acme": slow})
    second, _usage = service(tmp_path, {"acme": slow})
    reports: list[object] = []
    worker = threading.Thread(target=lambda: reports.append(first.test_all("acme", {"acme": slow}, Progress())))
    worker.start()
    try:
        assert started.wait(scaled(5))
        with pytest.raises(HeinzelError, match="A check of every model is already running"):
            second.test_all("acme", {"acme": slow}, Progress())  # type: ignore[arg-type]
    finally:
        release.set()
        worker.join(scaled(10))
    assert len(reports) == 1
    assert second.test_all("acme", {"acme": slow}, Progress()).ok, "free again once the first ended"  # type: ignore[arg-type]


def test_the_single_check_of_the_chosen_model_clears_its_mark_but_never_sets_one(tmp_path, monkeypatch):
    from heinzel.kernel.ai._ai_models import mark_answered

    adapter = Models()
    found = option(tmp_path, adapter)[1]
    mark_answered(found, "ok", answered=False)
    access = PluginModelsAccess("acme", lambda: found, lambda: AccessSettings(plugin_model="ok"))
    monkeypatch.setattr(adapter, "model", lambda model_id: FunctionModel(_plain, model_name=model_id))
    assert access.test().ok
    assert marked(found)["ok"] is True
    failing = PluginModelsAccess("acme", lambda: found, lambda: AccessSettings(plugin_model="boom"))
    monkeypatch.setattr(adapter, "model", lambda model_id: FunctionModel(_raises, model_name=model_id))
    assert not failing.test().ok
    assert marked(found)["boom"] is True, "a single check marks nothing"


async def _plain(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("OK")])


SECRET = "Incorrect API key provided: sk-proj-SECRETabcd"


async def _thinks(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """A reasoning model that spent the probe's few tokens thinking: a response, but no text (the review's probe)."""
    return ModelResponse(parts=[], finish_reason="length")


async def _refuses_with_a_secret(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    from pydantic_ai.exceptions import ModelHTTPError

    raise ModelHTTPError(401, "acme", {"error": SECRET})


class Thinks(Models):
    def model(self, model_id: str) -> FunctionModel:
        return FunctionModel(_thinks, model_name=model_id)


class Leaks(Models):
    """Its provider's errors carry a secret; so does its own failing check."""

    def model(self, model_id: str) -> FunctionModel:
        return FunctionModel(_refuses_with_a_secret, model_name=model_id)


class LeaksOwn(Models):
    def test(self, model_id: str) -> AccessTest:
        raise RuntimeError(SECRET)


def test_a_model_that_answers_without_text_counts_as_answered_with_its_tokens(tmp_path):
    access, found = option(tmp_path, Thinks())
    result = access.test_model("ok")
    assert result.ok, result.detail
    assert result.detail.startswith("it answered, without usable text (")
    assert result.input_tokens is not None
    assert result.input_tokens > 0
    assert marked(found)["ok"] is True, "a reasoning model is never marked for thinking"


def test_a_providers_error_text_never_reaches_the_detail_or_the_report(tmp_path):
    access, found = option(tmp_path, Leaks())
    result = access.test_model("ok")
    assert (result.ok, result.detail) == (False, "ModelHTTPError 401")
    assert marked(found)["ok"] is False
    own = option(tmp_path / "own", LeaksOwn())[0].test_model("ok")
    assert own.detail == "RuntimeError"
    single = PluginModelsAccess("acme", lambda: found, lambda: AccessSettings(plugin_model="ok")).test()
    assert single.detail == "ModelHTTPError 401"
    fake = Fake({"a": RuntimeError(SECRET)})
    report_service, _ = service(tmp_path, {"acme": fake})
    report = report_service.test_all("acme", {"acme": fake}, Progress())  # type: ignore[arg-type]
    assert "sk-proj" not in report.model_dump_json()
    for leaked in (result, own, single):
        assert "sk-proj" not in leaked.model_dump_json()


def test_a_new_mark_is_kept_however_many_stale_ids_the_file_holds(tmp_path):
    from heinzel.kernel.ai._ai_models import MAX_OFFERS, mark_answered

    access, found = option(tmp_path, Leaks())
    for i in range(MAX_OFFERS):
        mark_answered(found, f"a-stale-{i:04d}", answered=False)
    access.test_model("slow")
    assert marked(found)["slow"] is False
    assert found.unanswered is not None
    assert json.loads(found.unanswered.read_text()) == ["slow"]
