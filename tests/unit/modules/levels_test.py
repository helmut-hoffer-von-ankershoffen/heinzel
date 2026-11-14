"""The intelligence level resolver: the provider's marked levels become a model and an effort (ADR 0158)."""

import pytest

from heinzel import HeinzelError, ModelOffer
from heinzel.modules.heinzel._levels import (
    AUTO_RUNS_AS,
    HeinzelIntelligence,
    declared_levels,
    effort_for,
    nearest,
    resolve,
)

pytestmark = pytest.mark.unit

OFFERS = [
    ModelOffer(id="house-1", thinking="adaptive"),
    ModelOffer(id="house-2", thinking="adaptive", efforts=["low", "medium"]),
    ModelOffer(id="house-3", thinking="budget", efforts=["low", "medium", "high", "max"]),
]
ALL = {"low": "house-2", "medium": "house-1", "high": "house-3"}


@pytest.mark.parametrize(
    ("level", "model", "effort", "ran", "rule"),
    [
        ("low", "house-2", "low", "low", "fixed"),
        ("medium", "house-1", "medium", "medium", "fixed"),
        ("high", "house-3", "high", "high", "fixed"),
        ("auto", "house-1", "medium", "medium", "auto-fixed-medium"),
    ],
)
def test_every_level_runs_on_the_model_the_provider_marks(level, model, effort, ran, rule):
    choice = resolve("acme", level, ALL, OFFERS)
    assert (choice.model, choice.effort) == (model, effort)
    assert (choice.record.asked, choice.record.ran, choice.record.rule, choice.record.source) == (
        level,
        ran,
        rule,
        "declared",
    )


def test_auto_asks_for_medium_by_a_fixed_rule():
    assert AUTO_RUNS_AS == "medium"
    assert resolve("acme", "auto", ALL, OFFERS) == resolve("acme", "auto", ALL, OFFERS)


@pytest.mark.parametrize(
    ("tier", "marked", "ran"),
    [
        ("medium", {"low", "high"}, "low"),
        ("low", {"medium", "high"}, "medium"),
        ("high", {"low", "medium"}, "medium"),
        ("high", {"low"}, "low"),
        ("low", {"high"}, "high"),
        ("medium", {"medium"}, "medium"),
    ],
)
def test_the_nearest_marked_level_the_lower_on_a_tie(tier, marked, ran):
    assert nearest(tier, marked) == ran


def test_an_unmarked_level_runs_on_the_nearest_marked_one_and_records_it():
    choice = resolve("acme", "high", {"low": "house-2", "medium": "house-1"}, OFFERS)
    assert (choice.model, choice.effort) == ("house-1", "medium")
    assert (choice.record.asked, choice.record.ran, choice.record.source) == ("high", "medium", "nearest")


@pytest.mark.parametrize("level", ["auto", "low", "medium", "high"])
def test_a_provider_that_marks_no_level_runs_its_own_default(level):
    for offers in (OFFERS, []):
        choice = resolve("acme", level, {}, offers)
        assert (choice.model, choice.effort, choice.record.source) == (None, None, "provider-default")
        assert choice.record.asked == level


def test_a_provider_without_a_list_runs_the_model_it_marks_and_no_effort():
    choice = resolve("claude-code", "high", {"low": "haiku", "high": "opus"}, [])
    assert (choice.model, choice.effort, choice.record.source) == ("opus", None, "declared")
    assert resolve("claude-code", "medium", {"low": "haiku", "high": "opus"}, []).model == "haiku"


def test_marked_models_that_cant_run_are_skipped_for_the_nearest():
    offers = [ModelOffer(id="down", available=False), ModelOffer(id="mute", tools=False), ModelOffer(id="ok")]
    choice = resolve("acme", "low", {"low": "down", "medium": "mute", "high": "ok"}, offers)
    assert (choice.model, choice.record.ran, choice.record.source) == ("ok", "high", "nearest")
    choice = resolve("acme", "low", {"low": "gone", "high": "ok"}, offers)
    assert choice.model == "ok"


def test_no_runnable_marked_model_is_refused_naming_the_action():
    with pytest.raises(HeinzelError, match="Couldn't pick a model for this Heinzel's level on the AI provider acme"):
        resolve("acme", "medium", {"medium": "down"}, [ModelOffer(id="down", available=False)])


@pytest.mark.parametrize("level", ["auto", "low", "medium", "high"])
def test_the_exact_model_wins_over_every_level(level):
    choice = resolve("acme", level, ALL, OFFERS, exact="house-3")
    assert (choice.model, choice.record.source) == ("house-3", "exact")
    assert choice.effort == {"auto": "medium", "low": "low", "medium": "medium", "high": "high"}[level]


@pytest.mark.parametrize(
    ("offers", "why"),
    [
        ([], "lists no models"),
        ([ModelOffer(id="x", available=False)], "didn't answer"),
        ([ModelOffer(id="x", tools=False)], "doesn't call tools"),
        ([ModelOffer(id="y")], "doesn't offer it now"),
    ],
)
def test_an_exact_model_that_cant_run_is_refused(offers, why):
    with pytest.raises(HeinzelError, match=rf"Couldn't run this Heinzel on the exact model x: .*{why}.*Heinzel's page"):
        resolve("acme", "medium", {}, offers, exact="x")


def test_a_level_never_widens_a_grant():
    granted = frozenset({"house-1"})
    choice = resolve("acme", "high", ALL, OFFERS, granted=granted)
    assert (choice.model, choice.record.ran, choice.record.source) == ("house-1", "medium", "nearest")
    with pytest.raises(HeinzelError, match="no model grant covers it, and a level never widens a grant"):
        resolve("acme", "high", ALL, OFFERS, exact="house-3", granted=granted)
    with pytest.raises(HeinzelError, match="none of the models it marks for a level is covered by the grant"):
        resolve("acme", "low", ALL, OFFERS, granted=frozenset({"elsewhere"}))
    with pytest.raises(HeinzelError, match="covered by the grant"):
        resolve("acme", "low", {}, OFFERS, granted=granted)


@pytest.mark.parametrize(
    ("efforts", "thinking", "tier", "effort"),
    [
        ([], None, "high", None),
        ([], "adaptive", "high", "high"),
        (["low", "max"], "budget", "medium", "low"),
        (["medium", "max"], "budget", "high", "medium"),
        (["xhigh"], "adaptive", "low", "xhigh"),
        (["low", "medium", "high"], "adaptive", "low", "low"),
    ],
)
def test_effort_is_the_nearest_the_offer_takes_the_lower_on_a_tie(efforts, thinking, tier, effort):
    assert effort_for(ModelOffer(id="m", efforts=efforts, thinking=thinking), tier) == effort


def test_a_plugin_marks_its_levels_on_its_offers():
    offers = [
        ModelOffer(id="a", level="low", available=False),
        ModelOffer(id="b", level="low"),
        ModelOffer(id="c", level="low"),
        ModelOffer(id="d", level="high", tools=False),
        ModelOffer(id="e"),
    ]
    assert declared_levels(offers) == {"low": "b"}


def test_the_stored_intelligence_defaults_to_auto_with_no_provider_or_model():
    assert HeinzelIntelligence().model_dump() == {"level": "auto", "provider": None, "model": None}
    with pytest.raises(ValueError, match="level"):
        HeinzelIntelligence.model_validate({"level": "turbo"})
    with pytest.raises(ValueError, match="provider"):
        HeinzelIntelligence.model_validate({"provider": "Not A Name"})
